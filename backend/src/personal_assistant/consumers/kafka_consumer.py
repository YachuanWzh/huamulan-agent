"""Kafka consumer for OTEL telemetry push — batch ingestion and analysis.

Reads OTLP JSON spans/metrics/logs from Kafka topics exported by the
OpenTelemetry Demo's OTel Collector, converts them to the apm.py analysis
format, and builds ObservabilitySnapshot instances for patrol/audit agents.

Usage as cron script::

    python -m personal_assistant.consumers.kafka_consumer \
        --topic otel-spans --window 5m

Configuration via env vars (see config.py):

- ``OTEL_KAFKA_BROKERS`` — Kafka broker address (default: localhost:9092)
- ``OTEL_KAFKA_TOPIC_SPANS`` — spans topic (default: otel-spans)
- ``OTEL_KAFKA_CONSUMER_GROUP`` — consumer group ID (default: langgraph-claw)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from personal_assistant.apm import (
    FrontendRumEvent,
    ObservabilitySnapshot,
    build_observability_snapshot,
    from_jaeger_trace,
    from_jaeger_trace_to_logs,
)
from personal_assistant.config import get_settings

logger = logging.getLogger(__name__)

# ── OTLP JSON → Jaeger-compatible format conversion ──────────────────


def _otlp_attribute_value(attr: dict[str, Any]) -> Any:
    """Extract the typed value from an OTLP JSON attribute dict."""
    value = attr.get("value", {}) if isinstance(attr, dict) else {}
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if key in value:
            raw = value[key]
            if key == "intValue":
                return int(raw)
            if key == "doubleValue":
                return float(raw)
            if key == "boolValue":
                return raw is True or raw == "true"
            return str(raw)
    return None


def _otlp_nanos_to_ms(nanos: str | int) -> int:
    """Convert OTLP nanosecond timestamp to milliseconds."""
    return int(int(nanos) / 1_000_000)


def _otlp_nanos_to_us(nanos: str | int) -> int:
    """Convert OTLP nanosecond timestamp to microseconds (Jaeger format)."""
    return int(int(nanos) / 1_000)


def _otlp_span_duration_us(start_ns: str | int, end_ns: str | int) -> int:
    """Calculate span duration in microseconds from OTLP nano timestamps."""
    return _otlp_nanos_to_us(int(end_ns) - int(start_ns))


def otlp_spans_to_jaeger_trace(
    otlp_batch: dict[str, Any],
    *,
    trace_index: int = 0,
) -> dict[str, Any]:
    """Convert an OTLP JSON span batch into a Jaeger-compatible trace dict.

    The OTel Collector kafka exporter (``encoding: otlp_json``) produces
    ``ExportTraceServiceRequest`` messages. This function converts the first
    trace in the batch to the dict format expected by ``from_jaeger_trace()``
    and ``from_jaeger_trace_to_logs()``.

    Args:
        otlp_batch: Parsed JSON of an OTLP ExportTraceServiceRequest.
        trace_index: Which trace to extract when batch has multiple traces.

    Returns:
        A dict with ``traceID`` and ``spans`` keys, compatible with
        :func:`personal_assistant.apm.from_jaeger_trace`.
    """
    resource_spans = otlp_batch.get("resourceSpans", []) or []
    if not resource_spans:
        return {"traceID": "", "spans": []}

    # Collect all spans, grouped by trace ID
    spans_by_trace: dict[str, list[dict[str, Any]]] = {}
    service_name = ""

    for rs in resource_spans:
        # Extract service name from resource attributes
        resource = rs.get("resource", {}) if isinstance(rs, dict) else {}
        if isinstance(resource, dict):
            for attr in resource.get("attributes", []) or []:
                if isinstance(attr, dict) and attr.get("key") == "service.name":
                    service_name = str(_otlp_attribute_value(attr) or "")

        for ss in rs.get("scopeSpans", []) or []:
            scope = ss.get("scope", {}) if isinstance(ss, dict) else {}
            scope_name = scope.get("name", "") if isinstance(scope, dict) else ""

            for span in ss.get("spans", []) or []:
                if not isinstance(span, dict):
                    continue
                trace_id = str(span.get("traceId", ""))
                start_ns = span.get("startTimeUnixNano", "0")
                end_ns = span.get("endTimeUnixNano", "0")
                status = span.get("status") or {}
                if not isinstance(status, dict):
                    status = {}

                # Build Jaeger-compatible tags from OTLP attributes
                tags: list[dict[str, Any]] = []
                for attr in span.get("attributes", []) or []:
                    if not isinstance(attr, dict):
                        continue
                    key = attr.get("key", "")
                    value = _otlp_attribute_value(attr)
                    if key and value is not None:
                        tags.append({"key": key, "value": value})

                # Add scope as a synthetic tag
                if scope_name:
                    tags.append({"key": "otel.scope.name", "value": scope_name})

                # Add service name
                if service_name:
                    tags.append({"key": "service.name", "value": service_name})

                # Map OTLP status code to error tag
                status_code = status.get("code", 0)
                if status_code == 2:  # STATUS_CODE_ERROR
                    tags.append({"key": "error", "value": True})

                jaeger_span: dict[str, Any] = {
                    "traceID": trace_id,
                    "spanID": str(span.get("spanId", "")),
                    "operationName": str(span.get("name", "")),
                    "startTime": _otlp_nanos_to_ms(start_ns),
                    "duration": _otlp_span_duration_us(start_ns, end_ns),
                    "tags": tags,
                }
                spans_by_trace.setdefault(trace_id, []).append(jaeger_span)

    # Return the requested trace (or first available)
    trace_ids = list(spans_by_trace.keys())
    if not trace_ids:
        return {"traceID": "", "spans": []}

    selected = trace_ids[min(trace_index, len(trace_ids) - 1)]
    return {
        "traceID": selected,
        "spans": spans_by_trace[selected],
    }


# ── Kafka consumer ──────────────────────────────────────────────────


@dataclass
class OtelKafkaConsumer:
    """Batch consumer that reads OTLP telemetry from Kafka and runs APM analysis.

    Designed for cron-driven usage::

        consumer = OtelKafkaConsumer()
        snapshots = consumer.consume_and_analyze(window="5m")
        for snapshot in snapshots:
            if snapshot.anomalies:
                # Dispatch to patrol_agent or audit_agent
                ...
    """

    brokers: str = ""
    topic_spans: str = "otel-spans"
    topic_metrics: str = "otel-metrics"
    topic_logs: str = "otel-logs"
    consumer_group: str = "langgraph-claw"

    def __post_init__(self) -> None:
        if not self.brokers:
            settings = get_settings()
            self.brokers = settings.otel_kafka_brokers
            self.topic_spans = settings.otel_kafka_topic_spans
            self.topic_metrics = settings.otel_kafka_topic_metrics
            self.topic_logs = settings.otel_kafka_topic_logs
            self.consumer_group = settings.otel_kafka_consumer_group

    def consume_and_analyze(
        self,
        *,
        window: str = "5m",
        topic: str | None = None,
        limit: int = 100,
    ) -> list[ObservabilitySnapshot]:
        """Consume messages from Kafka and build observability snapshots.

        Args:
            window: Time window to consume (e.g. ``"5m"``, ``"30m"``).
            topic: Kafka topic to consume (default: spans topic from config).
            limit: Max number of messages to consume in this batch.

        Returns:
            List of :class:`ObservabilitySnapshot` instances, one per trace.
        """
        topic_name = topic or self.topic_spans
        window_seconds = _parse_window(window)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)

        snapshots: list[ObservabilitySnapshot] = []
        messages = self._fetch_messages(topic_name, limit=limit)

        for msg in messages:
            try:
                otlp_batch = json.loads(msg)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse Kafka message as JSON, skipping")
                continue

            # Convert OTLP → Jaeger format → FrontendRumEvent + ExecutionLog
            trace = otlp_spans_to_jaeger_trace(otlp_batch)
            if not trace.get("spans"):
                continue

            rum_events = from_jaeger_trace(trace)
            execution_logs = from_jaeger_trace_to_logs(trace)

            # Filter to events within the time window
            rum_events = _filter_events_by_time(rum_events, cutoff)
            if not rum_events and not execution_logs:
                continue

            snapshot = build_observability_snapshot(rum_events, execution_logs)
            snapshots.append(snapshot)

        return snapshots

    def _fetch_messages(self, topic: str, *, limit: int = 100) -> list[str]:
        """Fetch recent messages from a Kafka topic.

        Uses kafka-python KafkaConsumer to read messages from the end
        of the topic (latest offsets within the limit).
        """
        try:
            from kafka import KafkaConsumer, TopicPartition
        except ImportError:
            logger.error(
                "kafka-python is not installed. "
                "Install it with: pip install kafka-python"
            )
            return []

        try:
            broker_list = [b.strip() for b in self.brokers.split(",") if b.strip()]
            consumer = KafkaConsumer(
                bootstrap_servers=broker_list,
                group_id=self.consumer_group,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                consumer_timeout_ms=10_000,
                value_deserializer=lambda m: m.decode("utf-8") if m else "",
            )

            partitions = consumer.partitions_for_topic(topic)
            if not partitions:
                logger.warning("No partitions found for topic %s", topic)
                consumer.close()
                return []

            topic_partitions = [
                TopicPartition(topic, p) for p in sorted(partitions)
            ]
            consumer.assign(topic_partitions)

            messages: list[str] = []
            for tp in topic_partitions:
                end_offset = consumer.end_offsets([tp]).get(tp, 0)
                start_offset = max(0, end_offset - max(1, limit // len(topic_partitions)))
                if start_offset < end_offset:
                    consumer.seek(tp, start_offset)

            deadline = time.monotonic() + 15.0
            while len(messages) < limit and time.monotonic() < deadline:
                polled = consumer.poll(timeout_ms=2000, max_records=limit - len(messages))
                if not polled:
                    break
                for _tp, records in polled.items():
                    for record in records:
                        if record.value:
                            messages.append(record.value)

            consumer.close()
            return messages

        except Exception as exc:
            logger.error("Kafka consumer error: %s", exc, exc_info=True)
            return []


def _parse_window(window: str) -> int:
    """Parse a time window string like ``"5m"``, ``"30m"``, ``"1h"`` into seconds."""
    window = window.strip().lower()
    if window.endswith("s"):
        return int(window[:-1])
    if window.endswith("m"):
        return int(window[:-1]) * 60
    if window.endswith("h"):
        return int(window[:-1]) * 3600
    return 300  # default 5 minutes


def _filter_events_by_time(
    events: list[FrontendRumEvent],
    cutoff: datetime,
) -> list[FrontendRumEvent]:
    """Filter RUM events to those occurring after the cutoff time."""
    filtered: list[FrontendRumEvent] = []
    for event in events:
        if event.timestamp:
            try:
                ts_ms = int(event.timestamp)
                event_time = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                if event_time >= cutoff:
                    filtered.append(event)
                continue
            except (ValueError, OSError):
                pass
        filtered.append(event)
    return filtered


# ── CLI ─────────────────────────────────────────────────────────────


def main() -> int:
    """Cron-friendly CLI entry point for batch telemetry consumption."""
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="OTEL Kafka consumer — batch telemetry analysis"
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Kafka topic to consume (default: spans topic from config)",
    )
    parser.add_argument(
        "--window",
        default="5m",
        help="Time window (e.g. 5m, 30m, 1h, default: 5m)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max messages to consume (default: 100)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write JSON snapshots to file instead of stdout",
    )

    args = parser.parse_args()

    consumer = OtelKafkaConsumer()
    snapshots = consumer.consume_and_analyze(
        window=args.window,
        topic=args.topic,
        limit=args.limit,
    )

    output_data = [s.model_dump(mode="json") for s in snapshots]
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(output_data, fh, ensure_ascii=False, indent=2)
    else:
        json.dump(output_data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    anomaly_count = sum(1 for s in snapshots if s.anomalies)
    logger.info(
        "Consumed %d traces, %d snapshots, %d with anomalies (window=%s)",
        len(snapshots),
        len(snapshots),
        anomaly_count,
        args.window,
    )
    return 0 if snapshots else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
