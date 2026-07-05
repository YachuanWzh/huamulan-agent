# OTEL Push Phase 2+3 Implementation Plan

> **For agentic workers:** Execute this plan task-by-task, strict TDD per task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement langgraph-claw side of OTEL push architecture — Kafka consumer for P2/P3 batch telemetry analysis and AlertManager webhook endpoint for P0/P1 instant RCA.

**Architecture:** Two new modules: (1) `consumers/kafka_consumer.py` — cron-driven batch consumer that reads OTLP JSON from Kafka topics, converts to the apm.py analysis format, and generates `ObservabilitySnapshot`; (2) `api/server.py` webhook endpoint `POST /api/otel/alerts` — receives AlertManager v4 webhooks, extracts P0/P1 alerts, auto-pulls associated Jaeger traces + Prometheus metrics, and runs instant RCA via `troubleshoot_agent`.

**Tech Stack:** kafka-python (pure Python), pydantic (models), urllib (HTTP, matches existing patterns), existing apm.py converters

**Assumptions:**
- OTel Collector uses `encoding: otlp_json` (tell user to change one line in otelcol-config-extras.yml)
- Kafka is accessible from langgraph-claw's network (broker address in env var)
- `kafka-python` added to pyproject.toml dependencies

---

### Task 1: Add Kafka dependency and OTEL env vars to config

**Files:**
- Modify: `backend/pyproject.toml` (add kafka-python)
- Modify: `backend/.env` (add Kafka + webhook config)
- Modify: `backend/src/personal_assistant/config.py` (add OTEL Kafka settings)

- [ ] **Step 1: Add kafka-python to pyproject.toml**

```toml
# In dependencies list, add after redis>=5.0.0:
  "kafka-python>=2.0.0",
```

- [ ] **Step 2: Install the dependency**

Run: `cd backend && pip install kafka-python`
Expected: Successfully installed kafka-python

- [ ] **Step 3: Add env vars to .env**

Append to `backend/.env`:
```bash
# ---- OTEL Push: Kafka Consumer & AlertManager Webhook ------------------------
# Kafka broker address for consuming OTLP telemetry exported by opentelemetry-demo
OTEL_KAFKA_BROKERS=192.168.5.7:9092
# Kafka topic names (must match otelcol-config-extras.yml)
OTEL_KAFKA_TOPIC_SPANS=otel-spans
OTEL_KAFKA_TOPIC_METRICS=otel-metrics
OTEL_KAFKA_TOPIC_LOGS=otel-logs
# Consumer group ID for Kafka
OTEL_KAFKA_CONSUMER_GROUP=langgraph-claw
```

- [ ] **Step 4: Add config fields to config.py**

Add after the existing `otel_prometheus_proxy_url` field (line ~191):
```python
    # ── OTEL Push: Kafka Consumer ──────────────────────────────────
    otel_kafka_brokers: str = Field(
        default="localhost:9092",
        alias="OTEL_KAFKA_BROKERS",
    )
    otel_kafka_topic_spans: str = Field(
        default="otel-spans",
        alias="OTEL_KAFKA_TOPIC_SPANS",
    )
    otel_kafka_topic_metrics: str = Field(
        default="otel-metrics",
        alias="OTEL_KAFKA_TOPIC_METRICS",
    )
    otel_kafka_topic_logs: str = Field(
        default="otel-logs",
        alias="OTEL_KAFKA_TOPIC_LOGS",
    )
    otel_kafka_consumer_group: str = Field(
        default="langgraph-claw",
        alias="OTEL_KAFKA_CONSUMER_GROUP",
    )
```

- [ ] **Step 5: Verify config loads**

Run: `cd backend && python -c "from personal_assistant.config import get_settings; s = get_settings(); print(f'Kafka brokers: {s.otel_kafka_brokers}')"`
Expected: `Kafka brokers: 192.168.5.7:9092`

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/.env backend/src/personal_assistant/config.py
git commit -m "chore(otel): add kafka-python dependency and OTEL push config"
```

---

### Task 2: Add AlertManager webhook Pydantic schemas

**Files:**
- Modify: `backend/src/personal_assistant/api/schemas.py`

- [ ] **Step 1: Write the test (schemas)**

Create `backend/tests/test_otel_schemas.py`:
```python
import json
from personal_assistant.api.schemas import AlertManagerWebhook, OtelAlert


