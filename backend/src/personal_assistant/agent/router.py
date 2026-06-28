from langchain_core.messages import SystemMessage

from personal_assistant.agent.state import AgentState
from personal_assistant.skills import SkillRegistry


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

        instructions = "\n\n".join(
            f"## Skill: {registry.skills[name].name}\n{registry.skills[name].instructions}"
            for name in selected
            if name in registry.skills
        )

        system = SystemMessage(
            content=(
                "You are a personal assistant running as a single ReAct agent. "
                "Your capabilities come only from the selected skills below. "
                "Use tools only when a selected skill makes them available. "
                "Every tool call is approval-gated by the harness.\n\n"
                f"{instructions}"
            )
        )
        return {
            "messages": [system],
            "selected_skills": selected,
            "allowed_tools": list(registry.tool_map_for_skills(selected)),
        }

    return route_skills


def _keyword_route(registry: SkillRegistry, user_text: str) -> list[str]:
    normalized = user_text.lower()
    selected: list[str] = []
    for skill in registry.skills.values():
        haystack = f"{skill.name}\n{skill.description}\n{skill.instructions}".lower()
        tokens = {
            token.strip(".,:;()[]{}#`*_-/")
            for token in haystack.split()
            if len(token.strip(".,:;()[]{}#`*_-/")) >= 3
        }
        if any(token in normalized for token in tokens):
            selected.append(skill.name)
    return selected
