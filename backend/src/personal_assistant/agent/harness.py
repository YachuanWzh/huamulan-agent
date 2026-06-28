from collections.abc import Sequence
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

    def _compile(self, llm_config: LLMConfig | None):
        tools = self.registry.all_tools
        llm = build_llm(self.settings, llm_config).bind_tools(tools)
        approval_gate = ApprovalGate(self.decisions)

        async def call_agent(state: AgentState) -> AgentState:
            allowed = set(state.get("allowed_tools") or [])
            active_tools = [tool for tool in tools if tool.name in allowed]
            response = await llm.bind_tools(active_tools).ainvoke(state["messages"])
            return {"messages": [response]}

        async def inspect_approval(state: AgentState) -> AgentState:
            return approval_gate.inspect(state)

        graph = StateGraph(AgentState)
        graph.add_node("route_skills", build_skill_router(self.registry))
        graph.add_node("agent", call_agent)
        graph.add_node("approval", inspect_approval)
        graph.add_node("tools", ToolNode(tools))

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
