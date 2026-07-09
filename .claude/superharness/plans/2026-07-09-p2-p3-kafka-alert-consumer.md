# P2/P3 Kafka Alert Consumer Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cron-driven Kafka consumer that polls P2 alerts every 5min and P3 alerts every 30min (configurable via env), processing them through the same persistence+broadcast pipeline as the webhook handler.

**Architecture:** New `consumers/alert_consumer.py` module with `AlertKafkaConsumer` class. Two `asyncio.Task` loops run at different intervals (P2/P3), each consuming from a shared Kafka topic, filtering by `level`, then calling the same `_process_alert` pipeline (memory deque + AlertPersistence + SSE broadcast). Started/stopped in FastAPI lifespan alongside existing services.

**Tech Stack:** kafka-python (already a dependency), asyncio, existing AlertPersistence + SSE infrastructure.

---

### Task 1: Add config settings for alert Kafka topic and poll intervals

**Files:**
- Modify: `backend/src/personal_assistant/config.py` (add ~6 fields after existing OTEL Kafka block)

- [ ] **Step 1: Add env var settings to `Settings` class**

In `backend/src/personal_assistant/config.py`, after the `otel_kafka_consumer_group` field (line ~301), add:

```python
    # ── OTEL Alert Kafka Consumer (P2/P3 cron poll) ──────────────────
    otel_alert_kafka_enabled: bool = Field(
        default=False,
        alias="OTEL_ALERT_KAFKA_ENABLED",
    )
    otel_alert_kafka_topic: str = Field(
        default="otel-alerts",
        alias="OTEL_ALERT_KAFKA_TOPIC",
    )
    otel_alert_p2_poll_seconds: int = Field(
        default=300,
        alias="OTEL_ALERT_P2_POLL_SECONDS",
    )
    otel_alert_p3_poll_seconds: int = Field(
        default=1800,
        alias="OTEL_ALERT_P3_POLL_SECONDS",
    )
    otel_alert_kafka_max_messages: int = Field(
        default=100,
        alias="OTEL_ALERT_KAFKA_MAX_MESSAGES",
    )
```

- [ ] **Step 2: Run existing tests to confirm no regressions**

Run: `cd backend && python -m pytest tests/ -x -q --tb=short`

Expected: all existing tests PASS (no regressions from config changes).

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/config.py
git commit -m "feat(config): add OTEL_ALERT_KAFKA_* settings for P2/P3 cron consumer"
```

---

### Task 2: Write failing tests for AlertKafkaConsumer

**Files:**
- Create: `backend/tests/test_alert_consumer.py`

- [ ] **Step 1: Write the test file with `test_parse_alert_message_p2`**

```python
"""Tests for alert_consumer.py — P2/P3 Kafka alert consumer."""
import json
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from personal_assistant.consumers.alert_consumer import (
    AlertKafkaConsumer,
    _parse_alert_message,
)


SAMPLE_P2_MESSAGE = json.dumps({
    "status": "firing",
    "labels": {
        "alertname": "HighLatencyP95",
        "severity": "info",
        "service_name": "checkout",
    },
    "annotations": {
        "summary": "P95 latency > 500ms for 5 minutes",
        "description": "checkout service P95 latency is 650ms",
    },
    "startsAt": "2026-07-09T10:00:00Z",
    "endsAt": "",
    "generatorURL": "http://prometheus:9090/graph?g0.expr=...",
})

SAMPLE_P3_MESSAGE = json.dumps({
    "status": "firing",
    "labels": {
        "alertname": "CartLowTraffic",
        "severity": "none",
        "service_name": "cart",
    },
    "annotations": {
        "summary": "Cart service request rate below 1 req/s",
        "description": "Cart service has had fewer than 1 request/s for 10 minutes",
    },
    "startsAt": "2026-07-09T10:00:00Z",
    "endsAt": "",
    "generatorURL": "http://prometheus:9090/graph?g0.expr=...",
})


