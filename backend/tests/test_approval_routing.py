"""Tests for the approval/resume routing logic.

Verifies that the graph correctly handles the resume-after-approval-wait
scenario without sending unanswered tool_calls to the LLM API.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from personal_assistant.agent.approval import ApprovalGate, requires_tool_approval
from personal_assistant.agent.harness import (
    _approval_route,
    _entry_route,
    _sanitize_messages_for_api,
)
from personal_assistant.agent.state import AgentState


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _state(*messages) -> AgentState:
    """Build an AgentState with the given messages and defaults."""
    return AgentState(
        messages=list(messages),
        selected_skills=[],
        allowed_tools=[],
        pending_approvals=[],
    )


def _ai_with_tool_calls(*call_ids: str) -> AIMessage:
    """Create an AIMessage with tool_calls for the given ids."""
    return AIMessage(
        content="",
        tool_calls=[
            {"id": cid, "name": f"tool_{cid}", "args": {}}
            for cid in call_ids
        ],
    )


def _tool_msg(call_id: str, content: str = "result") -> ToolMessage:
    """Create a ToolMessage for a tool_call_id."""
    return ToolMessage(tool_call_id=call_id, content=content)


# ──────────────────────────────────────────
# _entry_route
# ──────────────────────────────────────────

class TestEntryRoute:
    def test_fresh_turn_no_messages(self):
        """On a fresh turn with no messages, route to route_skills."""
        state = _state()
        assert _entry_route(state) == "route_skills"

    def test_fresh_turn_human_only(self):
        """On a fresh turn with only a human message, route to route_skills."""
        state = _state(HumanMessage(content="Hello"))
        assert _entry_route(state) == "route_skills"

    def test_all_tool_calls_answered(self):
        """When all tool_calls have ToolMessages, route to route_skills."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            _ai_with_tool_calls("c1"),
            _tool_msg("c1"),
            AIMessage(content="Done!"),
        )
        assert _entry_route(state) == "route_skills"

    def test_unanswered_tool_calls_resume_to_approval(self):
        """When resuming with unanswered tool_calls, skip to approval."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="Do something"),
            _ai_with_tool_calls("c1"),
        )
        assert _entry_route(state) == "approval"

    def test_mixed_answered_and_unanswered(self):
        """When some calls are answered but others not, still resume to approval."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            _ai_with_tool_calls("c1", "c2"),
            _tool_msg("c1"),
        )
        assert _entry_route(state) == "approval"

    def test_multiple_ai_messages_last_unanswered(self):
        """Only the last AIMessage with tool_calls matters."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="first"),
            _ai_with_tool_calls("old1"),
            _tool_msg("old1"),
            AIMessage(content="intermediate"),
            HumanMessage(content="second"),
            _ai_with_tool_calls("new1"),  # unanswered
        )
        assert _entry_route(state) == "approval"


# ──────────────────────────────────────────
# _approval_route
# ──────────────────────────────────────────

class TestApprovalRoute:
    def test_pending_returns_wait(self):
        """When there are pending approvals, route to wait."""
        state = AgentState(
            messages=[HumanMessage("hello"), _ai_with_tool_calls("c1")],
            selected_skills=[],
            allowed_tools=[],
            pending_approvals=[{"approval_id": "a1", "tool_call_id": "c1"}],
        )
        assert _approval_route(state) == "wait"

    def test_no_pending_unanswered_tool_calls_route_to_tools(self):
        """All decided, tool calls unanswered — execute tools."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            _ai_with_tool_calls("c1", "c2"),
        )
        assert _approval_route(state) == "tools"

    def test_all_answered_tool_messages_route_to_agent(self):
        """All tool calls have ToolMessages — continue to agent."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            _ai_with_tool_calls("c1", "c2"),
            _tool_msg("c1"),
            _tool_msg("c2"),
        )
        assert _approval_route(state) == "agent"

    def test_mixed_denied_and_unexecuted_route_to_tools(self):
        """One denied (has ToolMessage), one approved (no ToolMessage) → tools."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            _ai_with_tool_calls("c1", "c2"),
            _tool_msg("c1", "Tool call denied"),  # denied
            # c2 has no ToolMessage — still needs execution
        )
        assert _approval_route(state) == "tools"

    def test_empty_messages_route_to_end(self):
        """No messages → end."""
        state = _state()
        assert _approval_route(state) == "end"

    def test_final_ai_message_route_to_end(self):
        """After agent's final response (no tool_calls), end."""
        state = _state(
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            AIMessage(content="All done!"),
        )
        assert _approval_route(state) == "end"


# ──────────────────────────────────────────
# ApprovalGate.inspect — no duplicate denials
# ──────────────────────────────────────────

