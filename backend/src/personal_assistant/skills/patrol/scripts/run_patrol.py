#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patrol skill script — consume Kafka OTEL telemetry and post P2/P3 alerts.

Triggered by the ``patrol`` skill.  Consumes pending telemetry from Kafka,
runs anomaly detection via :mod:`personal_assistant.apm`, and posts detected
anomalies as P2/P3 alerts to ``POST /api/otel/alerts`` for SSE broadcast to
the frontend APM tab.

Usage:
  python scripts/run_patrol.py --window 15m --limit 50
  python scripts/run_patrol.py --window 1h --topic otlp_spans --server-url http://staging:8000
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Severity → AlertManager severity label mapping ──────────────────────
SEVERITY_TO_LEVEL: dict[str, str] = {
    "high": "P2",
    "medium": "P3",
}
LEVEL_TO_SEVERITY: dict[str, str] = {
    "P2": "info",
    "P3": "none",
}


def should_scan_metric_alerts(topic: str | None) -> bool:
    """Return True when patrol should scan OTLP metric alert records."""
    if topic is None or topic == "":
        return True
    normalized = topic.lower()
    return "metric" in normalized or "alert" in normalized


def should_scan_trace_snapshots(topic: str | None) -> bool:
    """Return True when patrol should run trace snapshot anomaly detection."""
    if topic is None or topic == "":
        return True
    normalized = topic.lower()
    return "metric" not in normalized and "alert" not in normalized


def anomaly_to_alert_level(severity: str) -> str:
    """Map anomaly severity (high/medium) to alert level (P2/P3)."""
    return SEVERITY_TO_LEVEL.get(severity, "P3")


def build_alert_payload(
    service_name: str,
    alert_name: str,
    summary: str,
    level: str = "P2",
    description: str = "",
) -> dict[str, Any]:
    """Build an AlertManager v4 webhook payload for a single alert.

    Args:
        service_name: OTEL service that produced the anomaly.
        alert_name: Human-readable alert name (e.g. "LCPAnomaly").
        summary: One-line summary of the anomaly.
        level: Alert level — ``"P2"`` (info) or ``"P3"`` (none).
        description: Optional detailed description.

    Returns:
        An AlertManager webhook v4 payload dict ready for JSON serialization.
    """
    severity = LEVEL_TO_SEVERITY.get(level, "none")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "receiver": "langgraph-claw",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": alert_name,
                "severity": severity,
                "service_name": service_name,
            },
            "annotations": {
                "summary": summary,
                "description": description,
            },
            "startsAt": now,
            "endsAt": "",
            "generatorURL": "",
        }],
        "groupLabels": {},
        "commonLabels": {
            "alertname": alert_name,
            "severity": severity,
            "service_name": service_name,
        },
        "commonAnnotations": {
            "summary": summary,
        },
        "externalURL": "",
        "version": "4",
    }


