"""Tests for personal_assistant.tracing — Langfuse callback factory and harness injection."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from personal_assistant.agent.harness import AgentHarness


def test_build_callback_returns_none_when_disabled() -> None:
    """When Langfuse is disabled, build_langfuse_callback returns None."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = False

    result = build_langfuse_callback(settings)
    assert result is None


def test_build_callback_sets_env_vars_when_enabled() -> None:
    """When Langfuse is enabled, env vars are set for secret_key and host."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "https://langfuse.example.com"

    with patch.dict("os.environ", {}, clear=True):
        with patch("personal_assistant.tracing.Langfuse"):
            with patch("personal_assistant.tracing.CallbackHandler") as mock_handler_cls:
                mock_handler = MagicMock()
                mock_handler_cls.return_value = mock_handler

                result = build_langfuse_callback(settings)

                assert os.environ.get("LANGFUSE_SECRET_KEY") == "sk-test"
                assert os.environ.get("LANGFUSE_HOST") == "https://langfuse.example.com"
                mock_handler_cls.assert_called_once_with(public_key="pk-test")
                assert result is mock_handler


def test_build_callback_initializes_langfuse_client_when_enabled() -> None:
    """Langfuse 4.x requires a client before CallbackHandler can trace."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "https://langfuse.example.com"

    with patch("personal_assistant.tracing.Langfuse") as mock_langfuse_cls:
        with patch("personal_assistant.tracing.CallbackHandler"):
            build_langfuse_callback(settings)

    mock_langfuse_cls.assert_called_once_with(
        public_key="pk-test",
        secret_key="sk-test",
        host="https://langfuse.example.com",
    )


def test_build_callback_uses_default_host_when_none() -> None:
    """When no host is set, defaults to cloud.langfuse.com."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "https://cloud.langfuse.com"

    with patch.dict("os.environ", {}, clear=True):
        with patch("personal_assistant.tracing.Langfuse"):
            with patch("personal_assistant.tracing.CallbackHandler"):
                build_langfuse_callback(settings)

                assert os.environ.get("LANGFUSE_HOST") == "https://cloud.langfuse.com"


def test_build_callback_returns_none_when_import_fails() -> None:
    """Gracefully returns None if langfuse is not installed."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True

    with patch("personal_assistant.tracing._LANGFUSE_AVAILABLE", False):
        result = build_langfuse_callback(settings)
        assert result is None


def test_build_callback_sets_no_proxy_for_langfuse_host() -> None:
    """Langfuse host is added to NO_PROXY so trace data bypasses HTTP proxies."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "http://langfuse.internal.example:3000"

    with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:7897"}, clear=True):
        with patch("personal_assistant.tracing.Langfuse"):
            with patch("personal_assistant.tracing.CallbackHandler"):
                build_langfuse_callback(settings)

                no_proxy = os.environ.get("NO_PROXY", "")
                assert "langfuse.internal.example" in no_proxy


def test_build_callback_appends_to_existing_no_proxy() -> None:
    """Existing NO_PROXY entries are preserved when adding Langfuse host."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "http://langfuse.internal.example:3000"

    with patch.dict(
        "os.environ",
        {"NO_PROXY": "localhost,127.0.0.1"},
        clear=True,
    ):
        with patch("personal_assistant.tracing.Langfuse"):
            with patch("personal_assistant.tracing.CallbackHandler"):
                build_langfuse_callback(settings)

                no_proxy = os.environ.get("NO_PROXY", "")
                assert "localhost" in no_proxy
                assert "127.0.0.1" in no_proxy
                assert "langfuse.internal.example" in no_proxy


def test_build_callback_no_proxy_skips_cloud_langfuse() -> None:
    """Default cloud.langfuse.com host does not need NO_PROXY bypass."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "https://cloud.langfuse.com"

    with patch.dict("os.environ", {}, clear=True):
        with patch("personal_assistant.tracing.Langfuse"):
            with patch("personal_assistant.tracing.CallbackHandler"):
                build_langfuse_callback(settings)

                # Should not set NO_PROXY for cloud host
                assert "NO_PROXY" not in os.environ


def test_build_callback_does_not_set_env_secret_key_when_none() -> None:
    """Does not override other env vars when secret_key is None."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = None
    settings.langfuse_host = "https://cloud.langfuse.com"

    with patch.dict("os.environ", {}, clear=True):
        with patch("personal_assistant.tracing.Langfuse"):
            with patch("personal_assistant.tracing.CallbackHandler"):
                build_langfuse_callback(settings)

                # Should not set LANGFUSE_SECRET_KEY when it's None
                assert "LANGFUSE_SECRET_KEY" not in os.environ


# ── AgentHarness callback injection tests ────────────────────────────────────


def _make_harness_settings():
    settings = MagicMock()
    settings.llm_base_url = "https://api.example.com"
    settings.llm_api_key = "sk-test"
    settings.llm_model = "test-model"
    settings.llm_temperature = 0.2
    return settings


def test_harness_accepts_callbacks_in_constructor() -> None:
    """AgentHarness stores callbacks from constructor."""
    mock_cb = MagicMock()
    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[mock_cb],
    )
    assert harness.callbacks == [mock_cb]


def test_harness_callbacks_default_to_empty_list() -> None:
    """When no callbacks are provided, harness.callbacks is an empty list."""
    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
    )
    assert harness.callbacks == []


