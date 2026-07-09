"""Tests for alert_consumer.py — P2/P3 Kafka alert consumer."""
import json
from unittest.mock import AsyncMock, patch

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


# ── _parse_alert_message tests ────────────────────────────────────────


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


# ── AlertKafkaConsumer __init__ tests ─────────────────────────────────


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


# ── Poll cycle integration tests ──────────────────────────────────────


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

        # Mock _fetch_alert_messages to return controlled data
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