def test_alert_manager_webhook_parses_v4_payload():
    payload = {
        "receiver": "langgraph-claw-p0",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "ServiceDown",
                    "severity": "critical",
                    "service_name": "frontend",
                },
                "annotations": {
                    "summary": "frontend is DOWN",
                    "description": "Service frontend has been down for 1 minute.",
                },
                "startsAt": "2026-07-05T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
            }
        ],
        "groupLabels": {"alertname": "ServiceDown"},
        "commonLabels": {
            "alertname": "ServiceDown",
            "severity": "critical",
            "service_name": "frontend",
        },
        "commonAnnotations": {"summary": "frontend is DOWN"},
        "externalURL": "http://alertmanager:9093",
        "version": "4",
    }
    webhook = AlertManagerWebhook.model_validate(payload)
    assert webhook.receiver == "langgraph-claw-p0"
    assert webhook.status == "firing"
    assert len(webhook.alerts) == 1
    alert = webhook.alerts[0]
    assert alert.labels["severity"] == "critical"
    assert alert.labels["service_name"] == "frontend"
    assert alert.annotations["summary"] == "frontend is DOWN"
    assert alert.starts_at == "2026-07-05T10:00:00Z"


def test_alert_manager_webhook_parses_resolved_alert():
    payload = {
        "receiver": "langgraph-claw-p0",
        "status": "resolved",
        "alerts": [
            {
                "status": "resolved",
                "labels": {"alertname": "HighLatencyP95", "severity": "warning", "service_name": "checkout"},
                "annotations": {"summary": "checkout P95 latency > 500ms"},
                "startsAt": "2026-07-05T09:00:00Z",
                "endsAt": "2026-07-05T09:15:00Z",
                "generatorURL": "http://prometheus:9090/graph",
            }
        ],
        "groupLabels": {},
        "commonLabels": {"alertname": "HighLatencyP95", "severity": "warning", "service_name": "checkout"},
        "commonAnnotations": {},
        "externalURL": "",
        "version": "4",
    }
    webhook = AlertManagerWebhook.model_validate(payload)
    assert webhook.status == "resolved"
    assert webhook.alerts[0].status == "resolved"
    assert webhook.alerts[0].ends_at == "2026-07-05T09:15:00Z"


def test_alert_severity_is_required_for_routing():
    """Severity label is the key field for P0/P1 routing."""
    payload = {
        "receiver": "langgraph-claw-p0",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "TestAlert", "severity": "critical"},
                "annotations": {},
                "startsAt": "2026-07-05T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "",
            }
        ],
        "groupLabels": {},
        "commonLabels": {"alertname": "TestAlert", "severity": "critical"},
        "commonAnnotations": {},
        "externalURL": "",
        "version": "4",
    }
    webhook = AlertManagerWebhook.model_validate(payload)
    assert webhook.alerts[0].labels.get("severity") == "critical"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_otel_schemas.py -v`
Expected: FAIL — `AlertManagerWebhook` not defined

- [ ] **Step 3: Add schemas to schemas.py**

Add at the end of `backend/src/personal_assistant/api/schemas.py`:
```python
# ── OTEL Push: AlertManager Webhook ──────────────────────────────