async def test_run_user_turn_injects_callbacks_into_ainvoke() -> None:
    """AgentHarness.run_user_turn passes callbacks to app.ainvoke config."""
    mock_callback = MagicMock()
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[mock_callback],
    )

    with patch.object(harness, "_compile", return_value=mock_app):
        await harness.run_user_turn("thread-1", "hello")

    call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
    assert "callbacks" in call_config
    assert mock_callback in call_config["callbacks"]


async def test_run_user_turn_no_callbacks_leaves_config_clean() -> None:
    """Without callbacks, ainvoke config has no callbacks key."""
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
    )

    with patch.object(harness, "_compile", return_value=mock_app):
        await harness.run_user_turn("thread-1", "hello")

    call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
    assert "callbacks" not in call_config


async def test_run_user_turn_metadata_when_callbacks_present() -> None:
    """When callbacks are provided, langfuse_session_id is in metadata."""
    mock_callback = MagicMock()
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[mock_callback],
    )

    with patch.object(harness, "_compile", return_value=mock_app):
        await harness.run_user_turn("thread-abc-123", "hello")

    call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
    assert "metadata" in call_config
    assert call_config["metadata"]["langfuse_session_id"] == "thread-abc-123"


async def test_run_user_turn_merges_per_request_callbacks() -> None:
    """Per-request callbacks are merged with harness-level callbacks."""
    harness_cb = MagicMock()
    request_cb = MagicMock()
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[harness_cb],
    )

    with patch.object(harness, "_compile", return_value=mock_app):
        await harness.run_user_turn("thread-1", "hello", callbacks=[request_cb])

    call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
    callbacks = call_config.get("callbacks", [])
    assert harness_cb in callbacks
    assert request_cb in callbacks


async def test_resume_after_approval_injects_callbacks() -> None:
    """resume_after_approval also passes callbacks to ainvoke."""
    mock_callback = MagicMock()
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[mock_callback],
    )
    harness.memory = MagicMock()

    with patch.object(harness, "_compile", return_value=mock_app):
        await harness.resume_after_approval("thread-1", "approval-1", True)

    call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
    assert "callbacks" in call_config
    assert mock_callback in call_config["callbacks"]


# ── Streaming callback injection tests ────────────────────────────────────────


async def _consume_stream(stream):
    """Helper: fully consume an async generator."""
    async for _ in stream:
        pass


async def test_run_user_turn_stream_injects_callbacks() -> None:
    """Streaming calls pass callbacks to astream_events config."""
    mock_callback = MagicMock()
    mock_state = MagicMock()
    mock_state.values = {}

    configs_seen = []

    async def spy_astream(*args, **kwargs):
        configs_seen.append(kwargs.get("config", {}))
        yield {"event": "on_chain_end", "data": {}}

    mock_app = MagicMock()
    mock_app.astream_events = spy_astream
    mock_app.aget_state = AsyncMock(return_value=mock_state)

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[mock_callback],
    )

    with patch.object(harness, "_compile", return_value=mock_app):
        await _consume_stream(
            harness.run_user_turn_stream("thread-abc", "hello")
        )

    assert len(configs_seen) == 1
    call_config = configs_seen[0]
    assert "callbacks" in call_config
    assert mock_callback in call_config["callbacks"]
    assert "metadata" in call_config
    assert call_config["metadata"]["langfuse_session_id"] == "thread-abc"
    trace_context = call_config["configurable"]["trace_context"]
    assert trace_context["thread_id"] == "thread-abc"
    assert trace_context["trace_id"]


async def test_resume_after_approval_stream_injects_callbacks() -> None:
    """Streaming approval resume passes callbacks to astream_events."""
    mock_callback = MagicMock()
    mock_state = MagicMock()
    mock_state.values = {}

    configs_seen = []

    async def spy_astream(*args, **kwargs):
        configs_seen.append(kwargs.get("config", {}))
        yield {"event": "on_chain_end", "data": {}}

    mock_app = MagicMock()
    mock_app.astream_events = spy_astream
    mock_app.aget_state = AsyncMock(return_value=mock_state)

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
        callbacks=[mock_callback],
    )
    harness.memory = MagicMock()

    with patch.object(harness, "_compile", return_value=mock_app):
        await _consume_stream(
            harness.resume_after_approval_stream("thread-xyz", "approval-1", True)
        )

    assert len(configs_seen) == 1
    call_config = configs_seen[0]
    assert "callbacks" in call_config
    assert mock_callback in call_config["callbacks"]
    assert call_config["metadata"]["langfuse_session_id"] == "thread-xyz"


async def test_stream_no_callbacks_leaves_config_clean() -> None:
    """Without callbacks, streaming config has no callbacks key."""
    mock_state = MagicMock()
    mock_state.values = {}

    configs_seen = []

    async def spy_astream(*args, **kwargs):
        configs_seen.append(kwargs.get("config", {}))
        yield {"event": "on_chain_end", "data": {}}

    mock_app = MagicMock()
    mock_app.astream_events = spy_astream
    mock_app.aget_state = AsyncMock(return_value=mock_state)

    harness = AgentHarness(
        settings=_make_harness_settings(),
        registry=MagicMock(),
        memory=MagicMock(),
    )

    with patch.object(harness, "_compile", return_value=mock_app):
        await _consume_stream(
            harness.run_user_turn_stream("thread-1", "hello")
        )

    call_config = configs_seen[0]
    assert "callbacks" not in call_config
