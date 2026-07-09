"""Kafka consumer for P2/P3 OTEL alerts — cron-driven batch polling.

P0/P1 alerts arrive via AlertManager webhook (push model) at
``POST /api/otel/alerts``.  P2/P3 alerts flow through the OTel Collector
pipeline and are consumed here from Kafka in OTLP protobuf format.

Architecture::

    fire-p2p3-kafka.py → OTel Collector (:32957) → Kafka (otel-metrics, otlp_proto)
                                                          ↓
    alert_consumer.py ← cron poll ← P2/P3 classification ← metric extraction

Configuration via env vars (see config.py):

- ``OTEL_KAFKA_BROKERS`` — Kafka broker (default: localhost:9092)
- ``OTEL_ALERT_KAFKA_ENABLED`` — enable the alert consumer (default: False)
- ``OTEL_ALERT_KAFKA_TOPIC`` — Kafka topic (default: otel-metrics, matching Collector)
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
    """Parse a JSON AlertManager-style Kafka alert message.

    The primary P2/P3 path consumes OTLP protobuf metrics. This fallback keeps
    compatibility with older dedicated alert topics that stored one alert per
    Kafka record as JSON.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Alert consumer: message is not JSON alert")
        return None

    if not isinstance(obj, dict):
        return None

    labels = obj.get("labels") or {}
    if not isinstance(labels, dict) or not labels:
        logger.warning("Alert consumer: JSON message has no labels, skipping")
        return None

    severity = labels.get("severity", "")
    annotations = obj.get("annotations") or {}
    if not isinstance(annotations, dict):
        annotations = {}

    return {
        "id": uuid4().hex[:12],
        "received_at": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "level": _severity_to_level(severity),
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

# ── Alert classification tables ────────────────────────────────────────

# P2 alert types (trend / early-warning) — severity="info"
_P2_METRIC_ALERTS: dict[str, dict[str, str]] = {
    "http_server_duration_milliseconds": {
        "alertname": "LatencyTrendRising",
        "summary_tpl": "{service_name} P95 latency rising trend detected",
        "description_tpl": (
            "P95 latency histogram indicates rising trend for {service_name}. "
            "May breach SLO if trend continues."
        ),
    },
    "postgresql_tup_fetched": {
        "alertname": "SlowQueryIncrease",
        "summary_tpl": "{service_name} DB query rate surging",
        "description_tpl": (
            "PostgreSQL tuple fetch counter rising for {service_name}. "
            "Current value: {metric_value}. May indicate N+1 query or missing index."
        ),
    },
}

# P3 alert types (resource watermark / audit) — severity="none"
_P3_METRIC_ALERTS: dict[str, dict[str, str]] = {
    "system_cpu_utilization": {
        "alertname": "ResourceWatermarkCPU",
        "summary_tpl": "{service_name} CPU utilization > 85%",
        "description_tpl": (
            "Current CPU: {cpu_pct}% on {service_name}. "
            "Sustained high CPU may indicate busy-wait or noisy neighbor."
        ),
    },
}


def _classify_metric_alert(
    metric_name: str,
    metric_value: float,
    data_point_attrs: dict[str, str],
    resource_attrs: dict[str, str],
) -> dict[str, Any] | None:
    """Classify an OTLP metric data point into a P2 or P3 alert dict.

    Returns ``None`` when the metric doesn't match any known alert pattern.
    """
    service_name = resource_attrs.get("service.name", "unknown")

    # ── system_memory_utilization — dual classification by threshold ──
    if metric_name == "system_memory_utilization":
        mem_pct = metric_value
        if mem_pct >= 0.80:
            # P3: ResourceWatermarkMemory
            return _build_alert(
                level="P3",
                severity="none",
                alertname="ResourceWatermarkMemory",
                service_name=service_name,
                summary=f"{service_name} memory utilization > 85%",
                description=(
                    f"Current memory: {mem_pct*100:.0f}% on {service_name}. "
                    f"Above 85% watermark. Consider scaling up."
                ),
            )
        elif mem_pct >= 0.40:
            # P2: MemoryLeakEarlyWarning
            return _build_alert(
                level="P2",
                severity="info",
                alertname="MemoryLeakEarlyWarning",
                service_name=service_name,
                summary=f"{service_name} memory usage rising slowly",
                description=(
                    f"Memory utilization at {mem_pct*100:.0f}% on {service_name}. "
                    f"Trend suggests slow leak — monitor for escalation."
                ),
            )
        return None

    # ── http_server_duration_milliseconds_count — check for SLO / completeness ──
    if metric_name == "http_server_duration_milliseconds_count":
        status_code = data_point_attrs.get("http.status_code", "")
        has_route = "http.route" in data_point_attrs

        if status_code == "500":
            return _build_alert(
                level="P3",
                severity="none",
                alertname="SLOComplianceDrift",
                service_name=service_name,
                summary=f"{service_name} 30-day availability may drop below 99.9%",
                description=(
                    f"Elevated 5xx errors on {service_name} ({int(metric_value)} errors). "
                    f"Error budget may be burning."
                ),
            )
        if not has_route:
            return _build_alert(
                level="P3",
                severity="none",
                alertname="SpanAttributeCompleteness",
                service_name=service_name,
                summary=f"{service_name} spans missing http.route attribute",
                description=(
                    f"{int(metric_value)} spans from {service_name} lack http.route. "
                    f"This breaks RED metrics grouping."
                ),
            )
        return None

    # ── Known P2 metric ──
    if p2_cfg := _P2_METRIC_ALERTS.get(metric_name):
        return _build_alert(
            level="P2",
            severity="info",
            alertname=p2_cfg["alertname"],
            service_name=service_name,
            summary=p2_cfg["summary_tpl"].format(service_name=service_name),
            description=p2_cfg["description_tpl"].format(
                service_name=service_name,
                metric_value=f"{metric_value:.1f}",
            ),
        )

    # ── Known P3 metric ──
    if p3_cfg := _P3_METRIC_ALERTS.get(metric_name):
        return _build_alert(
            level="P3",
            severity="none",
            alertname=p3_cfg["alertname"],
            service_name=service_name,
            summary=p3_cfg["summary_tpl"].format(service_name=service_name),
            description=p3_cfg["description_tpl"].format(
                service_name=service_name,
                cpu_pct=f"{metric_value*100:.0f}",
            ),
        )

    return None


def _build_alert(
    *,
    level: str,
    severity: str,
    alertname: str,
    service_name: str,
    summary: str,
    description: str,
) -> dict[str, Any]:
    """Build a standard alert dict matching the webhook handler format."""
    return {
        "id": uuid4().hex[:12],
        "received_at": datetime.now(timezone.utc).isoformat(),
        "severity": severity,
        "level": level,
        "service_name": service_name,
        "alert_name": alertname,
        "summary": summary,
        "description": description,
        "starts_at": datetime.now(timezone.utc).isoformat(),
        "status": "firing",
        "rca_status": "pending",
        "rca_thread_id": None,
        "rca_pending_approvals": None,
        "rca_result_text": None,
        "metadata": {},
    }


def _extract_attrs(proto_attrs) -> dict[str, str]:
    """Extract key-value pairs from OTLP protobuf KeyValue list."""
    result: dict[str, str] = {}
    for attr in proto_attrs:
        key = attr.key
        if attr.value.HasField("string_value"):
            result[key] = attr.value.string_value
        elif attr.value.HasField("int_value"):
            result[key] = str(attr.value.int_value)
        elif attr.value.HasField("double_value"):
            result[key] = str(attr.value.double_value)
        elif attr.value.HasField("bool_value"):
            result[key] = str(attr.value.bool_value)
    return result


def _parse_otlp_metrics(raw: bytes) -> list[dict[str, Any]]:
    """Parse an OTLP ``ExportMetricsServiceRequest`` protobuf message into alert dicts.

    Each metric data point that matches a known P2/P3 pattern is converted
    into a standard alert dict.  Unrecognised metrics are silently skipped.

    Returns an empty list for malformed or empty protobuf messages.
    """
    try:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )
    except ImportError:
        logger.error("opentelemetry-proto not installed, cannot parse OTLP metrics")
        return []

    try:
        request = ExportMetricsServiceRequest()
        request.ParseFromString(raw)
    except Exception:
        logger.warning("Alert consumer: failed to parse OTLP protobuf message")
        return []

    alerts: list[dict[str, Any]] = []

    for resource_metric in request.resource_metrics:
        resource_attrs = _extract_attrs(resource_metric.resource.attributes)

        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                metric_name = metric.name

                # ── Gauge data points ──
                if metric.HasField("gauge"):
                    for dp in metric.gauge.data_points:
                        dp_attrs = _extract_attrs(dp.attributes)
                        value = _get_data_point_value(dp, dp_attrs)
                        alert = _classify_metric_alert(
                            metric_name, value, dp_attrs, resource_attrs,
                        )
                        if alert:
                            alerts.append(alert)

                # ── Sum (counter) data points ──
                elif metric.HasField("sum"):
                    for dp in metric.sum.data_points:
                        dp_attrs = _extract_attrs(dp.attributes)
                        value = _get_data_point_value(dp, dp_attrs)
                        alert = _classify_metric_alert(
                            metric_name, value, dp_attrs, resource_attrs,
                        )
                        if alert:
                            alerts.append(alert)

                # ── Histogram data points ──
                elif metric.HasField("histogram"):
                    for dp in metric.histogram.data_points:
                        dp_attrs = _extract_attrs(dp.attributes)
                        # Use histogram sum or count as the "value"
                        value = dp.sum if dp.sum > 0 else dp.count
                        alert = _classify_metric_alert(
                            metric_name, value, dp_attrs, resource_attrs,
                        )
                        if alert:
                            alerts.append(alert)

    return alerts


