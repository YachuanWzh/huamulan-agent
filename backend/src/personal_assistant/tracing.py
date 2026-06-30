"""Langfuse observability integration for the LangGraph agent.

Provides a factory to build a Langfuse LangChain CallbackHandler that
auto-traces LLM calls, tool executions, and graph node transitions.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

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

    # Ensure the Langfuse host bypasses any HTTP proxy so OTEL trace
    # export is not routed through a local proxy (e.g. Clash) that
    # cannot reach a self-hosted instance on the LAN.
    _ensure_no_proxy(settings.langfuse_host)

    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )

    return CallbackHandler(
        public_key=settings.langfuse_public_key,
    )


def _ensure_no_proxy(host: str | None) -> None:
    """Add the Langfuse host to ``NO_PROXY`` so trace data bypasses HTTP proxies.

    Self-hosted Langfuse instances are typically on a private network that
    local proxies (Clash, V2Ray, etc.) cannot reach.  Without this, the
    OTEL span exporter routes traffic through the proxy, which times out
    and silently drops all trace data.

    Skips ``cloud.langfuse.com`` — the public cloud endpoint is reachable
    through normal internet routing and does not need proxy bypass.
    """
    if not host:
        return

    try:
        hostname = urlparse(host).hostname
    except Exception:
        return

    if not hostname or hostname == "cloud.langfuse.com":
        return

    for var in ("NO_PROXY", "no_proxy"):
        existing = os.environ.get(var, "")
        entries = [e.strip() for e in existing.split(",") if e.strip()]
        if hostname not in entries:
            entries.append(hostname)
            os.environ[var] = ",".join(entries)

    logger.debug("Added %s to NO_PROXY for Langfuse OTEL export", hostname)
