"""Tests for OTEL push schemas — AlertManager v4 webhook payload models."""
from personal_assistant.api.schemas import AlertManagerWebhook


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


def test_alert_manager_webhook_parses_v4_payload():
    webhook = AlertManagerWebhook.model_validate(ALERTMANAGER_P0_PAYLOAD)
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
