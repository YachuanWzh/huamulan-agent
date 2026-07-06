"""Tests for POST /api/otel/alerts webhook endpoint."""
from unittest.mock import MagicMock, patch

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


def test_otel_alerts_endpoint_accepts_p2_alerts():
    """P2 (info) alerts are accepted via webhook, stored and broadcast, but no RCA."""
    payload = {
        "receiver": "blackhole",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "LatencyTrendRising", "severity": "info", "service_name": "cart"},
                "annotations": {"summary": "P95 latency trending upward"},
                "startsAt": "2026-07-05T10:00:00Z",
                "endsAt": "",
                "generatorURL": "",
            }
        ],
        "groupLabels": {},
        "commonLabels": {"alertname": "LatencyTrendRising", "severity": "info"},
        "commonAnnotations": {},
        "externalURL": "",
        "version": "4",
    }
    response = client.post("/api/otel/alerts", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["alerts"] == 1  # Accepted — stored + broadcast, no RCA


def test_otel_alerts_endpoint_accepts_p3_alerts():
    """P3 (none) alerts are accepted via webhook, stored and broadcast, but no RCA."""
    payload = {
        "receiver": "blackhole",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "SloComplianceDrift", "severity": "none", "service_name": "quote"},
                "annotations": {"summary": "SLO compliance drift detected"},
                "startsAt": "2026-07-05T10:00:00Z",
                "endsAt": "",
                "generatorURL": "",
            }
        ],
        "groupLabels": {},
        "commonLabels": {"alertname": "SloComplianceDrift", "severity": "none"},
        "commonAnnotations": {},
        "externalURL": "",
        "version": "4",
    }
    response = client.post("/api/otel/alerts", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["alerts"] == 1  # Accepted — stored + broadcast, no RCA


def test_otel_alerts_endpoint_accepts_unknown_severity():
    """Alerts with unrecognized severity default to P3 level."""
    payload = {
        "receiver": "blackhole",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "UnknownAlert", "severity": "bogus", "service_name": "test"},
                "annotations": {"summary": "unknown severity"},
                "startsAt": "2026-07-05T10:00:00Z",
                "endsAt": "",
                "generatorURL": "",
            }
        ],
        "groupLabels": {},
        "commonLabels": {"alertname": "UnknownAlert", "severity": "bogus"},
        "commonAnnotations": {},
        "externalURL": "",
        "version": "4",
    }
    response = client.post("/api/otel/alerts", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["alerts"] == 1  # Accepted — defaults to P3


# ── Feishu push integration tests ─────────────────────────────────────


class TestFeishuIntegration:
    """Verify Feishu notifier is called for P0/P1 but not P2/P3."""

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p0_alert_triggers_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_notifier.send_alert.return_value = True
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P0_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_called_once()
        call_args = mock_notifier.send_alert.call_args[0][0]
        assert call_args["level"] == "P0"

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p1_alert_triggers_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_notifier.send_alert.return_value = True
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P1_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_called_once()

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p2_alert_does_not_trigger_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_get_notifier.return_value = mock_notifier

        payload = {
            "receiver": "blackhole",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "LatencyTrendRising", "severity": "info", "service_name": "cart"},
                    "annotations": {"summary": "P95 latency trending upward"},
                    "startsAt": "2026-07-05T10:00:00Z",
                    "endsAt": "",
                    "generatorURL": "",
                }
            ],
            "groupLabels": {},
            "commonLabels": {"alertname": "LatencyTrendRising", "severity": "info"},
            "commonAnnotations": {},
            "externalURL": "",
            "version": "4",
        }
        response = client.post("/api/otel/alerts", json=payload)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_not_called()

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_p3_alert_does_not_trigger_feishu_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = True
        mock_get_notifier.return_value = mock_notifier

        payload = {
            "receiver": "blackhole",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "SloComplianceDrift", "severity": "none", "service_name": "quote"},
                    "annotations": {"summary": "SLO compliance drift detected"},
                    "startsAt": "2026-07-05T10:00:00Z",
                    "endsAt": "",
                    "generatorURL": "",
                }
            ],
            "groupLabels": {},
            "commonLabels": {"alertname": "SloComplianceDrift", "severity": "none"},
            "commonAnnotations": {},
            "externalURL": "",
            "version": "4",
        }
        response = client.post("/api/otel/alerts", json=payload)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_not_called()

    @patch("personal_assistant.api.server.get_feishu_notifier")
    def test_feishu_disabled_skips_push(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_notifier.enabled = False
        mock_get_notifier.return_value = mock_notifier

        response = client.post("/api/otel/alerts", json=ALERTMANAGER_P0_PAYLOAD)
        assert response.status_code == 200
        mock_notifier.send_alert.assert_not_called()
