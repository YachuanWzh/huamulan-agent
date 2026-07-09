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
