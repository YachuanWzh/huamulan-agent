import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    selected_skills: list[str]
    routing_trace: list[dict[str, Any]]
    allowed_tools: list[str]
    pending_approvals: list[dict[str, Any]]
    approval_turn_count: Annotated[int, operator.add]
    rewritten_query: str
    intent_slots: dict[str, Any]
    user_vector_context: dict[str, Any]
    multiagent_plan: dict[str, Any]
    apm_reports: Annotated[list[dict[str, Any]], operator.add]
    child_agent_tasks: list[dict[str, Any]]  # 子 agent 任务状态列表 (multi-agent only)
    knowledge_context: dict[str, Any]