class TestApprovalGateNoDuplicates:
    def test_read_file_does_not_require_approval(self):
        """Reading workspace files is auto-allowed."""
        gate = ApprovalGate(decisions={}, requires_approval=requires_tool_approval)

        state = _state(
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "c1", "name": "read_file", "args": {"path": "README.md"}}
                ],
            )
        )
        result = gate.inspect(state)

        assert result["pending_approvals"] == []

    def test_write_file_requires_approval(self):
        """Writing workspace files still waits for user approval."""
        gate = ApprovalGate(decisions={}, requires_approval=requires_tool_approval)

        state = _state(
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "c1", "name": "write_file", "args": {"path": "notes.txt", "content": "x"}}
                ],
            )
        )
        result = gate.inspect(state)

        assert result["pending_approvals"] == [
            {
                "approval_id": "c1",
                "tool_call_id": "c1",
                "name": "write_file",
                "args": {"path": "notes.txt", "content": "x"},
            }
        ]

    def test_delete_shell_command_requires_approval(self):
        """Deleting or moving files through shell commands waits for user approval."""
        gate = ApprovalGate(decisions={}, requires_approval=requires_tool_approval)

        state = _state(
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "c1", "name": "shell_command", "args": {"command": "rm -rf scratch"}}
                ],
            )
        )
        result = gate.inspect(state)

        assert result["pending_approvals"][0]["tool_call_id"] == "c1"

    def test_denial_not_duplicated_on_second_pass(self):
        """A denied tool call should not get a second ToolMessage on re-inspection."""
        gate = ApprovalGate(decisions={"c1": False})

        # First pass: AI message with tool_calls, no prior ToolMessages
        state1 = _state(_ai_with_tool_calls("c1"))
        result1 = gate.inspect(state1)
        assert "messages" in result1
        assert len(result1["messages"]) == 1  # one denial ToolMessage

        # Second pass: same AI message, but now a denial ToolMessage exists
        state2 = _state(
            _ai_with_tool_calls("c1"),
            _tool_msg("c1", "Tool call denied by user approval policy."),
        )
        result2 = gate.inspect(state2)
        # Should NOT produce another denial ToolMessage
        assert "messages" not in result2 or result2["messages"] == []

    def test_mixed_pending_and_denied_returns_both(self):
        """When one call is pending and another is denied, return both."""
        gate = ApprovalGate(decisions={"c1": False})  # c1 denied, c2 undecided

        state = _state(_ai_with_tool_calls("c1", "c2"))
        result = gate.inspect(state)

        # Should have pending for c2
        assert result["pending_approvals"]
        assert any(p["tool_call_id"] == "c2" for p in result["pending_approvals"])

        # Should have denial ToolMessage for c1
        assert "messages" in result
        assert any(
            m.tool_call_id == "c1" for m in result["messages"]
        )

    def test_all_decided_no_denials_no_pending(self):
        """When all calls are approved (True), no pending and no denials."""
        gate = ApprovalGate(decisions={"c1": True, "c2": True})

        state = _state(_ai_with_tool_calls("c1", "c2"))
        result = gate.inspect(state)

        assert result["pending_approvals"] == []
        assert "messages" not in result or result["messages"] == []


# ──────────────────────────────────────────
# _sanitize_messages_for_api
# ──────────────────────────────────────────

class TestSanitizeMessagesForAPI:
    def test_all_answered_leaves_messages_unchanged(self):
        """Valid message sequence should be returned as-is."""
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            _ai_with_tool_calls("c1"),
            _tool_msg("c1"),
            AIMessage(content="Done!"),
        ]
        result = _sanitize_messages_for_api(msgs)
        assert len(result) == 5
        # The AI message should still have tool_calls
        ai_msg = result[2]
        assert isinstance(ai_msg, AIMessage)
        assert ai_msg.tool_calls

    def test_no_tool_calls_leaves_unchanged(self):
        """Messages without any tool_calls should be unchanged."""
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
            AIMessage(content="Hi there!"),
        ]
        result = _sanitize_messages_for_api(msgs)
        assert len(result) == 3

    def test_strips_unanswered_tool_calls(self):
        """An AIMessage with unanswered tool_calls gets them stripped."""
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="do it"),
            _ai_with_tool_calls("c1", "c2"),
            _tool_msg("c1"),  # c2 is missing
        ]
        result = _sanitize_messages_for_api(msgs)
        # The AI message should now have only c1, not c2
        ai_msg = result[2]
        assert isinstance(ai_msg, AIMessage)
        answered_ids = {tc["id"] for tc in (ai_msg.tool_calls or [])}
        assert "c1" in answered_ids
        assert "c2" not in answered_ids
        # Non-AI messages should be unchanged
        assert result[0].content == "sys"
        assert result[1].content == "do it"
        assert isinstance(result[3], ToolMessage)

    def test_strips_all_when_none_answered(self):
        """When no tool_calls have ToolMessages, strip all tool_calls."""
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="do it"),
            _ai_with_tool_calls("c1", "c2"),
        ]
        result = _sanitize_messages_for_api(msgs)
        ai_msg = result[2]
        assert isinstance(ai_msg, AIMessage)
        assert not ai_msg.tool_calls or ai_msg.tool_calls == []
        assert not any(isinstance(msg, ToolMessage) for msg in result)

    def test_empty_messages_returns_empty(self):
        """Empty message list should return empty list."""
        result = _sanitize_messages_for_api([])
        assert result == []

    def test_original_messages_not_mutated(self):
        """Sanitization must not modify the original message objects."""
        ai = _ai_with_tool_calls("c1")
        msgs = [HumanMessage(content="hi"), ai]
        original_tool_calls = ai.tool_calls
        _sanitize_messages_for_api(msgs)
        # Original AI message should still have its tool_calls
        assert ai.tool_calls == original_tool_calls

    def test_strips_tool_calls_when_tool_message_is_not_adjacent(self):
        """Non-adjacent ToolMessages do not satisfy OpenAI's tool-call ordering rule."""
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="do it"),
            _ai_with_tool_calls("c1"),
            HumanMessage(content="new user turn before tool result"),
            _tool_msg("c1"),
        ]

        result = _sanitize_messages_for_api(msgs)

        ai_msg = result[2]
        assert isinstance(ai_msg, AIMessage)
        assert not ai_msg.tool_calls or ai_msg.tool_calls == []
