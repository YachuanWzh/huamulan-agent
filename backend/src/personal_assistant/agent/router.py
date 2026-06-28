from langchain_core.messages import SystemMessage

from personal_assistant.agent.state import AgentState
from personal_assistant.skills import SkillRegistry

_BASE_PROMPT = (
    "You are a personal assistant running as a single ReAct agent. "
    "Your capabilities come only from the selected skills below. "
    "Use tools only when a selected skill makes them available. "
    "Every tool call is approval-gated by the harness."
)


def build_skill_router(registry: SkillRegistry):
    async def route_skills(state: AgentState) -> AgentState:
        user_text = "\n".join(
            getattr(message, "content", "")
            for message in state.get("messages", [])
            if getattr(message, "type", "") == "human"
        )[-4000:]

        selected = _keyword_route(registry, user_text)
        if not selected and registry.skills:
            selected = list(registry.skills)

        # Progressive loading: load full content only for selected skills
        for name in selected:
            registry.load_skill(name)

        system = build_system_prompt(registry, selected)
        return {
            "messages": [system],
            "selected_skills": selected,
            "allowed_tools": list(registry.tool_map_for_skills(selected)),
        }

    return route_skills


def build_system_prompt(registry: SkillRegistry, selected: list[str]) -> SystemMessage:
    """Build a progressive system prompt with meta overview + detailed selected skills."""
    sections = [_BASE_PROMPT]

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
    """Match skills using only meta (name + description), not full instructions."""
    normalized = user_text.lower()
    selected: list[str] = []
    for skill in registry.skills.values():
        haystack = f"{skill.name}\n{skill.description}".lower()
        tokens = {
            token.strip(".,:;()[]{}#`*_-/")
            for token in haystack.split()
            if len(token.strip(".,:;()[]{}#`*_-/")) >= 3
        }
        if any(token in normalized for token in tokens):
            selected.append(skill.name)
    return selected