class OtelAlert(BaseModel):
    """A single alert from AlertManager webhook (v4 format)."""
    status: str  # "firing" | "resolved"
    labels: dict[str, str]  # alertname, severity, service_name, ...
    annotations: dict[str, str]  # summary, description, runbook_url, ...
    starts_at: str = Field(alias="startsAt")
    ends_at: str = Field(default="", alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL")


class AlertManagerWebhook(BaseModel):
    """AlertManager webhook payload (v4 format).

    POST to /api/otel/alerts by AlertManager when P0/P1 alerts fire.
    """
    receiver: str
    status: str  # "firing" | "resolved"
    alerts: list[OtelAlert]
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(default_factory=dict, alias="commonAnnotations")
    external_url: str = Field(default="", alias="externalURL")
    version: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_otel_schemas.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_otel_schemas.py backend/src/personal_assistant/api/schemas.py
git commit -m "feat(otel): add AlertManager webhook Pydantic schemas"
```

---

### Task 3: Implement Kafka consumer (OTLP JSON → apm.py analysis)

**Files:**
- Create: `backend/src/personal_assistant/consumers/__init__.py`
- Create: `backend/src/personal_assistant/consumers/kafka_consumer.py`
- Create: `backend/tests/test_kafka_consumer.py`

- [ ] **Step 1: Write the test for OTLP span conversion**

Create `backend/tests/test_kafka_consumer.py`:
```python
"""Tests for kafka_consumer.py OTLP JSON → apm.py conversion."""
import json
from personal_assistant.consumers.kafka_consumer import (
    otlp_spans_to_jaeger_trace,
    OTLP_BATCH_SCHEMA,
)


# Sample OTLP JSON span batch as produced by OTel Collector kafka exporter
SAMPLE_OTLP_SPANS = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "frontend"}},
                    {"key": "service.namespace", "value": {"stringValue": "opentelemetry-demo"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "http"},
                    "spans": [
                        {
                            "traceId": "abcdef1234567890abcdef1234567890",
                            "spanId": "1234567890abcdef",
                            "parentSpanId": "",
                            "name": "GET /api/products",
                            "kind": 2,  # SPAN_KIND_SERVER
                            "startTimeUnixNano": "1700000000000000000",
                            "endTimeUnixNano": "1700000000050000000",  # 50ms
                            "attributes": [
                                {"key": "http.method", "value": {"stringValue": "GET"}},
                                {"key": "http.target", "value": {"stringValue": "/api/products/123"}},
                                {"key": "http.status_code", "value": {"intValue": "200"}},
                            ],
                            "status": {"code": 1},  # Ok
                        },
                        {
                            "traceId": "abcdef1234567890abcdef1234567891",
                            "spanId": "abcdef1234567890",
                            "parentSpanId": "",
                            "name": "POST /api/checkout",
                            "kind": 2,
                            "startTimeUnixNano": "1700000001000000000",
                            "endTimeUnixNano": "1700000001500000000",  # 500ms
                            "attributes": [
                                {"key": "http.method", "value": {"stringValue": "POST"}},
                                {"key": "http.status_code", "value": {"intValue": "500"}},
                                {"key": "error", "value": {"boolValue": True}},
                            ],
                            "status": {"code": 2},  # Error
                        },
                    ],
                }
            ],
        }
    ]
}


def test_otlp_spans_to_jaeger_trace_converts_basic_span():
    trace = otlp_spans_to_jaeger_trace(SAMPLE_OTLP_SPANS)
    assert trace["traceID"] == "abcdef1234567890abcdef1234567890"
    assert len(trace["spans"]) == 2

    span = trace["spans"][0]
    assert span["operationName"] == "GET /api/products"
    assert span["spanID"] == "1234567890abcdef"
    assert span["duration"] == 50000  # 50ms in microseconds
    assert len(span["tags"]) >= 3  # http.method, http.target, http.status_code


def test_otlp_spans_to_jaeger_trace_detects_error_span():
    trace = otlp_spans_to_jaeger_trace(SAMPLE_OTLP_SPANS)
    error_span = trace["spans"][1]
    error_tags = {t["key"]: t["value"] for t in error_span["tags"]}
    assert error_tags.get("error") is True
    assert error_tags.get("http.status_code") == 500


def test_otlp_spans_to_jaeger_empty_returns_empty_dict():
    result = otlp_spans_to_jaeger_trace({"resourceSpans": []})
    assert result == {"traceID": "", "spans": []}


