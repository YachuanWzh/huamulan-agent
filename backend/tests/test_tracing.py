"""Tests for personal_assistant.tracing — Langfuse callback factory."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


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
