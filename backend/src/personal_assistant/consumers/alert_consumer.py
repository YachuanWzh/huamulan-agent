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

    def _fetch_alert_messages(self) -> list[str]:
        """Fetch recent messages from the alert Kafka topic as strings.

        Returns decoded UTF-8 strings (the alert messages are JSON,
        not protobuf).  Returns an empty list when Kafka is unreachable
        or no messages are available.
        """
        try:
            from kafka import KafkaConsumer, TopicPartition
        except ImportError:
            logger.error("kafka-python not installed, alert consumer cannot fetch")
            return []

        try:
            broker_list = [b.strip() for b in self.brokers.split(",") if b.strip()]
            consumer = KafkaConsumer(
                bootstrap_servers=broker_list,
                group_id=self.consumer_group,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                consumer_timeout_ms=10_000,
            )

            partitions = consumer.partitions_for_topic(self.topic)
            if not partitions:
                logger.debug("Alert consumer: no partitions for topic %s", self.topic)
                consumer.close()
                return []

            topic_partitions = [
                TopicPartition(self.topic, p) for p in sorted(partitions)
            ]
            consumer.assign(topic_partitions)

            messages: list[str] = []
            for tp in topic_partitions:
                end_offset = consumer.end_offsets([tp]).get(tp, 0)
                start_offset = max(
                    0, end_offset - max(1, self.max_messages // len(topic_partitions))
                )
                if start_offset < end_offset:
                    consumer.seek(tp, start_offset)

            deadline = time.monotonic() + 15.0
            while len(messages) < self.max_messages and time.monotonic() < deadline:
                polled = consumer.poll(
                    timeout_ms=2000,
                    max_records=self.max_messages - len(messages),
                )
                if not polled:
                    break
                for _tp, records in polled.items():
                    for record in records:
                        if record.value:
                            raw = record.value
                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8", errors="replace")
                            messages.append(raw)

            consumer.close()
            return messages

        except Exception:
            logger.exception(
                "Alert consumer: Kafka fetch error for topic %s", self.topic
            )
            return []

    async def _poll_for_level(self, level: str) -> None:
        """Execute one poll cycle for a specific alert level.

        Fetches messages from Kafka, parses each one, filters to
        those matching *level*, and invokes ``on_alert`` for each.
        """
        messages = self._fetch_alert_messages()
        if not messages:
            return

        count = 0
        for raw in messages:
            alert_data = _parse_alert_message(raw)
            if alert_data is None:
                continue
            if alert_data.get("level") != level:
                continue
            count += 1
            if self._on_alert:
                try:
                    await self._on_alert(alert_data)
                except Exception:
                    logger.exception(
                        "Alert consumer: on_alert callback failed for %s",
                        alert_data.get("id"),
                    )

        if count:
            logger.info(
                "Alert consumer: processed %d %s alerts from Kafka topic %s",
                count, level, self.topic,
            )
