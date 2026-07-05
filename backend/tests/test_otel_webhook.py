"""Tests for POST /api/otel/alerts webhook endpoint."""
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


def test_otel_alerts_endpoint_drops_p2_p3_alerts():
    """P2 (info) and P3 (none) alerts are not processed — Kafka handles them."""
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
    assert data["alerts"] == 0  # Dropped — not critical/warning
