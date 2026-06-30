import pytest
from langchain_core.messages import AIMessage

from personal_assistant.agent.harness import AgentHarness


class FailingStreamApp:
    async def astream_events(self, *_args, **_kwargs):
        raise RuntimeError("llm connection failed")
        yield  # pragma: no cover


class FailingHarness(AgentHarness):
    def __init__(self) -> None:
        self.callbacks = []

    def _compile(self, _llm_config=None):
        return FailingStreamApp()


class FailingCompileHarness(AgentHarness):
    def __init__(self) -> None:
        self.decisions = {}
        self.callbacks = []

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
        self.callbacks = []

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
        self.callbacks = []

    def _compile(self, _llm_config=None):
        return ToolResultStreamApp()


class CompactingState:
    values = {"messages": []}


class CompactingStreamApp:
    async def astream_events(self, *_args, **_kwargs):
        yield {
            "event": "on_chain_start",
            "name": "compact_context",
            "data": {
                "input": {
                    "messages": [
                        {"type": "human", "content": f"user {index}"}
                        for index in range(20)
                    ],
                    "approval_turn_count": 1,
                }
            },
        }
        yield {
            "event": "on_chain_end",
            "name": "compact_context",
            "data": {"output": {"messages": ["compacted"]}},
        }

    async def aget_state(self, *_args, **_kwargs):
        return CompactingState()


class CompactingHarness(AgentHarness):
    def __init__(self) -> None:
        self.callbacks = []

    def _compile(self, _llm_config=None):
        return CompactingStreamApp()


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
        self.callbacks = []

    def _compile(self, _llm_config=None):
        return PendingApprovalStreamApp()


class CompletedState:
    values = {"messages": [AIMessage(content="Final answer")], "pending_approvals": []}


class CompletedStreamApp:
    async def astream_events(self, *_args, **_kwargs):
        yield {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk(content="Final")}}
        yield {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk(content=" answer")}}

    async def aget_state(self, *_args, **_kwargs):
        return CompletedState()


class BackgroundReflectionHarness(AgentHarness):
    def __init__(self) -> None:
        self.callbacks = []
        self.scheduled_values = []

    def _compile(self, _llm_config=None, *, enable_memory_reflection=True):
        assert enable_memory_reflection is False
        return CompletedStreamApp()

    def _schedule_memory_reflection(self, thread_id, values, llm_config, callbacks):
        self.scheduled_values.append((thread_id, values, llm_config, callbacks))


class BatchApprovalStreamApp:
    def __init__(self) -> None:
        self.inputs = []

    async def astream_events(self, input_values, *_args, **_kwargs):
        self.inputs.append(input_values)
        yield {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk(content="Done")}}

    async def aget_state(self, *_args, **_kwargs):
        return CompletedState()


class BatchApprovalHarness(AgentHarness):
    def __init__(self) -> None:
        self.decisions = {}
        self.callbacks = []
        self.app = BatchApprovalStreamApp()

    def _compile(self, _llm_config=None):
        return self.app


class ApprovalBackgroundReflectionHarness(AgentHarness):
    def __init__(self) -> None:
        self.decisions = {}
        self.callbacks = []
        self.scheduled_values = []
        self.app = BatchApprovalStreamApp()

    def _compile(self, _llm_config=None, *, enable_memory_reflection=True):
        assert enable_memory_reflection is False
        return self.app

    def _schedule_memory_reflection(self, thread_id, values, llm_config, callbacks):
        self.scheduled_values.append((thread_id, values, llm_config, callbacks))


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
async def test_compaction_progress_is_sent_as_sse_events() -> None:
    chunks = [
        chunk
        async for chunk in CompactingHarness().run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks[:2] == [
        'event: compacting\ndata: {"status": "started", "content": "Compacting context"}\n\n',
        'event: compacting\ndata: {"status": "completed", "content": "Context compacted"}\n\n',
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


@pytest.mark.asyncio
async def test_stream_done_is_sent_before_background_memory_reflection() -> None:
    harness = BackgroundReflectionHarness()

    chunks = [
        chunk
        async for chunk in harness.run_user_turn_stream("thread-1", "remember this")
    ]

    assert chunks == [
        'event: token\ndata: {"content": "Final"}\n\n',
        'event: token\ndata: {"content": " answer"}\n\n',
        'event: done\ndata: {"status": "completed", "message": "Final answer"}\n\n',
        "data: [DONE]\n\n",
    ]
    assert harness.scheduled_values == [
        ("thread-1", CompletedState.values, None, None),
    ]


@pytest.mark.asyncio
async def test_batch_approval_stream_records_all_decisions_and_resumes_once() -> None:
    harness = BatchApprovalHarness()

    chunks = [
        chunk
        async for chunk in harness.resume_after_approvals_stream(
            "thread-1",
            [
                {"approval_id": "approval-1", "approved": True},
                {"approval_id": "approval-2", "approved": False},
            ],
        )
    ]

    assert harness.decisions == {"approval-1": True, "approval-2": False}
    assert harness.app.inputs == [{"approval_turn_count": 2}]
    assert chunks == [
        'event: token\ndata: {"content": "Done"}\n\n',
        'event: done\ndata: {"status": "completed", "message": "Final answer"}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_approval_resume_stream_schedules_memory_reflection_in_background() -> None:
    harness = ApprovalBackgroundReflectionHarness()

    chunks = [
        chunk
        async for chunk in harness.resume_after_approval_stream(
            "thread-1",
            "approval-1",
            True,
        )
    ]

    assert chunks == [
        'event: token\ndata: {"content": "Done"}\n\n',
        'event: done\ndata: {"status": "completed", "message": "Final answer"}\n\n',
        "data: [DONE]\n\n",
    ]
    assert harness.scheduled_values == [
        ("thread-1", CompletedState.values, None, None),
    ]


@pytest.mark.asyncio
async def test_batch_approval_resume_stream_schedules_memory_reflection_in_background() -> None:
    harness = ApprovalBackgroundReflectionHarness()

    chunks = [
        chunk
        async for chunk in harness.resume_after_approvals_stream(
            "thread-1",
            [{"approval_id": "approval-1", "approved": True}],
        )
    ]

    assert chunks == [
        'event: token\ndata: {"content": "Done"}\n\n',
        'event: done\ndata: {"status": "completed", "message": "Final answer"}\n\n',
        "data: [DONE]\n\n",
    ]
    assert harness.scheduled_values == [
        ("thread-1", CompletedState.values, None, None),
    ]
