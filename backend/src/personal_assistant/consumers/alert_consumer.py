"""Kafka consumer for P2/P3 OTEL alerts — cron-driven batch polling.

P0/P1 alerts arrive via AlertManager webhook (push model) at
``POST /api/otel/alerts``.  P2/P3 alerts are written to a dedicated
Kafka topic and consumed here by two background loops running at
independently configurable intervals.

Configuration via env vars (see config.py):

- ``OTEL_ALERT_KAFKA_ENABLED`` — enable the alert consumer (default: False)
- ``OTEL_ALERT_KAFKA_TOPIC`` — Kafka topic for alerts (default: otel-alerts)
- ``OTEL_ALERT_P2_POLL_SECONDS`` — P2 poll interval (default: 300 = 5 min)
- ``OTEL_ALERT_P3_POLL_SECONDS`` — P3 poll interval (default: 1800 = 30 min)
- ``OTEL_ALERT_KAFKA_MAX_MESSAGES`` — max messages per poll cycle (default: 100)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from personal_assistant.config import get_settings

logger = logging.getLogger(__name__)

# ── AlertManager severity → P0-P3 level mapping ──────────────────────

_SEVERITY_LEVEL_MAP: dict[str, str] = {
    "critical": "P0",
    "warning": "P1",
    "info": "P2",
    "none": "P3",
}


def _severity_to_level(severity: str) -> str:
    """Map an AlertManager severity label to a P0-P3 display level."""
    return _SEVERITY_LEVEL_MAP.get(severity.lower(), "P3")


def _parse_alert_message(raw: str | bytes) -> dict[str, Any] | None:
    """Parse a single Kafka alert message into the standard alert dict.

    The message format is AlertManager v4 webhook JSON (single alert,
    not the top-level ``AlertManagerWebhook`` wrapper — each Kafka
    record is one ``OtelAlert``-shaped object).

    Returns ``None`` for malformed or incomplete messages.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Alert consumer: failed to parse JSON message")
        return None

    if not isinstance(obj, dict):
        return None

    labels = obj.get("labels") or {}
    if not isinstance(labels, dict) or not labels:
        logger.warning("Alert consumer: message has no labels, skipping")
        return None

    severity = labels.get("severity", "")
    level = _severity_to_level(severity)
    annotations = obj.get("annotations") or {}

    alert_data: dict[str, Any] = {
        "id": uuid4().hex[:12],
        "received_at": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "level": level,
        "service_name": labels.get("service_name", "unknown"),
        "alert_name": labels.get("alertname", "unknown"),
        "summary": annotations.get("summary", ""),
        "description": annotations.get("description", ""),
        "starts_at": obj.get("startsAt", ""),
        "status": obj.get("status", "firing"),
        "rca_status": "pending",
        "rca_thread_id": None,
        "rca_pending_approvals": None,
        "rca_result_text": None,
        "metadata": {},
    }
    return alert_data