def test_parse_alert_message_p2():
    """P2 (severity=info) messages parse with level='P2'."""
    result = _parse_alert_message(SAMPLE_P2_MESSAGE)
    assert result is not None
    assert result["level"] == "P2"
    assert result["severity"] == "info"
    assert result["alert_name"] == "HighLatencyP95"
    assert result["service_name"] == "checkout"


def test_parse_alert_message_p3():
    """P3 (severity=none) messages parse with level='P3'."""
    result = _parse_alert_message(SAMPLE_P3_MESSAGE)
    assert result is not None
    assert result["level"] == "P3"
    assert result["severity"] == "none"
    assert result["alert_name"] == "CartLowTraffic"
    assert result["service_name"] == "cart"


def test_parse_alert_message_has_all_persistence_fields():
    """Parsed alert dict must contain all fields AlertPersistence expects."""
    result = _parse_alert_message(SAMPLE_P2_MESSAGE)
    required_fields = {
        "id", "received_at", "severity", "level", "service_name",
        "alert_name", "summary", "description", "starts_at", "status",
        "rca_status", "rca_thread_id", "rca_pending_approvals",
        "rca_result_text", "metadata",
    }
    for field in required_fields:
        assert field in result, f"Missing field: {field}"


def test_parse_alert_message_invalid_json_returns_none():
    """Malformed JSON returns None."""
    result = _parse_alert_message("not valid json {{{")
    assert result is None


def test_parse_alert_message_missing_labels_returns_none():
    """Messages without required labels fields return None."""
    bad_msg = json.dumps({"status": "firing", "labels": {}})
    result = _parse_alert_message(bad_msg)
    assert result is None


def test_parse_alert_message_unknown_severity_defaults_to_p3():
    """Unknown severity values default to P3."""
    msg = json.dumps({
        "status": "firing",
        "labels": {"alertname": "Test", "severity": "bogus", "service_name": "test"},
        "annotations": {"summary": "test", "description": ""},
        "startsAt": "2026-07-09T10:00:00Z",
    })
    result = _parse_alert_message(msg)
    assert result is not None
    assert result["level"] == "P3"
```

- [ ] **Step 2: Run tests to verify they FAIL**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py -v`

Expected: all 6 tests FAIL with `ModuleNotFoundError: No module named 'personal_assistant.consumers.alert_consumer'`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_alert_consumer.py
git commit -m "test: add failing tests for alert_consumer _parse_alert_message"
```

---

### Task 3: Implement `_parse_alert_message` — Kafka message → alert dict

**Files:**
- Create: `backend/src/personal_assistant/consumers/alert_consumer.py`

- [ ] **Step 1: Create `alert_consumer.py` with `_parse_alert_message` and stub class**

```python
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
```

- [ ] **Step 2: Run tests to verify they PASS for the parse function**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py -v`

Expected: 6 tests PASS (the `_parse_alert_message` tests).

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/consumers/alert_consumer.py
git commit -m "feat(alert_consumer): add _parse_alert_message for Kafka alert JSON"
```

---

### Task 4: Write failing tests for AlertKafkaConsumer class

**Files:**
- Modify: `backend/tests/test_alert_consumer.py` (append tests)

- [ ] **Step 1: Add class-level tests**

Append to `backend/tests/test_alert_consumer.py`:

```python
# ── AlertKafkaConsumer tests ──────────────────────────────────────────


class TestAlertKafkaConsumerInit:
    """Test AlertKafkaConsumer initialization and config wiring."""

    def test_defaults_from_settings(self):
        """Consumer picks up defaults from config when no args given."""
        consumer = AlertKafkaConsumer()
        assert consumer.topic == "otel-alerts"
        assert consumer.p2_interval == 300
        assert consumer.p3_interval == 1800
        assert consumer.max_messages == 100
        assert consumer.brokers != ""  # from OTEL_KAFKA_BROKERS

    def test_explicit_args_override_settings(self):
        """Explicit constructor args take precedence."""
        consumer = AlertKafkaConsumer(
            brokers="kafka:9092",
            topic="custom-alerts",
            p2_interval=60,
            p3_interval=120,
            max_messages=50,
            consumer_group="test-group",
        )
        assert consumer.topic == "custom-alerts"
        assert consumer.p2_interval == 60
        assert consumer.p3_interval == 120
        assert consumer.max_messages == 50
