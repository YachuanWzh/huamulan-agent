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
        with patch("personal_assistant.tracing.CallbackHandler") as mock_handler_cls:
            mock_handler = MagicMock()
            mock_handler_cls.return_value = mock_handler

            result = build_langfuse_callback(settings)

            assert os.environ.get("LANGFUSE_SECRET_KEY") == "sk-test"
            assert os.environ.get("LANGFUSE_HOST") == "https://langfuse.example.com"
            mock_handler_cls.assert_called_once_with(public_key="pk-test")
            assert result is mock_handler


def test_build_callback_uses_default_host_when_none() -> None:
    """When no host is set, defaults to cloud.langfuse.com."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "https://cloud.langfuse.com"

    with patch.dict("os.environ", {}, clear=True):
        with patch("personal_assistant.tracing.CallbackHandler") as mock_handler_cls:
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


def test_build_callback_does_not_set_env_secret_key_when_none() -> None:
    """Does not override other env vars when secret_key is None."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = None
    settings.langfuse_host = "https://cloud.langfuse.com"

    with patch.dict("os.environ", {}, clear=True):
        with patch("personal_assistant.tracing.CallbackHandler") as mock_handler_cls:
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
