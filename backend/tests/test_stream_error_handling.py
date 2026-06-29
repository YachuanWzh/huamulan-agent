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
