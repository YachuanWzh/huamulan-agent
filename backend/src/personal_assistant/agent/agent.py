from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from personal_assistant.agent.approval import ApprovalGate
from personal_assistant.agent.harness import (
    _approval_route,
    _entry_route,
    _sanitize_messages_for_api,
)
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import build_skill_router
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import LLMConfig
from personal_assistant.config import Settings
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry


def compile_agent(
    settings: Settings,
    registry: SkillRegistry,
    memory: PostgresMemory,
    decisions: dict[str, bool],
    llm_config: LLMConfig | None = None,
):
    llm = build_llm(settings, llm_config)
    approval_gate = ApprovalGate(decisions)

    async def call_agent(state: AgentState) -> AgentState:
        active_tools = list(
            registry.tool_map_for_skills(
                state.get("selected_skills", [])
            ).values()
        )
        messages = _sanitize_messages_for_api(state["messages"])
        response = await llm.bind_tools(active_tools).ainvoke(messages)
        return {"messages": [response]}

    async def execute_tools(state: AgentState) -> AgentState:
        active_tools = list(
            registry.tool_map_for_skills(
                state.get("selected_skills", [])
            ).values()
        )
        return await ToolNode(active_tools).ainvoke(state)

    async def inspect_approval(state: AgentState) -> AgentState:
        return approval_gate.inspect(state)

    graph = StateGraph(AgentState)
    graph.add_node("route_skills", build_skill_router(registry))
    graph.add_node("agent", call_agent)
    graph.add_node("approval", inspect_approval)
    graph.add_node("tools", execute_tools)

    graph.set_conditional_entry_point(
        _entry_route,
        {
            "route_skills": "route_skills",
            "approval": "approval",
        },
    )
    graph.add_edge("route_skills", "agent")
    graph.add_edge("agent", "approval")
    graph.add_conditional_edges(
        "approval",
        _approval_route,
        {
            "wait": END,
            "tools": "tools",
            "agent": "agent",
            "end": END,
        },
    )
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=memory.checkpointer)
