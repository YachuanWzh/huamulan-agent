import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from personal_assistant.agent.approval import ApprovalGate
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import build_skill_router
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import ChatResponse, LLMConfig, ToolCallApproval
from personal_assistant.config import Settings
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry


class AgentHarness:
    def __init__(self, settings: Settings, registry: SkillRegistry, memory: PostgresMemory):
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.decisions: dict[str, bool] = {}

    async def run_user_turn(
        self,
        thread_id: str,
        message: str,
        llm_config: LLMConfig | None = None,
    ) -> ChatResponse:
        app = self._compile(llm_config)
        result = await app.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        return _to_response(thread_id, result)

    async def resume_after_approval(
        self,
        thread_id: str,
        approval_id: str,
        approved: bool,
        llm_config: LLMConfig | None = None,
    ) -> ChatResponse:
        self.decisions[approval_id] = approved
        app = self._compile(llm_config)
        result = await app.ainvoke(
            {},
            config={"configurable": {"thread_id": thread_id}},
        )
        return _to_response(thread_id, result)

    async def replay(self, thread_id: str) -> list[dict[str, Any]]:
        return await self.memory.replay(thread_id)

    async def run_user_turn_stream(
        self,
        thread_id: str,
        message: str,
        llm_config: LLMConfig | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream the agent response as SSE events."""
        app = self._compile(llm_config)
        config = {"configurable": {"thread_id": thread_id}}

        async for event in app.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    yield _sse_event("token", {"content": chunk.content})

        # After streaming, inspect final state
        state = await app.aget_state(config)
        values = state.values if state.values else {}
        pending = values.get("pending_approvals") or []

        if pending:
            yield _sse_event("requires_approval", {
                "approvals": [
                    {"approval_id": a["approval_id"], "tool_call_id": a["tool_call_id"],
                     "name": a["name"], "args": a["args"]}
                    for a in pending
                ]
            })
        else:
            msg = _extract_last_ai_message(values.get("messages", []))
            yield _sse_event("done", {"status": "completed", "message": msg})

        yield "data: [DONE]\n\n"

    async def resume_after_approval_stream(
        self,
        thread_id: str,
        approval_id: str,
        approved: bool,
        llm_config: LLMConfig | None = None,
    ) -> AsyncGenerator[str, None]:
        """Resume after approval with streaming SSE events."""
        self.decisions[approval_id] = approved
        app = self._compile(llm_config)
        config = {"configurable": {"thread_id": thread_id}}

        async for event in app.astream_events(
            {},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    yield _sse_event("token", {"content": chunk.content})

        state = await app.aget_state(config)
        values = state.values if state.values else {}
        pending = values.get("pending_approvals") or []

        if pending:
            yield _sse_event("requires_approval", {
                "approvals": [
                    {"approval_id": a["approval_id"], "tool_call_id": a["tool_call_id"],
                     "name": a["name"], "args": a["args"]}
                    for a in pending
                ]
            })
        else:
            msg = _extract_last_ai_message(values.get("messages", []))
            yield _sse_event("done", {"status": "completed", "message": msg})

        yield "data: [DONE]\n\n"

    def _compile(self, llm_config: LLMConfig | None):
        llm = build_llm(self.settings, llm_config)
        approval_gate = ApprovalGate(self.decisions)

        async def call_agent(state: AgentState) -> AgentState:
            # Dynamically resolve tools from loaded skills (progressive loading)
            active_tools = list(
                self.registry.tool_map_for_skills(
                    state.get("selected_skills", [])
                ).values()
            )
            response = await llm.bind_tools(active_tools).ainvoke(state["messages"])
            return {"messages": [response]}

        async def execute_tools(state: AgentState) -> AgentState:
            # Dynamically create ToolNode with currently loaded tools
            active_tools = list(
                self.registry.tool_map_for_skills(
                    state.get("selected_skills", [])
                ).values()
            )
            return await ToolNode(active_tools).ainvoke(state)

        async def inspect_approval(state: AgentState) -> AgentState:
            return approval_gate.inspect(state)

        graph = StateGraph(AgentState)
        graph.add_node("route_skills", build_skill_router(self.registry))
        graph.add_node("agent", call_agent)
        graph.add_node("approval", inspect_approval)
        graph.add_node("tools", execute_tools)

        graph.set_entry_point("route_skills")
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
        return graph.compile(checkpointer=self.memory.checkpointer)


def _approval_route(state: AgentState) -> str:
    pending = state.get("pending_approvals") or []
    if pending:
        return "wait"

    messages: Sequence[Any] = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]

    if getattr(last, "type", "") == "tool":
        return "agent"
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "end"


def _to_response(thread_id: str, state: AgentState) -> ChatResponse:
    pending = state.get("pending_approvals") or []
    if pending:
        return ChatResponse(
            thread_id=thread_id,
            status="requires_approval",
            approvals=[ToolCallApproval(**item) for item in pending],
        )

    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "ai" and getattr(message, "content", None):
            return ChatResponse(thread_id=thread_id, status="completed", message=message.content)
    return ChatResponse(thread_id=thread_id, status="completed", message="")


def _extract_last_ai_message(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "ai" and getattr(msg, "content", None):
            return msg.content
    return ""


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
