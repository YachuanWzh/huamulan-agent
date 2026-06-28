from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from personal_assistant.agent.state import AgentState


@dataclass
class ApprovalGate:
    decisions: dict[str, bool]

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
            if approval_id not in self.decisions:
                pending.append(
                    {
                        "approval_id": approval_id,
                        "tool_call_id": call["id"],
                        "name": call["name"],
                        "args": call.get("args", {}),
                    }
                )
            elif (
                self.decisions[approval_id] is False
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
