"""Tests for the patrol skill — Kafka consumption + alert posting."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the patrol script is importable
SKILLS_DIR = Path(__file__).resolve().parents[1] / "src" / "personal_assistant" / "skills"
sys.path.insert(0, str(SKILLS_DIR / "patrol" / "scripts"))


class TestAnomalyToAlertLevel:
    """Tests for anomaly_to_alert_level function."""

    def test_high_severity_maps_to_P2(self):
        from run_patrol import anomaly_to_alert_level
        assert anomaly_to_alert_level("high") == "P2"

    def test_medium_severity_maps_to_P3(self):
        from run_patrol import anomaly_to_alert_level
        assert anomaly_to_alert_level("medium") == "P3"

    def test_unknown_severity_defaults_to_P3(self):
        from run_patrol import anomaly_to_alert_level
        assert anomaly_to_alert_level("unknown") == "P3"
        assert anomaly_to_alert_level("") == "P3"


class TestBuildAlertPayload:
    """Tests for build_alert_payload function."""

    def test_builds_alert_manager_webhook_format(self):
        from run_patrol import build_alert_payload
        payload = build_alert_payload(
            service_name="test-svc",
            alert_name="P95LatencyAnomaly",
            summary="P95 latency anomaly detected: 1500ms",
            level="P2",
            description="IQR upper bound: 800ms, actual: 1500ms",
        )
        assert payload["receiver"] == "langgraph-claw"
        assert payload["status"] == "firing"
        assert payload["version"] == "4"
        assert len(payload["alerts"]) == 1
        alert = payload["alerts"][0]
        assert alert["labels"]["severity"] == "info"  # P2 -> info
        assert alert["labels"]["alertname"] == "P95LatencyAnomaly"
        assert alert["labels"]["service_name"] == "test-svc"
        assert alert["annotations"]["summary"] == "P95 latency anomaly detected: 1500ms"

    def test_p3_maps_to_none_severity(self):
        from run_patrol import build_alert_payload
        payload = build_alert_payload(
            service_name="svc",
            alert_name="TestAlert",
            summary="summary",
            level="P3",
        )
        assert payload["alerts"][0]["labels"]["severity"] == "none"

    def test_starts_at_is_iso8601_utc(self):
        from run_patrol import build_alert_payload
        payload = build_alert_payload(
            service_name="svc",
            alert_name="TestAlert",
            summary="summary",
            level="P2",
        )
        starts_at = payload["alerts"][0]["startsAt"]
        assert "T" in starts_at
        assert starts_at.endswith("Z")


class TestPostAlerts:
    """Tests for post_alerts function."""

    def test_posts_to_otel_alerts_endpoint(self):
        from run_patrol import build_alert_payload, post_alerts
        payloads = [
            build_alert_payload("svc1", "Alert1", "summary1", "P2"),
            build_alert_payload("svc2", "Alert2", "summary2", "P3"),
        ]
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'
        mock_response.status = 200
        # urlopen is used as a context manager: `with urlopen(...) as resp`
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_cm):
            results = post_alerts(payloads, server_url="http://localhost:8000")
            assert len(results) == 2
            assert all(r["success"] for r in results)

    def test_handles_http_error(self):
        from run_patrol import build_alert_payload, post_alerts
        from urllib.error import HTTPError

        mock_error = HTTPError("http://localhost:8000", 500, "Error", {}, None)

        payloads = [build_alert_payload("svc", "Alert", "summary", "P2")]
        with patch("urllib.request.urlopen", side_effect=mock_error):
            results = post_alerts(payloads, server_url="http://localhost:8000")
            assert len(results) == 1
            assert not results[0]["success"]
            assert "error" in results[0]


class TestRunPatrol:
    """Tests for run_patrol function."""

    def test_returns_empty_when_no_kafka_messages(self):
        from run_patrol import run_patrol

        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = []
        with patch(
            "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
            return_value=mock_consumer,
        ):
            result = run_patrol(window="5m", topic="test", limit=10)
            assert result == {"status": "ok", "alerts_posted": 0, "traces_consumed": 0,
                              "anomalies_detected": 0, "errors": []}

    def test_posts_alerts_for_snapshots_with_anomalies(self):
        from run_patrol import run_patrol
        from personal_assistant.apm import (
            AnomalySignal,
            BackendObservabilitySummary,
            FrontendObservabilitySummary,
            ObservabilitySnapshot,
            RootCauseReport,
        )
        snapshot = ObservabilitySnapshot(
            frontend=FrontendObservabilitySummary(
                total_events=5,
                error_count=1,
                resource_error_count=0,
                web_vitals={},
                top_errors=[],
            ),
            backend=BackendObservabilitySummary(
                total_events=3,
                tool_errors=0,
                tool_retries=0,
                p95_duration_ms=None,
            ),
            anomalies=[
                AnomalySignal(
                    metric="LCP",
                    value=5000.0,
                    method="iqr",
                    severity="high",
                    reason="LCP value 5000 is above IQR upper bound 3000",
                ),
            ],
            root_cause=RootCauseReport(
                category="frontend_performance",
                summary="Frontend performance metrics crossed APM thresholds.",
                evidence=[],
                recommendation="Inspect render-blocking resources.",
            ),
        )
        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = [snapshot]
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'
        mock_response.status = 200
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch(
            "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
            return_value=mock_consumer,
        ):
            with patch("urllib.request.urlopen", return_value=mock_cm):
                result = run_patrol(
                    window="5m", topic="test", limit=10,
                    server_url="http://localhost:8000",
                )
                assert result["status"] == "ok"
                assert result["alerts_posted"] == 1
                assert result["traces_consumed"] == 1

    def test_skips_snapshots_without_anomalies(self):
        from run_patrol import run_patrol
        from personal_assistant.apm import (
            BackendObservabilitySummary,
            FrontendObservabilitySummary,
            ObservabilitySnapshot,
            RootCauseReport,
        )
        snapshot = ObservabilitySnapshot(
            frontend=FrontendObservabilitySummary(
                total_events=5, error_count=1, resource_error_count=0,
                web_vitals={}, top_errors=[],
            ),
            backend=BackendObservabilitySummary(
                total_events=3, tool_errors=0, tool_retries=0,
                p95_duration_ms=None,
            ),
            anomalies=[],  # No anomalies
            root_cause=RootCauseReport(
                category="normal",
                summary="No dominant incident signature.",
                evidence=[],
                recommendation="Keep collecting.",
            ),
        )
        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = [snapshot]

        with patch(
            "personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer",
            return_value=mock_consumer,
        ):
            result = run_patrol(window="5m", topic="test", limit=10)
            assert result["alerts_posted"] == 0
            assert result["traces_consumed"] == 1
