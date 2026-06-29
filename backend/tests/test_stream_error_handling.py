import pytest

from personal_assistant.agent.harness import AgentHarness


class FailingStreamApp:
    async def astream_events(self, *_args, **_kwargs):
        raise RuntimeError("llm connection failed")
        yield  # pragma: no cover


class FailingHarness(AgentHarness):
    def __init__(self) -> None:
        pass

    def _compile(self, _llm_config=None):
        return FailingStreamApp()


class FailingCompileHarness(AgentHarness):
    def __init__(self) -> None:
        self.decisions = {}

    def _compile(self, _llm_config=None):
        raise RuntimeError("Missing credentials")


class FakeChunk:
    def __init__(
        self,
        content: str = "",
        additional_kwargs: dict | None = None,
        response_metadata: dict | None = None,
    ) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs or {}
        self.response_metadata = response_metadata or {}


class FakeState:
    values = {"messages": []}


class ReasoningStreamApp:
    def __init__(self, chunks: list[FakeChunk]) -> None:
        self.chunks = chunks

    async def astream_events(self, *_args, **_kwargs):
        for chunk in self.chunks:
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}

    async def aget_state(self, *_args, **_kwargs):
        return FakeState()


class ReasoningHarness(AgentHarness):
    def __init__(self, chunks: list[FakeChunk]) -> None:
        self.chunks = chunks

    def _compile(self, _llm_config=None):
        return ReasoningStreamApp(self.chunks)


class ToolResultState:
    values = {"messages": []}


class ToolResultStreamApp:
    async def astream_events(self, *_args, **_kwargs):
        yield {
            "event": "on_tool_end",
            "name": "resolve_current_time",
            "data": {"output": "2026-06-29 19:30"},
        }

    async def aget_state(self, *_args, **_kwargs):
        return ToolResultState()


class ToolResultHarness(AgentHarness):
    def __init__(self) -> None:
        self.decisions = {}

    def _compile(self, _llm_config=None):
        return ToolResultStreamApp()


class PendingApprovalState:
    values = {
        "pending_approvals": [
            {
                "approval_id": "approval-1",
                "tool_call_id": "tool-1",
                "name": "resolve_current_time",
                "args": {},
            }
        ]
    }


class PendingApprovalStreamApp:
    async def astream_events(self, *_args, **_kwargs):
        if False:
            yield {}

    async def aget_state(self, *_args, **_kwargs):
        return PendingApprovalState()


class AuditMemory:
    def __init__(self) -> None:
        self.audit_events = []

    async def record_audit_event(self, event):
        self.audit_events.append(event)


class PendingApprovalHarness(AgentHarness):
    def __init__(self) -> None:
        self.memory = AuditMemory()

    def _compile(self, _llm_config=None):
        return PendingApprovalStreamApp()


@pytest.mark.asyncio
async def test_streaming_llm_errors_are_sent_as_sse_error_events() -> None:
    chunks = [
        chunk
        async for chunk in FailingHarness().run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks == [
        'event: error\ndata: {"message": "llm connection failed"}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_streaming_compile_errors_are_sent_as_sse_error_events() -> None:
    chunks = [
        chunk
        async for chunk in FailingCompileHarness().run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks == [
        'event: error\ndata: {"message": "Missing credentials"}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_approval_streaming_compile_errors_are_sent_as_sse_error_events() -> None:
    chunks = [
        chunk
        async for chunk in FailingCompileHarness().resume_after_approval_stream(
            "thread-1",
            "approval-1",
            True,
        )
    ]

    assert chunks == [
        'event: error\ndata: {"message": "Missing credentials"}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_streaming_reasoning_chunks_are_sent_as_sse_events() -> None:
    chunks = [
        chunk
        async for chunk in ReasoningHarness(
            [
                FakeChunk(additional_kwargs={"reasoning_content": "think"}),
                FakeChunk(content="answer"),
            ]
        ).run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks == [
        'event: reasoning\ndata: {"content": "think"}\n\n',
        'event: token\ndata: {"content": "answer"}\n\n',
        'event: done\ndata: {"status": "completed", "message": ""}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_streaming_chunks_without_reasoning_do_not_emit_reasoning_events() -> None:
    chunks = [
        chunk
        async for chunk in ReasoningHarness(
            [FakeChunk(response_metadata={"finish_reason": "stop"}, content="answer")]
        ).run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks == [
        'event: token\ndata: {"content": "answer"}\n\n',
        'event: done\ndata: {"status": "completed", "message": ""}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_tool_results_are_sent_as_sse_events() -> None:
    chunks = [
        chunk
        async for chunk in ToolResultHarness().resume_after_approval_stream(
            "thread-1",
            "approval-1",
            True,
        )
    ]

    assert chunks == [
        'event: tool_result\ndata: {"name": "resolve_current_time", "content": "2026-06-29 19:30"}\n\n',
        'event: done\ndata: {"status": "completed", "message": ""}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_pending_tool_approval_requests_are_recorded_to_audit() -> None:
    harness = PendingApprovalHarness()

    chunks = [
        chunk
        async for chunk in harness.run_user_turn_stream("thread-1", "what time")
    ]

    assert chunks[0].startswith("event: requires_approval")
    assert len(harness.memory.audit_events) == 1
    event = harness.memory.audit_events[0]
    assert event.thread_id == "thread-1"
    assert event.category == "tool_approval_requested"
    assert event.subject == "resolve_current_time"
    assert event.metadata == {
        "approval_id": "approval-1",
        "tool_call_id": "tool-1",
        "tool_name": "resolve_current_time",
        "tool_args": {},
    }
