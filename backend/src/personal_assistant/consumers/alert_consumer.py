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


# ── AlertKafkaConsumer ────────────────────────────────────────────────


class AlertKafkaConsumer:
    """Cron-driven Kafka consumer for P2/P3 OTEL alerts.

    Two background ``asyncio.Task`` loops poll a shared Kafka topic at
    independently configurable intervals:

    - **P2 loop**: polls every ``p2_interval`` seconds, processes only
      messages whose ``level`` is ``"P2"``.
    - **P3 loop**: polls every ``p3_interval`` seconds, processes only
      messages whose ``level`` is ``"P3"``.

    Each consumed alert flows through the same pipeline as the webhook
    handler: in-memory deque append → ``AlertPersistence.save_alert()``
    → SSE broadcast callback.

    Usage::

        consumer = AlertKafkaConsumer(
            on_alert=my_persist_and_broadcast_coro,
        )
        await consumer.start()
        # ... server runs ...
        await consumer.stop()
    """

    def __init__(
        self,
        *,
        on_alert: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        brokers: str | None = None,
        topic: str | None = None,
        p2_interval: int | None = None,
        p3_interval: int | None = None,
        max_messages: int | None = None,
        consumer_group: str | None = None,
    ):
        settings = get_settings()
        self.brokers = brokers or settings.otel_kafka_brokers
        self.topic = topic or settings.otel_alert_kafka_topic
        self.p2_interval = (
            p2_interval if p2_interval is not None
            else settings.otel_alert_p2_poll_seconds
        )
        self.p3_interval = (
            p3_interval if p3_interval is not None
            else settings.otel_alert_p3_poll_seconds
        )
        self.max_messages = (
            max_messages if max_messages is not None
            else settings.otel_alert_kafka_max_messages
        )
        self.consumer_group = consumer_group or settings.otel_kafka_consumer_group

        self._on_alert = on_alert

        # Lifecycle state
        self._running = False
        self._p2_task: asyncio.Task | None = None
        self._p3_task: asyncio.Task | None = None
