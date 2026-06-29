"""Langfuse observability integration for the LangGraph agent.

Provides a factory to build a Langfuse LangChain CallbackHandler that
auto-traces LLM calls, tool executions, and graph node transitions.
"""
from __future__ import annotations

import logging
import os

from personal_assistant.config import Settings

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse  # type: ignore[import-untyped]
    from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]

    _LANGFUSE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LANGFUSE_AVAILABLE = False


def build_langfuse_callback(
    settings: Settings,
) -> CallbackHandler | None:
    """Build a Langfuse LangChain CallbackHandler if Langfuse is enabled.

    Returns ``None`` when:
    - ``settings.langfuse_enabled`` is ``False`` (no credentials configured)
    - The ``langfuse`` package is not installed

    The returned handler can be passed directly to LangChain/LangGraph
    via ``config["callbacks"]``.
    """
    if not settings.langfuse_enabled:
        return None

    if not _LANGFUSE_AVAILABLE:
        logger.warning(
            "Langfuse is enabled in settings but the langfuse package "
            "is not installed. Install it with: pip install langfuse"
        )
        return None

    # Langfuse 4.x reads secret_key and host from environment variables
    if settings.langfuse_secret_key:
        os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    if settings.langfuse_host:
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)

    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )

    return CallbackHandler(
        public_key=settings.langfuse_public_key,
    )
