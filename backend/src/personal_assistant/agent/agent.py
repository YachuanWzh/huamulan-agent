from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, ToolMessage

from personal_assistant.agent.approval import ApprovalGate
from personal_assistant.agent.harness import (
    _record_audit,
    _approval_route,
    _entry_route,
    _sanitize_messages_for_api,
    scan_tool_guard,
)
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import build_skill_router
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import AuditEventCreate, LLMConfig
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

    async def execute_tools(state: AgentState, config=None) -> AgentState:
        active_tools = list(
            registry.tool_map_for_skills(
                state.get("selected_skills", [])
            ).values()
        )
        messages = state.get("messages", [])
        ai_message = _latest_ai_with_tool_calls(messages)
        if ai_message is None:
            return await ToolNode(active_tools).ainvoke(state)

        thread_id = None
        if isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                thread_id = configurable.get("thread_id")

        allowed_calls = []
        blocked_messages = []
        for call in ai_message.tool_calls:
            match = scan_tool_guard(call["name"], call.get("args", {}))
            if match is None:
                allowed_calls.append(call)
                continue
            await _record_audit(
                memory,
                AuditEventCreate(
                    thread_id=thread_id,
                    source="tool",
                    category=match.category,
                    severity=match.severity,
                    reason=match.reason,
                    subject=call["name"],
                    metadata={
                        "tool_call_id": call["id"],
                        "tool_name": call["name"],
                        "tool_args": call.get("args", {}),
                        "tool_guard_blocked": True,
                    },
                ),
            )
            blocked_messages.append(
                ToolMessage(
                    tool_call_id=call["id"],
                    content=f"SecurityError: {match.category}: {match.reason}",
                )
            )

        if not blocked_messages:
            return await ToolNode(active_tools).ainvoke(state)
        if not allowed_calls:
            return {"messages": blocked_messages}

        guarded_state = dict(state)
        guarded_state["messages"] = [
            _replace_ai_tool_calls(message, allowed_calls) if message is ai_message else message
            for message in messages
        ]
        result = await ToolNode(active_tools).ainvoke(guarded_state)
        return {"messages": [*blocked_messages, *result.get("messages", [])]}

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


def _latest_ai_with_tool_calls(messages):
    for message in reversed(messages):
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            return message
    return None


def _replace_ai_tool_calls(message: AIMessage, tool_calls: list[dict]) -> AIMessage:
    return AIMessage(
        content=message.content,
        tool_calls=tool_calls,
        id=getattr(message, "id", None),
        name=getattr(message, "name", None),
    )
