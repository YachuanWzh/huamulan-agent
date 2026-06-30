from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from personal_assistant.agent.state import AgentState


RequiresApproval = Callable[[str, Any], bool]


_READ_ONLY_TOOL_NAMES = {"read_file"}


def requires_tool_approval(tool_name: str, args: Any) -> bool:
    if tool_name in _READ_ONLY_TOOL_NAMES:
        return False
    return True


@dataclass
class ApprovalGate:
    decisions: dict[str, bool]
    requires_approval: RequiresApproval | None = None

    def inspect(self, state: AgentState) -> AgentState:
        messages = state.get("messages", [])
        if not messages:
            return {"pending_approvals": []}
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None)
        if not isinstance(last, AIMessage) or not tool_calls:
            return {"pending_approvals": []}

        # Track existing ToolMessage responses to avoid duplicates across
        # resume cycles — a denied call already has a ToolMessage from a
        # prior inspection pass and should not get a second one.
        answered_ids: set[str] = {
            m.tool_call_id
            for m in messages
            if isinstance(m, ToolMessage)
        }

        pending: list[dict[str, Any]] = []
        denial_messages: list[ToolMessage] = []
        for call in tool_calls:
            approval_id = _approval_id(call)
            tool_name = str(call.get("name") or "")
            args = call.get("args", {})
            requires_approval = (
                self.requires_approval(tool_name, args)
                if self.requires_approval is not None
                else True
            )
            if requires_approval and approval_id not in self.decisions:
                pending.append(
                    {
                        "approval_id": approval_id,
                        "tool_call_id": call["id"],
                        "name": call["name"],
                        "args": call.get("args", {}),
                    }
                )
            elif (
                requires_approval
                and self.decisions.get(approval_id) is False
                and call["id"] not in answered_ids
            ):
                denial_messages.append(
                    ToolMessage(
                        tool_call_id=call["id"],
                        content="Tool call denied by user approval policy.",
                    )
                )

        result: dict[str, Any] = {"pending_approvals": pending}
        if denial_messages:
            # Return denial ToolMessages even when other calls are still
            # pending — the state's add_messages reducer appends them, and
            # _entry_route / _approval_route use answered_ids to skip
            # already-answered tool calls in subsequent passes.
            result["messages"] = denial_messages
        return result


def _approval_id(call: dict[str, Any]) -> str:
    return call.get("id") or str(uuid4())