```

- [ ] **Step 2: Run tests to verify they FAIL**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py::TestAlertKafkaConsumerInit -v`

Expected: FAIL — `ImportError: cannot import name 'AlertKafkaConsumer'`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_alert_consumer.py
git commit -m "test: add failing tests for AlertKafkaConsumer initialization"
```

---

### Task 5: Implement `AlertKafkaConsumer.__init__` and config wiring

**Files:**
- Modify: `backend/src/personal_assistant/consumers/alert_consumer.py` (extend)

- [ ] **Step 1: Add `AlertKafkaConsumer` class with `__init__` and config wiring**

Append to `alert_consumer.py` (after the `_parse_alert_message` function):

```python
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
        self.p2_interval = p2_interval if p2_interval is not None else settings.otel_alert_p2_poll_seconds
        self.p3_interval = p3_interval if p3_interval is not None else settings.otel_alert_p3_poll_seconds
        self.max_messages = max_messages if max_messages is not None else settings.otel_alert_kafka_max_messages
        self.consumer_group = consumer_group or settings.otel_kafka_consumer_group

        self._on_alert = on_alert

        # Lifecycle state
        self._running = False
        self._p2_task: asyncio.Task | None = None
        self._p3_task: asyncio.Task | None = None
```

- [ ] **Step 2: Run tests to verify they PASS**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py::TestAlertKafkaConsumerInit -v`

Expected: 2 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/consumers/alert_consumer.py
git commit -m "feat(alert_consumer): add AlertKafkaConsumer class with config wiring"
```

---

### Task 6: Write failing integration test for the Kafka poll cycle

**Files:**
- Modify: `backend/tests/test_alert_consumer.py` (append integration test)

- [ ] **Step 1: Add `TestAlertKafkaConsumerPollCycle` class**

Append to `backend/tests/test_alert_consumer.py`:

```python
class TestAlertKafkaConsumerPollCycle:
    """Integration-style tests for the Kafka poll → process pipeline."""

    @pytest.mark.asyncio
    async def test_single_poll_cycle_processes_alerts(self):
        """A single poll_for_level call fetches, filters, and processes alerts."""
        processed: list[dict] = []

        async def capture(alert_data: dict) -> None:
            processed.append(alert_data)

        consumer = AlertKafkaConsumer(
            on_alert=capture,
            brokers="localhost:9092",
            topic="test-alerts",
        )

        # Build mock Kafka records: 2 P2, 1 P3
        p2_msg_1 = json.dumps({
            "status": "firing",
            "labels": {"alertname": "HighLatency", "severity": "info", "service_name": "checkout"},
            "annotations": {"summary": "p2 test 1", "description": ""},
            "startsAt": "2026-07-09T10:00:00Z",
        })
        p2_msg_2 = json.dumps({
            "status": "firing",
            "labels": {"alertname": "HighErrorRate", "severity": "info", "service_name": "frontend"},
            "annotations": {"summary": "p2 test 2", "description": ""},
            "startsAt": "2026-07-09T10:01:00Z",
        })
        p3_msg = json.dumps({
            "status": "firing",
            "labels": {"alertname": "LowTraffic", "severity": "none", "service_name": "cart"},
            "annotations": {"summary": "p3 test", "description": ""},
            "startsAt": "2026-07-09T10:00:00Z",
        })

        # Mock _fetch_alerts to return controlled data
        with patch.object(consumer, "_fetch_alert_messages") as mock_fetch:
            mock_fetch.return_value = [p2_msg_1, p2_msg_2, p3_msg]

            await consumer._poll_for_level("P2")

        # Only P2 alerts should be captured
        assert len(processed) == 2
        p2_levels = [a["level"] for a in processed]
        assert all(level == "P2" for level in p2_levels)
        alert_names = [a["alert_name"] for a in processed]
        assert "HighLatency" in alert_names
        assert "HighErrorRate" in alert_names

    @pytest.mark.asyncio
    async def test_poll_cycle_filters_p3(self):
        """Polling for P3 only processes messages with level=P3."""
        processed: list[dict] = []

        async def capture(alert_data: dict) -> None:
            processed.append(alert_data)

        consumer = AlertKafkaConsumer(
            on_alert=capture,
            brokers="localhost:9092",
        )

        messages = [
            json.dumps({
                "status": "firing",
                "labels": {"alertname": "P2Alert", "severity": "info", "service_name": "svc"},
                "annotations": {"summary": "p2", "description": ""},
                "startsAt": "2026-07-09T10:00:00Z",
            }),
            json.dumps({
                "status": "firing",
                "labels": {"alertname": "P3Alert", "severity": "none", "service_name": "svc"},
                "annotations": {"summary": "p3", "description": ""},
                "startsAt": "2026-07-09T10:00:00Z",
            }),
        ]

        with patch.object(consumer, "_fetch_alert_messages") as mock_fetch:
            mock_fetch.return_value = messages
            await consumer._poll_for_level("P3")

        assert len(processed) == 1
        assert processed[0]["level"] == "P3"
        assert processed[0]["alert_name"] == "P3Alert"

    @pytest.mark.asyncio
    async def test_empty_fetch_no_processing(self):
        """When Kafka returns no messages, nothing is processed."""
        processed: list[dict] = []

        async def capture(alert_data: dict) -> None:
            processed.append(alert_data)

        consumer = AlertKafkaConsumer(on_alert=capture, brokers="localhost:9092")

        with patch.object(consumer, "_fetch_alert_messages") as mock_fetch:
            mock_fetch.return_value = []
            await consumer._poll_for_level("P2")

        assert len(processed) == 0
