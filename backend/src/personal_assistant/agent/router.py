import re
from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from personal_assistant.agent.state import AgentState
from personal_assistant.skills import SkillRegistry

if TYPE_CHECKING:
    from personal_assistant.memory.long_term import LongTermMemoryStore

_BASE_PROMPT = (
    "You are a personal assistant running as a single ReAct agent. "
    "Basic shell and file tools are always available. "
    "Additional capabilities come from the selected skills below. "
    "Use skill tools only when a selected skill makes them available. "
    "Every tool call is approval-gated by the harness."
)


def build_skill_router(
    registry: SkillRegistry,
    long_term_memory: "LongTermMemoryStore | None" = None,
    cache=None,
    memory_cache_ttl_seconds: int = 60,
):
    async def route_skills(state: AgentState) -> AgentState:
        user_text = "\n".join(
            getattr(message, "content", "")
            for message in state.get("messages", [])
            if getattr(message, "type", "") == "human"
        )[-4000:]

        # Progressive loading: match skills using lightweight meta only
        # (name + description + triggers). Skills that don't match are NOT
        # force-loaded — the agent still sees their meta overview below and
        # can ask a clarifying question.
        selected = _keyword_route(registry, user_text)

        # Load full content (instructions + tools) only for matched skills
        for name in selected:
            registry.load_skill(name)

        memory_text = None
        if long_term_memory is not None and cache is not None:
            memory_text = await long_term_memory.read_all_cached(
                cache,
                ttl_seconds=memory_cache_ttl_seconds,
            )
        system = build_system_prompt(
            registry,
            selected,
            long_term_memory=long_term_memory,
            memory_text=memory_text,
        )
        return {
            "messages": [system],
            "selected_skills": selected,
            "allowed_tools": list(registry.tool_map_for_skills(selected)),
        }

    return route_skills


def build_system_prompt(
    registry: SkillRegistry,
    selected: list[str],
    long_term_memory: "LongTermMemoryStore | None" = None,
    memory_text: str | None = None,
) -> SystemMessage:
    """Build a progressive system prompt with meta overview + detailed selected skills.

    When *long_term_memory* is provided, the durable memory content (USER.md,
    SYSTEM.md, MEMORY.md index, and individual memory files) is loaded from
    disk and prepended to the system prompt so the agent retains context across
    conversations.
    """
    sections: list[str] = []

    # Long-term memory — prepended so it's the first thing the agent sees
    if long_term_memory is not None:
        if memory_text is None:
            memory_text = long_term_memory.read_all()
        if memory_text:
            sections.append(memory_text)

    sections.append(_BASE_PROMPT)

    # Meta overview — always present, lightweight
    if registry.skills:
        meta_lines = [
            f"- **{skill.name}**: {skill.description}"
            for skill in registry.skills.values()
        ]
        sections.append("## Available Skills\n" + "\n".join(meta_lines))

    # Detailed instructions — only for selected skills
    detail_parts = []
    for name in selected:
        skill = registry.skills.get(name)
        if skill and skill.loaded and skill.instructions:
            detail_parts.append(f"## Skill: {skill.name}\n{skill.instructions}")
    if detail_parts:
        sections.append("\n\n".join(detail_parts))

    return SystemMessage(content="\n\n".join(sections))


def _keyword_route(registry: SkillRegistry, user_text: str) -> list[str]:
    """Match skills using lightweight meta only — not full instructions.

    A skill matches if any of its explicit ``triggers`` appears in the user text
    (substring match, case-insensitive for ASCII), or — for skills without
    triggers — if a name/description token appears in the text.
    """
    normalized = user_text.lower()
    selected: list[str] = []
    for skill in registry.skills.values():
        if skill.triggers:
            if any(_trigger_match(t, normalized) for t in skill.triggers):
                selected.append(skill.name)
            continue
        # No explicit triggers: fall back to name + description tokens.
        # Match on word boundaries so a token like "for" doesn't fire on
        # "information" (substring false positive).
        haystack = f"{skill.name}\n{skill.description}".lower()
        tokens = {
            token.strip(".,:;()[]{}#`*_-/")
            for token in haystack.split()
            if len(token.strip(".,:;()[]{}#`*_-/")) >= 3
        }
        if any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in tokens):
            selected.append(skill.name)
    return selected


def _trigger_match(trigger: str, normalized_text: str) -> bool:
    """A trigger matches if it appears as a substring (ASCII case-insensitive)."""
    return trigger.lower() in normalized_text
