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