```

- [ ] **Step 2: Run tests to verify they FAIL**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py::TestAlertKafkaConsumerPollCycle -v`

Expected: 3 FAIL (AttributeError — `_fetch_alert_messages` and `_poll_for_level` don't exist yet).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_alert_consumer.py
git commit -m "test: add failing integration tests for poll cycle"
```

---

### Task 7: Implement `_fetch_alert_messages` and `_poll_for_level`

**Files:**
- Modify: `backend/src/personal_assistant/consumers/alert_consumer.py` (extend AlertKafkaConsumer)

- [ ] **Step 1: Add `_fetch_alert_messages` method**

Inside `AlertKafkaConsumer` class, append:

```python
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
            logger.exception("Alert consumer: Kafka fetch error for topic %s", self.topic)
            return []
```

- [ ] **Step 2: Add `_poll_for_level` method**

Inside `AlertKafkaConsumer` class, append:

```python
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
```

- [ ] **Step 3: Run tests to verify they PASS**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py::TestAlertKafkaConsumerPollCycle -v`

Expected: 3 PASS.

- [ ] **Step 4: Run ALL alert consumer tests**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py -v`

Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/consumers/alert_consumer.py
git commit -m "feat(alert_consumer): add _fetch_alert_messages and _poll_for_level"
```

---

### Task 8: Write failing tests for start/stop lifecycle

**Files:**
- Modify: `backend/tests/test_alert_consumer.py` (append lifecycle tests)

- [ ] **Step 1: Add `TestAlertKafkaConsumerLifecycle` class**

Append to `backend/tests/test_alert_consumer.py`:

```python
class TestAlertKafkaConsumerLifecycle:
    """Test start/stop lifecycle management."""

    @pytest.mark.asyncio
    async def test_start_creates_background_tasks(self):
        """start() creates two asyncio Tasks for P2 and P3 loops."""
        consumer = AlertKafkaConsumer(brokers="localhost:9092")

        # Mock _poll_for_level so the loop doesn't actually contact Kafka
        with patch.object(consumer, "_poll_for_level", new_callable=AsyncMock) as mock_poll:
            await consumer.start()

            assert consumer._running is True
            assert consumer._p2_task is not None
            assert consumer._p3_task is not None

            # Give the loop one tick to call the mock at least once
            await asyncio.sleep(0.05)

            await consumer.stop()

        # Should have been called at least once per level
        p2_calls = [c for c in mock_poll.call_args_list if c.args == ("P2",)]
        p3_calls = [c for c in mock_poll.call_args_list if c.args == ("P3",)]
        assert len(p2_calls) >= 1
        assert len(p3_calls) >= 1

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self):
        """stop() cancels both background tasks and sets _running=False."""
        consumer = AlertKafkaConsumer(brokers="localhost:9092")

        with patch.object(consumer, "_poll_for_level", new_callable=AsyncMock):
            await consumer.start()
            assert consumer._running is True

            await consumer.stop()

            assert consumer._running is False
            assert consumer._p2_task is None
            assert consumer._p3_task is None

    @pytest.mark.asyncio
    async def test_double_start_is_safe(self):
        """Calling start() twice does not create duplicate tasks."""
        consumer = AlertKafkaConsumer(brokers="localhost:9092")

        with patch.object(consumer, "_poll_for_level", new_callable=AsyncMock):
            await consumer.start()
            p2_first = consumer._p2_task
            p3_first = consumer._p3_task

            await consumer.start()  # second call — no-op

            assert consumer._p2_task is p2_first
            assert consumer._p3_task is p3_first

            await consumer.stop()
```

- [ ] **Step 2: Run tests to verify they FAIL**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py::TestAlertKafkaConsumerLifecycle -v`

Expected: 3 FAIL — `AttributeError: 'AlertKafkaConsumer' object has no attribute 'start'`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_alert_consumer.py
git commit -m "test: add failing lifecycle tests for start/stop"
```

---

### Task 9: Implement `start()` and `stop()` methods

**Files:**
- Modify: `backend/src/personal_assistant/consumers/alert_consumer.py` (extend AlertKafkaConsumer)

- [ ] **Step 1: Add `_run_loop`, `start`, and `stop` methods**

Inside `AlertKafkaConsumer` class, append:

```python
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
            for _ in range(interval):
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
```

- [ ] **Step 2: Run lifecycle tests to verify they PASS**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py::TestAlertKafkaConsumerLifecycle -v`

Expected: 3 PASS.

- [ ] **Step 3: Run ALL alert consumer tests**

Run: `cd backend && python -m pytest tests/test_alert_consumer.py -v`

Expected: all 14 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/personal_assistant/consumers/alert_consumer.py
git commit -m "feat(alert_consumer): add start/stop lifecycle with background poll loops"
```

---

### Task 10: Integrate into FastAPI lifespan with SSE broadcast

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py` (lifespan + _process_alert helper)

- [ ] **Step 1: Add `_process_alert_from_kafka` helper function to server.py**

Before the `lifespan` function (around line 415 of server.py), add:

```python
async def _process_alert_from_kafka(alert_data: dict) -> None:
    """Process a P2/P3 alert from the Kafka consumer.

    Mirrors the webhook handler's pipeline: in-memory deque → persistence
    → SSE broadcast.  Does NOT trigger auto-RCA (P2/P3 are manual analyze)
    and does NOT push to Feishu (P0/P1 only).
    """
    _otel_alerts.appendleft(alert_data)
    await _alert_persistence.save_alert(alert_data)
    await _broadcast_otel_alert(alert_data)
```

- [ ] **Step 2: Modify `lifespan` to create/start/stop the alert consumer**

Inside the `lifespan` function, add:

```python
@asynccontextmanager
async def lifespan(_: FastAPI):
    global _feishu_stream_client, _alert_kafka_consumer  # <-- add _alert_kafka_consumer

    await postgres_memory.start()
    registry.start_watching()
    await warmup_skill_routing(settings, registry)

    # Start Feishu Stream client (bidirectional bot via WebSocket)
    if settings.feishu_stream_enabled and settings.feishu_app_id:
        _feishu_stream_client = FeishuStreamClient(
            on_message=_build_feishu_message_handler(harness, memory),
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
        )
        _feishu_stream_client.start_background()
        logger.info("Feishu Stream client started (bidirectional mode)")

    # ── Start P2/P3 Kafka alert consumer ──────────────────────────
    if settings.otel_alert_kafka_enabled:
        from personal_assistant.consumers.alert_consumer import AlertKafkaConsumer

        _alert_kafka_consumer = AlertKafkaConsumer(
            on_alert=_process_alert_from_kafka,
        )
        await _alert_kafka_consumer.start()
    else:
        _alert_kafka_consumer = None

    try:
        yield
    finally:
        # Stop P2/P3 Kafka alert consumer
        if _alert_kafka_consumer is not None:
            await _alert_kafka_consumer.stop()

        # Stop Feishu Stream client
        if _feishu_stream_client and _feishu_stream_client.running:
            _feishu_stream_client.stop(timeout=5.0)
            logger.info("Feishu Stream client stopped")

        registry.stop_watching()
        await cache.close()
        await postgres_memory.stop()
```

- [ ] **Step 3: Declare `_alert_kafka_consumer` global near the top of server.py**

Near line ~188 (where `_active_rca_tasks` is declared), add:

```python
_alert_kafka_consumer = None  # type: ignore[var-annotated]
```

- [ ] **Step 4: Run existing tests to confirm no regressions**

Run: `cd backend && python -m pytest tests/ -x -q --tb=short`

Expected: all existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/api/server.py
git commit -m "feat(api): integrate AlertKafkaConsumer into FastAPI lifespan"
```

---

### Task 11: Update `.env.example` with new settings

**Files:**
- Modify: `backend/.env.example` (append new section)

- [ ] **Step 1: Append OTEL Alert Kafka Consumer section**

After the existing `# ── OTEL Push: Kafka Consumer ──────────────────────────────────` block (around line 301), add:

```
# ── OTEL Alert Kafka Consumer — P2/P3 cron poll ──────────────────
# When OTEL_ALERT_KAFKA_ENABLED is true, the backend starts two
# background asyncio loops that poll a dedicated Kafka topic for
# P2/P3 alerts (P0/P1 arrive via AlertManager webhook instead).
#
# OTEL_ALERT_KAFKA_TOPIC — Kafka topic that receives P2/P3 alert JSON.
#   Each record is a single alert in AlertManager v4 JSON format
#   (labels.severity = "info"→P2, "none"→P3).
#
# OTEL_ALERT_P2_POLL_SECONDS — interval between P2 poll cycles.
#   Default 300 (5 minutes). Set lower for testing.
#
# OTEL_ALERT_P3_POLL_SECONDS — interval between P3 poll cycles.
#   Default 1800 (30 minutes). Set lower for testing.
#
# OTEL_ALERT_KAFKA_MAX_MESSAGES — max messages per poll cycle (default 100).
#
# Required: no (default: disabled)
# OTEL_ALERT_KAFKA_ENABLED=true
# OTEL_ALERT_KAFKA_TOPIC=otel-alerts
# OTEL_ALERT_P2_POLL_SECONDS=300
# OTEL_ALERT_P3_POLL_SECONDS=1800
# OTEL_ALERT_KAFKA_MAX_MESSAGES=100
```

- [ ] **Step 2: Commit**

```bash
git add backend/.env.example
git commit -m "docs(env): document OTEL_ALERT_KAFKA_* settings"
```

---

### Task 12: Run full test suite and verify

**Files:** (none — verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short`

Expected: all tests PASS (existing + new alert consumer tests).

- [ ] **Step 2: Verify import works standalone**

Run: `cd backend && PYTHONPATH=src python -c "from personal_assistant.consumers.alert_consumer import AlertKafkaConsumer; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit (if any fixes needed)**

Only if Step 1 or 2 revealed issues.