def post_alerts(
    payloads: list[dict[str, Any]],
    *,
    server_url: str = "http://localhost:8000",
) -> list[dict[str, Any]]:
    """Post a batch of alert payloads to the OTEL alert endpoint.

    Args:
        payloads: List of AlertManager webhook payloads.
        server_url: Base URL of the langgraph-claw server.

    Returns:
        List of result dicts with ``success``, ``status_code``, and optional
        ``error`` keys.
    """
    url = f"{server_url.rstrip('/')}/api/otel/alerts"
    results: list[dict[str, Any]] = []

    for i, payload in enumerate(payloads):
        data = json.dumps(payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
                results.append({
                    "success": True,
                    "status_code": resp.status,
                    "response": body,
                })
                logger.info(
                    "Alert %d/%d posted: %s — %s",
                    i + 1,
                    len(payloads),
                    payload["alerts"][0]["labels"]["alertname"],
                    body.get("status", "unknown"),
                )
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode() if exc.fp else ""
            results.append({
                "success": False,
                "status_code": exc.code,
                "error": error_body,
            })
            logger.error(
                "Alert %d/%d HTTP %d: %s",
                i + 1, len(payloads), exc.code, error_body,
            )
        except (TimeoutError, socket.timeout) as exc:
            results.append({
                "success": False,
                "status_code": None,
                "error": f"timeout: {exc}",
            })
            logger.error(
                "Alert %d/%d timeout posting to %s",
                i + 1, len(payloads), url,
            )
        except urllib.error.URLError as exc:
            results.append({
                "success": False,
                "status_code": None,
                "error": str(exc.reason),
            })
            logger.error(
                "Alert %d/%d connection error: %s",
                i + 1, len(payloads), exc.reason,
            )

    return results


def run_patrol(
    *,
    window: str = "15m",
    topic: str | None = None,
    limit: int = 500,
    server_url: str = "http://localhost:8000",
) -> dict[str, Any]:
    """Execute a full patrol cycle: consume Kafka → detect anomalies → post alerts.

    Args:
        window: Time window for Kafka consumption (e.g. ``"15m"``).
        topic: Kafka topic override (``None`` uses config default).
        limit: Max Kafka messages to consume.
        server_url: Base URL for posting alerts.

    Returns:
        Summary dict with ``status``, ``alerts_posted``, ``traces_consumed``,
        ``anomalies_detected``, and ``errors``.
    """
    from personal_assistant.consumers.kafka_consumer import OtelKafkaConsumer

    metric_alerts_detected = 0
    messages_consumed = 0
    snapshots = []
    if should_scan_trace_snapshots(topic):
        consumer = OtelKafkaConsumer()
        snapshots = consumer.consume_and_analyze(
            window=window,
            topic=topic,
            limit=limit,
        )

    alerts_posted = 0
    anomalies_detected = 0
    errors: list[str] = []

    payloads: list[dict[str, Any]] = []
    posted_metric_keys: set[tuple[str, str, str]] = set()
    if should_scan_metric_alerts(topic):
        from personal_assistant.consumers.alert_consumer import (
            AlertKafkaConsumer,
            _parse_otlp_metrics,
        )

        metric_consumer = AlertKafkaConsumer(
            topic=topic or None,
            max_messages=limit,
        )
        raw_messages = metric_consumer._fetch_alert_messages()
        messages_consumed += len(raw_messages)
        for raw in raw_messages:
            for alert_data in _parse_otlp_metrics(raw):
                level = alert_data.get("level", "P3")
                if level not in ("P2", "P3"):
                    continue
                metric_alerts_detected += 1
                service_name = alert_data.get("service_name", "unknown")
                alert_name = alert_data.get("alert_name", "MetricAlert")
                dedupe_key = (level, service_name, alert_name)
                if dedupe_key in posted_metric_keys:
                    continue
                posted_metric_keys.add(dedupe_key)
                payloads.append(
                    build_alert_payload(
                        service_name=service_name,
                        alert_name=alert_name,
                        summary=alert_data.get("summary", ""),
                        level=level,
                        description=alert_data.get("description", ""),
                    )
                )

    for snapshot in snapshots:
        if not snapshot.anomalies:
            continue

        anomalies_detected += len(snapshot.anomalies)
        service_name = "unknown"

        for anomaly in snapshot.anomalies:
            level = anomaly_to_alert_level(anomaly.severity)
            alert_name = f"Patrol{anomaly.metric}Anomaly"
            summary = f"[巡检] {anomaly.reason}"
            description = (
                f"Patrol scan (window={window}) detected {anomaly.method} anomaly. "
                f"Metric: {anomaly.metric}, value: {anomaly.value:g}, "
                f"severity: {anomaly.severity}."
            )
            payloads.append(
                build_alert_payload(
                    service_name=service_name,
                    alert_name=alert_name,
                    summary=summary,
                    level=level,
                    description=description,
                )
            )

    if payloads:
        results = post_alerts(payloads, server_url=server_url)
        alerts_posted = sum(1 for r in results if r["success"])
        errors = [
            r.get("error", f"HTTP {r.get('status_code')}")
            for r in results
            if not r["success"]
        ]

    return {
        "status": "ok" if not errors else "partial",
        "alerts_posted": alerts_posted,
        "messages_consumed": messages_consumed,
        "traces_consumed": len(snapshots),
        "anomalies_detected": anomalies_detected,
        "metric_alerts_detected": metric_alerts_detected,
        "errors": errors,
    }


def main() -> int:
    """CLI entry point for the patrol script (called by the skill harness)."""
    parser = argparse.ArgumentParser(
        description="Patrol scan: consume Kafka OTEL telemetry → post P2/P3 alerts"
    )
    parser.add_argument(
        "--window", default="15m",
        help="Time window to scan (e.g. 5m, 15m, 30m, 1h)",
    )
    parser.add_argument(
        "--topic", default=None,
        help="Kafka topic override (default: spans topic from config)",
    )
    parser.add_argument(
        "--limit", type=int, default=500,
        help="Max Kafka messages to consume (default: 500)",
    )
    parser.add_argument(
        "--server-url", default="http://localhost:8000",
        help="Base URL for posting alerts (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    result = run_patrol(
        window=args.window,
        topic=args.topic,
        limit=args.limit,
        server_url=args.server_url,
    )

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    if result["status"] == "ok":
        return 0
    return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    raise SystemExit(main())