def _get_data_point_value(dp, dp_attrs: dict[str, str]) -> float:
    """Extract a numeric value from an OTLP data point."""
    if dp.HasField("as_double"):
        return dp.as_double
    if dp.HasField("as_int"):
        return float(dp.as_int)
    return 0.0


# ── AlertKafkaConsumer ────────────────────────────────────────────────


class AlertKafkaConsumer:
    """Cron-driven Kafka consumer for P2/P3 OTEL alerts.

    Two background ``asyncio.Task`` loops poll a shared Kafka topic at
    independently configurable intervals:

    - **P2 loop**: polls every *p2_interval* seconds, processes only
      messages classified as ``"P2"``.
    - **P3 loop**: polls every *p3_interval* seconds, processes only
      messages classified as ``"P3"``.

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

    def _fetch_alert_messages(self) -> list[bytes]:
        """Fetch recent OTLP protobuf messages from the Kafka topic.

        Returns raw protobuf bytes (OTLP ``ExportMetricsServiceRequest``).
        Returns an empty list when Kafka is unreachable or no messages
        are available.
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
                logger.warning(
                    "Alert consumer: no partitions for topic %s (brokers=%s) — "
                    "topic may not exist or broker unreachable",
                    self.topic, self.brokers,
                )
                consumer.close()
                return []

            topic_partitions = [
                TopicPartition(self.topic, p) for p in sorted(partitions)
            ]
            consumer.assign(topic_partitions)

            messages: list[bytes] = []
            for tp in topic_partitions:
                end_offset = consumer.end_offsets([tp]).get(tp, 0)
                start_offset = max(
                    0, end_offset - max(1, self.max_messages // len(topic_partitions))
                )
                logger.debug(
                    "Alert consumer: topic=%s partition=%s end_offset=%d start_offset=%d",
                    self.topic, tp.partition, end_offset, start_offset,
                )
                if start_offset < end_offset:
                    consumer.seek(tp, start_offset)
                else:
                    logger.info(
                        "Alert consumer: topic=%s partition=%s has no new messages "
                        "(end_offset=%d)",
                        self.topic, tp.partition, end_offset,
                    )

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
                            messages.append(record.value)

            consumer.close()
            if messages:
                logger.info(
                    "Alert consumer: fetched %d raw messages from topic %s",
                    len(messages), self.topic,
                )
            return messages

        except Exception:
            logger.exception(
                "Alert consumer: Kafka fetch error for topic %s", self.topic
            )
            return []

    async def _poll_for_level(self, level: str) -> None:
        """Execute one poll cycle for a specific alert level.

        Fetches OTLP protobuf messages from Kafka, parses each one into
        alert dicts, filters to those matching *level*, and invokes
        ``on_alert`` for each.
        """
        raw_messages = self._fetch_alert_messages()
        if not raw_messages:
            return

        count = 0
        for raw in raw_messages:
            alerts = _parse_otlp_metrics(raw)
            if not alerts:
                json_alert = _parse_alert_message(raw)
                alerts = [json_alert] if json_alert else []
            for alert_data in alerts:
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
        else:
            logger.info(
                "Alert consumer: polled topic=%s level=%s — %d raw messages "
                "fetched but none classified as %s",
                self.topic, level, len(raw_messages), level,
            )

    async def _run_loop(self, level: str, interval: int) -> None:
        """Background loop: poll for *level* alerts every *interval* seconds."""
        while self._running:
            try:
                await self._poll_for_level(level)
            except Exception:
                logger.exception(
                    "Alert consumer: %s poll cycle failed, will retry", level
                )
            # Sleep in 1-second chunks so shutdown is responsive
            for _ in range(max(1, interval)):
                if not self._running:
                    break
                await asyncio.sleep(1)

    async def start(self) -> None:
        """Start the background poll loops for P2 and P3.

        Safe to call multiple times — subsequent calls are no-ops if
        already running.
        """
        if self._running:
            return
        self._running = True

        # Snappy health check — Kafka messages from the OTel Collector are
        # snappy-compressed; if the decompression library is missing every
        # poll cycle will fail silently.
        try:
            import snappy  # noqa: F401
        except ImportError:
            logger.warning(
                "python-snappy is NOT installed — Kafka messages from the "
                "OTel Collector are snappy-compressed and will be unreadable. "
                "Install with: pip install python-snappy"
            )

        self._p2_task = asyncio.create_task(
            self._run_loop("P2", self.p2_interval)
        )
        self._p3_task = asyncio.create_task(
            self._run_loop("P3", self.p3_interval)
        )
        logger.info(
            "Alert consumer started: topic=%s P2_every=%ds P3_every=%ds",
            self.topic, self.p2_interval, self.p3_interval,
        )

    async def stop(self) -> None:
        """Stop the background poll loops.

        Safe to call multiple times — subsequent calls are no-ops if
        already stopped.
        """
        if not self._running:
            return
        self._running = False

        for task in (self._p2_task, self._p3_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._p2_task = None
        self._p3_task = None
        logger.info("Alert consumer stopped")
