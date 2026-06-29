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