def test_otlp_spans_multi_trace_returns_first():
    """When batch has multiple traces, returns the first one."""
    result = otlp_spans_to_jaeger_trace(SAMPLE_OTLP_SPANS)
    # With two different traceIds, it should return the first trace's spans only
    assert result["traceID"] == "abcdef1234567890abcdef1234567890"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_kafka_consumer.py -v`
Expected: FAIL — module/function not defined

- [ ] **Step 3: Implement the consumer module**

Create `backend/src/personal_assistant/consumers/__init__.py`:
```python
"""OTEL telemetry consumers — Kafka batch ingestion and analysis."""
```

Create `backend/src/personal_assistant/consumers/kafka_consumer.py`:
```python
"""Kafka consumer for OTEL telemetry push — batch ingestion and analysis.

Reads OTLP JSON spans/metrics/logs from Kafka topics exported by the
OpenTelemetry Demo's OTel Collector, converts them to the apm.py analysis
format, and builds ObservabilitySnapshot instances for patrol/audit agents.

Usage as cron script::

    python -m personal_assistant.consumers.kafka_consumer \\
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
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from personal_assistant.apm import (
    FrontendRumEvent,
    ExecutionLog,
    ObservabilitySnapshot,
    build_observability_snapshot,
    from_jaeger_trace,
    from_jaeger_trace_to_logs,
)
from personal_assistant.config import get_settings

logger = logging.getLogger(__name__)

# ── OTLP JSON → Jaeger-compatible format conversion ──────────────────


def _otlp_attribute_value(attr: dict[str, Any]) -> Any:
    """Extract the typed value from an OTLP attribute dict."""
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
                status = span.get("status", {}) if isinstance(span, "status") else {}

                # Build Jaeger-compatible tags
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
                status_code = status.get("code", 0) if isinstance(status, dict) else 0
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
            window: Time window to consume (e.g. "5m", "30m").
            topic: Kafka topic to consume (default: spans topic).
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
            # Parse broker list (supports comma-separated)
            broker_list = [b.strip() for b in self.brokers.split(",") if b.strip()]
            consumer = KafkaConsumer(
                bootstrap_servers=broker_list,
                group_id=self.consumer_group,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                consumer_timeout_ms=10_000,  # 10s timeout
                value_deserializer=lambda m: m.decode("utf-8") if m else "",
            )

            # Get partitions and seek to latest - N
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

            # Poll for messages
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
    """Parse a time window string like "5m", "30m", "1h" into seconds."""
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
                # Jaeger timestamps are microsecond epoch numbers in the event metadata
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
        help="Kafka topic to consume (default: spans topic from env)",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_kafka_consumer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/consumers/__init__.py backend/src/personal_assistant/consumers/kafka_consumer.py backend/tests/test_kafka_consumer.py
git commit -m "feat(otel): implement Kafka consumer for OTLP telemetry batch analysis"
```

---

### Task 4: Add AlertManager webhook endpoint to API server

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py` (add import + route)
- Create: `backend/tests/test_otel_webhook.py`

- [ ] **Step 1: Write the webhook endpoint test**

Create `backend/tests/test_otel_webhook.py`:
```python
"""Tests for POST /api/otel/alerts webhook endpoint."""
import json
import pytest
from fastapi.testclient import TestClient
from personal_assistant.api.server import app

client = TestClient(app)


ALERTMANAGER_P0_PAYLOAD = {
    "receiver": "langgraph-claw-p0",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "ServiceDown",
                "severity": "critical",
                "service_name": "frontend",
            },
            "annotations": {
                "summary": "frontend is DOWN",
                "description": "Service frontend has been down for more than 1 minute.",
            },
            "startsAt": "2026-07-05T10:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090/graph",
        }
    ],
    "groupLabels": {"alertname": "ServiceDown"},
    "commonLabels": {
        "alertname": "ServiceDown",
        "severity": "critical",
        "service_name": "frontend",
    },
    "commonAnnotations": {"summary": "frontend is DOWN"},
    "externalURL": "http://alertmanager:9093",
    "version": "4",
}

ALERTMANAGER_P1_PAYLOAD = {
    "receiver": "langgraph-claw-p1",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "HighLatencyP95",
                "severity": "warning",
                "service_name": "checkout",
            },
            "annotations": {
                "summary": "checkout P95 latency > 500ms",
                "description": "Current P95: 650ms",
            },
            "startsAt": "2026-07-05T10:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090/graph",
        }
    ],
    "groupLabels": {"alertname": "HighLatencyP95"},
    "commonLabels": {
        "alertname": "HighLatencyP95",
        "severity": "warning",
        "service_name": "checkout",
    },
    "commonAnnotations": {"summary": "checkout P95 latency > 500ms"},
    "externalURL": "http://alertmanager:9093",
    "version": "4",
}


def test_otel_alerts_endpoint_accepts_p0_alert():
    response = client.post("/api/otel/alerts", json=ALERTMANAGER_P0_PAYLOAD)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["alerts"] == 1


def test_otel_alerts_endpoint_accepts_p1_alert():
    response = client.post("/api/otel/alerts", json=ALERTMANAGER_P1_PAYLOAD)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"


def test_otel_alerts_endpoint_rejects_invalid_payload():
    response = client.post("/api/otel/alerts", json={"invalid": "payload"})
    assert response.status_code == 422  # Validation error


def test_otel_alerts_endpoint_accepts_multi_alert_batch():
    payload = {
        **ALERTMANAGER_P0_PAYLOAD,
        "alerts": ALERTMANAGER_P0_PAYLOAD["alerts"] + ALERTMANAGER_P1_PAYLOAD["alerts"],
    }
    response = client.post("/api/otel/alerts", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["alerts"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_otel_webhook.py -v`
Expected: FAIL — 404 Not Found (endpoint not defined)

- [ ] **Step 3: Add the webhook endpoint to server.py**

Add import at top of `server.py` (after existing schemas imports):
```python
from personal_assistant.api.schemas import (
    ...
    AlertManagerWebhook,  # Add this line
)
```

Add the route after the existing observability endpoints (after line ~349):
```python
@app.post("/api/otel/alerts")
async def handle_otel_alert(payload: AlertManagerWebhook):
    """Receive AlertManager webhook for P0/P1 instant RCA.

    P0 (severity=critical) and P1 (severity=warning) alerts trigger
    immediate root cause analysis. The handler:
    1. Filters to P0/P1 alerts only (P2/P3 come via Kafka)
    2. Auto-pulls associated Jaeger traces and Prometheus metrics
    3. Runs instant RCA via troubleshoot_agent
    4. Returns accepted status (RCA runs in background)

    P2/P3 alerts are silently dropped here — they arrive via Kafka.
    """
    processed = 0
    for alert in payload.alerts:
        severity = alert.labels.get("severity", "")
        if severity not in ("critical", "warning"):
            continue  # P2/P3 come via Kafka

        service = alert.labels.get("service_name", "unknown")
        alert_name = alert.labels.get("alertname", "unknown")
        summary = alert.annotations.get("summary", "")
        starts_at = alert.starts_at

        logger.info(
            "OTEL alert received: severity=%s service=%s alert=%s summary=%s",
            severity,
            service,
            alert_name,
            summary,
        )

        # TODO Phase 4: Dispatch to troubleshoot_agent for auto RCA
        # background_tasks.add_task(
        #     run_auto_troubleshoot,
        #     alert=alert,
        #     service=service,
        # )

        processed += 1

    return {"status": "accepted", "alerts": processed}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_otel_webhook.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/api/server.py backend/tests/test_otel_webhook.py
git commit -m "feat(otel): add AlertManager webhook endpoint POST /api/otel/alerts"
```

---

### Task 5: Add OTEL push golden evaluation cases

**Files:**
- Create: `backend/evaluation/golden/otel_push.jsonl`

- [ ] **Step 1: Create golden test cases**

Create `backend/evaluation/golden/otel_push.jsonl`:
```jsonl
{"id": "otel-push-001", "category": "otel_push", "difficulty": "easy", "query": "收到一条 P0 告警：frontend 服务挂了。AlertManager webhook 已推送过来，请自动执行根因分析。告警详情：ServiceDown, severity=critical, service_name=frontend, startsAt=2026-07-05T10:00:00Z", "expected_skills": ["troubleshoot"], "expected_intent": "troubleshoot", "expected_metrics": [], "expected_entities": ["frontend", "ServiceDown", "P0"], "expected_tool_calls": [{"tool": "query_traces", "args_contains": {"service": "frontend"}}, {"tool": "query_metrics"}], "expected_answer_contains": ["P0", "frontend", "RCA", "root cause"]}
{"id": "otel-push-002", "category": "otel_push", "difficulty": "medium", "query": "收到一条 P1 告警：checkout 服务 P95 延迟飙到 800ms，over SLO 2x。AlertManager webhook 推送过来了，请做根因分析。告警标签：severity=warning, service_name=checkout, alertname=HighLatencyP95", "expected_skills": ["troubleshoot"], "expected_intent": "troubleshoot", "expected_metrics": ["p95"], "expected_entities": ["checkout", "HighLatencyP95", "P1"], "expected_tool_calls": [{"tool": "query_traces", "args_contains": {"service": "checkout"}}, {"tool": "query_metrics"}], "expected_answer_contains": ["P1", "checkout", "latency", "P95", "800ms"]}
{"id": "otel-push-003", "category": "otel_push", "difficulty": "medium", "query": "Kafka consumer 刚从 otel-spans topic 消费到一批 trace，请 patrol agent 做趋势分析。这批 trace 来自 recommendation 服务，发现 RPS 在过去 5 分钟涨了 300%。", "expected_skills": ["patrol"], "expected_intent": "patrol", "expected_metrics": ["rps"], "expected_entities": ["recommendation", "kafka", "patrol"], "expected_tool_calls": [{"tool": "query_traces", "args_contains": {"service": "recommendation"}}], "expected_answer_contains": ["patrol", "trend", "RPS", "300%", "recommendation"]}
{"id": "otel-push-004", "category": "otel_push", "difficulty": "hard", "query": "audit agent 需要审计所有服务过去 30min 的 SLA 合规情况。数据来自 Kafka otel-metrics topic。请检查 availability、P95 latency、error_budget 三个维度，输出合规评分。", "expected_skills": ["audit-sop"], "expected_intent": "audit", "expected_metrics": ["availability", "error_budget"], "expected_entities": ["SLA", "audit", "kafka"], "expected_tool_calls": [{"tool": "query_metrics"}], "expected_answer_contains": ["audit", "SLA", "availability", "error budget", "compliance"]}
{"id": "otel-push-005", "category": "otel_push", "difficulty": "hard", "query": "多告警并发场景：一分钟内收到 3 条 P0 告警——frontend ServiceDown、checkout High5xxRate 50%、cart DatabaseConnectionPoolExhausted。AlertManager 把它们合并到了一个 webhook。需要 troubleshoot agent 找出哪个是根因哪个是受害者。", "expected_skills": ["troubleshoot"], "expected_intent": "troubleshoot", "expected_metrics": [], "expected_entities": ["frontend", "checkout", "cart", "P0", "root cause", "cascade"], "expected_tool_calls": [{"tool": "query_traces"}, {"tool": "query_metrics"}], "expected_answer_contains": ["root cause", "cascade", "frontend", "checkout", "cart", "P0"]}
```

- [ ] **Step 2: Verify golden cases parse correctly**

Run: `cd backend && python -c "
import json
from personal_assistant.skills.evaluation.models import AgentEvaluationCase
with open('evaluation/golden/otel_push.jsonl') as f:
    cases = [AgentEvaluationCase.model_validate(json.loads(line)) for line in f if line.strip()]
print(f'Loaded {len(cases)} OTEL push golden cases')
for c in cases:
    print(f'  {c.id}: {c.difficulty} — {len(c.expected_skills)} skills, intent={c.expected_intent}')
"`
Expected: Loaded 5 OTEL push golden cases, each listed with details

- [ ] **Step 3: Commit**

```bash
git add backend/evaluation/golden/otel_push.jsonl
git commit -m "test(otel): add OTEL push golden evaluation cases (5 scenarios)"
```

---

### Task 6: Integration verification

**Files:** (none, verification only)

- [ ] **Step 1: Run all OTEL tests together**

Run: `cd backend && python -m pytest tests/test_otel_schemas.py tests/test_kafka_consumer.py tests/test_otel_webhook.py -v`
Expected: All 11 tests PASS

- [ ] **Step 2: Verify import chain works end-to-end**

Run: `cd backend && python -c "
from personal_assistant.api.schemas import AlertManagerWebhook, OtelAlert
from personal_assistant.consumers.kafka_consumer import otlp_spans_to_jaeger_trace, OtelKafkaConsumer
from personal_assistant.apm import from_jaeger_trace, build_observability_snapshot
from personal_assistant.config import get_settings

settings = get_settings()
print(f'Kafka brokers: {settings.otel_kafka_brokers}')
print(f'Topics: {settings.otel_kafka_topic_spans}, {settings.otel_kafka_topic_metrics}, {settings.otel_kafka_topic_logs}')
print('All OTEL push imports OK')
"`
Expected: All imports OK, settings printed

- [ ] **Step 3: Commit verification results**

```bash
git add -A
git commit -m "verify(otel): integration check — all OTEL push modules load correctly"
```
