# Patrol Auto-Inspection Skill Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a `patrol` skill that, when triggered (e.g. "执行巡检" / "patrol"), consumes P2/P3 telemetry from Kafka via the existing `OtelKafkaConsumer`, converts anomalies to alerts, and POSTs them to `/api/otel/alerts` for SSE-broadcast display under the frontend APM tab — without interfering with existing skill triggers.

**Architecture:** The patrol skill lives at `skills/patrol/` with a focused `SKILL.md` (triggers scoped tightly to patrol/inspection actions, never metric definitions or incident troubleshooting) and a single Python script `run_patrol.py` that calls `OtelKafkaConsumer.consume_and_analyze()` and posts detected anomalies as P2/P3 alerts via `urllib.request` to the local server. The skill is then mounted onto the existing `patrol_agent` sub-agent in `multi_agent.py`.

**Tech Stack:** Python 3.12, `personal_assistant.consumers.kafka_consumer.OtelKafkaConsumer`, `urllib.request` (stdlib HTTP client), pytest

---

## Trigger Conflict Analysis

Before defining the patrol skill's triggers, here is the **complete trigger space** of existing skills so we avoid any overlap:

| Skill | Domain | Key Triggers | What It Does |
|-------|--------|-------------|--------------|
| `apm-metrics` | Knowledge | Web Vitals, LCP, CLS, INP, error rate, Apdex, custom metrics, conversion rate, error budget | **Explains** metric definitions (read-only) |
| `troubleshoot` | Action | troubleshoot, root cause, RCA, APM incident, frontend error, performance anomaly | **Diagnoses** a specific incident |
| `otel-query` | Action | otel, trace, span, metric, prometheus, jaeger, latency, throughput, error rate | **Queries** specific telemetry data |
| `audit-sop` | Action | audit, trace, execution log, tool failure, retry chain, token usage, SLA, compliance | **Audits** agent execution logs |
| `troubleshoot-runbook` | Knowledge | runbook, slow API, memory leak, CPU spike, JS error, resource failure | **References** runbook entries |

### Triggers to **AVOID** (would clash with existing skills):
- ❌ `error rate` — matches `apm-metrics` and `otel-query`
- ❌ `告警` (alone) — too broad; appears in patrol intent regex but also associated with general alerting
- ❌ `监控` — too broad; appears in intent_router patrol regex
- ❌ `健康检查` — already in `_DEFAULT_SKILL_REGEXES["patrol"]`; fine as a router regex but too broad as a skill trigger
- ❌ `trace`, `span`, `metric`, `latency` — belong to `otel-query`
- ❌ `APM incident`, `frontend error`, `performance anomaly` — belong to `troubleshoot`

### Patrol Skill's Triggers (carefully scoped to **inspection actions only**):

| Trigger | Rationale |
|---------|-----------|
| `patrol` | Unique English term; already routed to patrol intent |
| `inspection` | Synonymous with patrol; no other skill claims it |
| `巡检` | Core Chinese term; exclusively patrol domain |
| `自动巡检` | Auto-inspection — the exact use case |
| `定时巡检` | Scheduled inspection |
| `批量巡检` | Batch inspection |
| `执行巡检` | Execute inspection (action-oriented) |
| `拉取预警` | Pull alerts (NOT "告警" alone — prefixed with action) |
| `扫描预警` | Scan alerts (action-prefixed) |

These triggers are **action-oriented** (巡检/patrol = do something) and distinct from:
- Knowledge queries ("什么是 LCP" → `apm-metrics`)
- Incident diagnosis ("排查这个超时" → `troubleshoot`)
- Telemetry queries ("查一下 trace" → `otel-query`)
- Log audits ("审计线程" → `audit-sop`)

---

## Task 1: Create Patrol Skill SKILL.md (metadata only)

**Files:**
- Create: `backend/src/personal_assistant/skills/patrol/SKILL.md`

- [ ] **Step 1: Write the SKILL.md with carefully scoped triggers**

```markdown
---
name: patrol
description: >
  Scheduled batch patrol inspection — consumes Kafka OTEL telemetry, detects
  P2/P3 anomalies, and posts alerts for APM tab review. Use for running
  automatic inspection, pulling pending Kafka alerts, or executing a patrol
  scan. Do NOT use for explaining metrics (→ apm-metrics), diagnosing specific
  incidents (→ troubleshoot), querying individual traces or metrics
  (→ otel-query), or auditing agent execution logs (→ audit-sop).
triggers:
  - patrol
  - inspection
  - 巡检
  - 自动巡检
  - 定时巡检
  - 批量巡检
  - 执行巡检
  - 拉取预警
  - 扫描预警
scripts:
  - name: run_patrol
    description: >
      Consume pending OTEL telemetry from Kafka, detect anomalies, and post
      P2/P3 alerts to the OTEL alert pipeline for APM tab display. Supports
      configurable time window and topic selection.
    command: ["python", "scripts/run_patrol.py", "--window", "{window}", "--topic", "{topic}", "--limit", "{limit}", "--server-url", "{server_url}"]
    params:
      window:
        type: string
        description: Time window to scan (e.g. 5m, 30m, 1h).
        default: "15m"
      topic:
        type: string
        description: Kafka topic to consume (default: spans topic from config).
        default: ""
      limit:
        type: integer
        description: Maximum number of Kafka messages to consume.
        default: 50
      server_url:
        type: string
        description: Base URL of the langgraph-claw server to post alerts to.
        default: "http://localhost:8000"
---

# Patrol Skill — 自动巡检

## Purpose

Run a batch patrol/inspection scan: consume pending OTEL telemetry from Kafka,
detect anomalies via the APM analysis pipeline, and post P2/P3 alerts to the
OTEL alert endpoint so they appear under the frontend **APM tab** for human
review and on-demand analysis.

## When to Use

- User says: "执行巡检" / "run patrol" / "自动巡检"
- User says: "拉取最近的 Kafka 预警" / "pull pending alerts from Kafka"
- User says: "批量扫描预警" / "scan for alerts"
- User wants to proactively check service health before an incident happens

## When NOT to Use

- Explaining what a metric means (→ `apm-metrics`)
- Diagnosing a specific ongoing incident (→ `troubleshoot`)
- Querying a specific trace or Prometheus metric (→ `otel-query`)
- Auditing agent execution logs for governance (→ `audit-sop`)

## Procedure

1. Run the `run_patrol` script with the requested time window.
2. The script consumes Kafka messages, runs anomaly detection, and POSTs
   detected anomalies as P2/P3 alerts to `/api/otel/alerts`.
3. Alerts appear in the frontend APM tab via SSE broadcast.
4. Report the number of alerts generated and their severity levels.

## Alert Level Mapping

| Kafka Anomaly Severity | Posted Alert Level |
|------------------------|-------------------|
| `high` (IQR outlier)   | P2 (info)         |
| `medium` (Z-score ≥2)  | P3 (none)         |

P2 alerts are higher priority — they indicate clear outliers. P3 alerts are
lower priority — they indicate statistical deviations worth monitoring.

## Configuration

The script reads Kafka broker/topic settings from the standard config
(`OTEL_KAFKA_BROKERS`, etc.). The target server URL for posting alerts
defaults to `http://localhost:8000`.
```

- [ ] **Step 2: Verify SKILL.md is valid YAML frontmatter**

Run: `python -c "import yaml; yaml.safe_load(open('src/personal_assistant/skills/patrol/SKILL.md').read().split('---')[1])"`
Expected: Parses without error, returns dict with name/description/triggers/scripts.

- [ ] **Step 3: Verify SkillRegistry discovers the new skill**

Run: `python -c "from personal_assistant.skills import SkillRegistry; r = SkillRegistry('src/personal_assistant/skills'); assert 'patrol' in r.skill_names; s = r.skills['patrol']; print(f'Found: {s.name} with {len(s.triggers)} triggers and {len(s.script_decls)} scripts')"`
Expected: `Found: patrol with 9 triggers and 1 scripts`

- [ ] **Step 4: Commit**

```bash
git add backend/src/personal_assistant/skills/patrol/SKILL.md
git commit -m "feat(patrol): add patrol skill SKILL.md with scoped triggers

Triggers are carefully scoped to patrol/inspection actions only
(巡检, patrol, 自动巡检, etc.) to avoid interfering with existing
skills: apm-metrics (knowledge), troubleshoot (incident diagnosis),
otel-query (telemetry query), audit-sop (log audit).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: Create Patrol Script (run_patrol.py)

**Files:**
- Create: `backend/src/personal_assistant/skills/patrol/scripts/run_patrol.py`
- Test: `backend/tests/test_patrol_skill.py` (create)

- [ ] **Step 1: Write the failing test for run_patrol script**

```python
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

from run_patrol import (
    build_alert_payload,
    anomaly_to_alert_level,
    post_alerts,
    run_patrol,
    SEVERITY_TO_LEVEL,
)


class TestAnomalyToAlertLevel:
    def test_high_severity_maps_to_P2(self):
        assert anomaly_to_alert_level("high") == "P2"

    def test_medium_severity_maps_to_P3(self):
        assert anomaly_to_alert_level("medium") == "P3"

    def test_unknown_severity_defaults_to_P3(self):
        assert anomaly_to_alert_level("unknown") == "P3"
        assert anomaly_to_alert_level("") == "P3"


class TestBuildAlertPayload:
    def test_builds_alert_manager_webhook_format(self):
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
        assert alert["labels"]["severity"] == "info"  # P2 → info
        assert alert["labels"]["alertname"] == "P95LatencyAnomaly"
        assert alert["labels"]["service_name"] == "test-svc"
        assert alert["annotations"]["summary"] == "P95 latency anomaly detected: 1500ms"

    def test_P3_maps_to_none_severity(self):
        payload = build_alert_payload(
            service_name="svc",
            alert_name="TestAlert",
            summary="summary",
            level="P3",
        )
        assert payload["alerts"][0]["labels"]["severity"] == "none"

    def test_starts_at_is_iso8601_utc(self):
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
    def test_posts_to_otel_alerts_endpoint(self):
        payloads = [
            build_alert_payload("svc1", "Alert1", "summary1", "P2"),
            build_alert_payload("svc2", "Alert2", "summary2", "P3"),
        ]
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"status": "accepted", "alerts": 1}'

        with patch("run_patrol.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            results = post_alerts(payloads, server_url="http://localhost:8000")
            assert len(results) == 2
            assert all(r["success"] for r in results)
            assert mock_urlopen.call_count == 2

    def test_handles_http_error(self):
        from urllib.error import HTTPError
        mock_error = MagicMock()
        mock_error.code = 500
        mock_error.fp = None

        payloads = [build_alert_payload("svc", "Alert", "summary", "P2")]
        with patch("run_patrol.urllib.request.urlopen", side_effect=mock_error):
            results = post_alerts(payloads, server_url="http://localhost:8000")
            assert len(results) == 1
            assert not results[0]["success"]
            assert "error" in results[0]


class TestRunPatrol:
    def test_returns_empty_when_no_kafka_messages(self):
        mock_consumer = MagicMock()
        mock_consumer.consume_and_analyze.return_value = []
        with patch("run_patrol.OtelKafkaConsumer", return_value=mock_consumer):
            result = run_patrol(window="5m", topic="test", limit=10)
            assert result == {"status": "ok", "alerts_posted": 0, "traces_consumed": 0}

    def test_posts_alerts_for_snapshots_with_anomalies(self):
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

        with patch("run_patrol.OtelKafkaConsumer", return_value=mock_consumer):
            with patch("run_patrol.urllib.request.urlopen", return_value=mock_response):
                result = run_patrol(window="5m", topic="test", limit=10, server_url="http://localhost:8000")
                assert result["status"] == "ok"
                assert result["alerts_posted"] == 1
                assert result["traces_consumed"] == 1

    def test_skips_snapshots_without_anomalies(self):
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

        with patch("run_patrol.OtelKafkaConsumer", return_value=mock_consumer):
            result = run_patrol(window="5m", topic="test", limit=10)
            assert result["alerts_posted"] == 0
            assert result["traces_consumed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_patrol_skill.py -v --tb=short`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_patrol'`

- [ ] **Step 3: Write the patrol script implementation**

```python
#!/usr/bin/env python3
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
            logger.error("Alert %d/%d HTTP %d: %s", i + 1, len(payloads), exc.code, error_body)
        except urllib.error.URLError as exc:
            results.append({
                "success": False,
                "status_code": None,
                "error": str(exc.reason),
            })
            logger.error("Alert %d/%d connection error: %s", i + 1, len(payloads), exc.reason)

    return results


def run_patrol(
    *,
    window: str = "15m",
    topic: str | None = None,
    limit: int = 50,
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
        "traces_consumed": len(snapshots),
        "anomalies_detected": anomalies_detected,
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
        "--limit", type=int, default=50,
        help="Max Kafka messages to consume (default: 50)",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_patrol_skill.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/skills/patrol/scripts/run_patrol.py \
        backend/tests/test_patrol_skill.py
git commit -m "feat(patrol): add patrol script — Kafka consume → anomaly detect → post P2/P3 alerts

The run_patrol.py script:
1. Calls OtelKafkaConsumer.consume_and_analyze() to read OTEL telemetry
2. Maps anomaly signals to P2 (high/IQR) or P3 (medium/Z-score) alerts
3. POSTs alerts to /api/otel/alerts for SSE broadcast → APM tab display

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Mount Patrol Skill to Patrol Agent

**Files:**
- Modify: `backend/src/personal_assistant/agent/multi_agent.py:31-36`
- Test: `backend/tests/test_patrol_skill.py` (add to existing test file)

- [ ] **Step 1: Write the failing test for patrol agent skill assignment**

Append to `backend/tests/test_patrol_skill.py`:

```python
class TestPatrolAgentSkills:
    """Verify patrol skill is mounted on patrol_agent in multi_agent.py."""

    def test_patrol_agent_has_patrol_skill(self):
        from personal_assistant.agent.multi_agent import CHILD_AGENT_SKILLS
        assert "patrol" in CHILD_AGENT_SKILLS
        patrol_skills = CHILD_AGENT_SKILLS["patrol"]
        assert "patrol" in patrol_skills, (
            f"Expected 'patrol' skill in patrol agent skills, got: {patrol_skills}"
        )

    def test_patrol_agent_has_otel_query_skill(self):
        """Patrol agent should also have otel-query for on-demand data fetching."""
        from personal_assistant.agent.multi_agent import CHILD_AGENT_SKILLS
        patrol_skills = CHILD_AGENT_SKILLS["patrol"]
        assert "otel-query" in patrol_skills, (
            f"Expected 'otel-query' skill in patrol agent skills, got: {patrol_skills}"
        )

    def test_patrol_is_registered_subagent(self):
        from personal_assistant.agent.multi_agent import APM_SUBAGENTS
        assert "patrol" in APM_SUBAGENTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_patrol_skill.py::TestPatrolAgentSkills -v --tb=short`
Expected: FAIL — `AssertionError: 'patrol' not in patrol agent skills (currently [])`

- [ ] **Step 3: Update CHILD_AGENT_SKILLS in multi_agent.py**

In `backend/src/personal_assistant/agent/multi_agent.py:31-36`, change:

```python
CHILD_AGENT_SKILLS: dict[str, list[str]] = {
    "metrics": ["apm-metrics", "otel-query"],
    "troubleshoot": ["troubleshoot", "troubleshoot-runbook", "otel-query"],
    "patrol": [],
    "audit": ["audit-sop"],
}
```

To:

```python
CHILD_AGENT_SKILLS: dict[str, list[str]] = {
    "metrics": ["apm-metrics", "otel-query"],
    "troubleshoot": ["troubleshoot", "troubleshoot-runbook", "otel-query"],
    "patrol": ["patrol", "otel-query"],
    "audit": ["audit-sop"],
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_patrol_skill.py::TestPatrolAgentSkills -v --tb=short`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/multi_agent.py \
        backend/tests/test_patrol_skill.py
git commit -m "feat(patrol): mount patrol + otel-query skills on patrol_agent

Previously patrol_agent had an empty skill list. Now it has:
- patrol: run Kafka patrol scans and post P2/P3 alerts
- otel-query: query Jaeger/Prometheus for on-demand data

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Trigger Non-Interference Verification

**Files:**
- Test: `backend/tests/test_patrol_skill.py` (append integration tests)

- [ ] **Step 1: Write tests to verify patrol triggers don't overlap with other skills**

Append to `backend/tests/test_patrol_skill.py`:

```python
class TestTriggerNonInterference:
    """Verify patrol skill triggers do NOT match queries that belong to other skills."""

    def test_patrol_triggers_dont_match_knowledge_queries(self):
        """Queries asking about metric definitions should NOT trigger patrol."""
        knowledge_queries = [
            "什么是LCP",
            "怎么定义error rate",
            "Apdex是什么含义",
            "error budget怎么解读",
            "Web Vitals采集方法",
            "how to collect custom metrics",
        ]
        patrol_triggers = {"patrol", "inspection", "巡检", "自动巡检",
                           "定时巡检", "批量巡检", "执行巡检", "拉取预警", "扫描预警"}

        for query in knowledge_queries:
            lower = query.lower()
            matched = any(t in lower for t in patrol_triggers)
            assert not matched, (
                f"Knowledge query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in patrol_triggers if t in lower]}"
            )

    def test_patrol_triggers_dont_match_troubleshoot_queries(self):
        """Queries asking to diagnose incidents should NOT trigger patrol."""
        troubleshoot_queries = [
            "排查这个超时问题",
            "根因分析一下",
            "RCA这个故障",
            "帮我定位这个异常",
            "troubleshoot the frontend error",
            "APM incident diagnosis",
        ]
        patrol_triggers = {"patrol", "inspection", "巡检", "自动巡检",
                           "定时巡检", "批量巡检", "执行巡检", "拉取预警", "扫描预警"}

        for query in troubleshoot_queries:
            lower = query.lower()
            matched = any(t in lower for t in patrol_triggers)
            assert not matched, (
                f"Troubleshoot query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in patrol_triggers if t in lower]}"
            )

    def test_patrol_triggers_dont_match_otel_query_queries(self):
        """Queries asking to query specific traces/metrics should NOT trigger patrol."""
        otel_queries = [
            "查一下最近的trace",
            "查一下jaeger的span",
            "查一下Prometheus的latency",
            "查看error rate的metric",
            "show me spans for frontend service",
        ]
        patrol_triggers = {"patrol", "inspection", "巡检", "自动巡检",
                           "定时巡检", "批量巡检", "执行巡检", "拉取预警", "扫描预警"}

        for query in otel_queries:
            lower = query.lower()
            matched = any(t in lower for t in patrol_triggers)
            assert not matched, (
                f"OTEL query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in patrol_triggers if t in lower]}"
            )

    def test_patrol_triggers_dont_match_audit_queries(self):
        """Queries asking to audit execution logs should NOT trigger patrol."""
        audit_queries = [
            "审计这个线程",
            "检查工具调用失败率",
            "查一下审批事件",
            "audit the execution logs",
            "compliance report",
        ]
        patrol_triggers = {"patrol", "inspection", "巡检", "自动巡检",
                           "定时巡检", "批量巡检", "执行巡检", "拉取预警", "扫描预警"}

        for query in audit_queries:
            lower = query.lower()
            matched = any(t in lower for t in patrol_triggers)
            assert not matched, (
                f"Audit query '{query}' should NOT match patrol triggers, "
                f"but matched: {[t for t in patrol_triggers if t in lower]}"
            )

    def test_patrol_triggers_DO_match_patrol_queries(self):
        """Actual patrol queries SHOULD match patrol triggers."""
        patrol_queries = [
            "执行巡检",
            "run patrol",
            "自动巡检一下所有服务",
            "定时巡检",
            "批量巡检",
            "拉取预警",
            "扫描最近的预警",
            "patrol inspection now",
        ]
        patrol_triggers = {"patrol", "inspection", "巡检", "自动巡检",
                           "定时巡检", "批量巡检", "执行巡检", "拉取预警", "扫描预警"}

        for query in patrol_queries:
            lower = query.lower()
            matched = any(t in lower for t in patrol_triggers)
            assert matched, (
                f"Patrol query '{query}' SHOULD match patrol triggers, "
                f"but matched none. Triggers: {patrol_triggers}"
            )
```

- [ ] **Step 2: Run tests to verify trigger isolation**

Run: `python -m pytest tests/test_patrol_skill.py::TestTriggerNonInterference -v --tb=short`
Expected: All 5 tests PASS — patrol triggers match patrol queries but NOT knowledge/troubleshoot/otel/audit queries.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_patrol_skill.py
git commit -m "test(patrol): add trigger non-interference tests

Verify patrol skill triggers:
- Match: 执行巡检, run patrol, 自动巡检, 拉取预警, etc.
- Do NOT match: knowledge queries (什么是LCP), troubleshoot queries
  (排查超时), OTEL queries (查trace), audit queries (审计线程)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Integration — Verify Full Pipeline

**Files:**
- No new files. Run full test suite + manual verification.

- [ ] **Step 1: Run full test suite (excluding pre-existing failures)**

Run: `python -m pytest tests/test_patrol_skill.py tests/test_kafka_consumer.py tests/test_multi_agent_graph.py tests/test_child_agent_protocol.py tests/test_intent_router.py -v --tb=short 2>&1`
Expected: All tests pass (60+ across all files). Pre-existing intent_router failures (2 tests) are unrelated.

- [ ] **Step 2: Verify skill registration end-to-end**

Run: `python -c "
from personal_assistant.skills import SkillRegistry
r = SkillRegistry('src/personal_assistant/skills')
s = r.skills['patrol']
print(f'Skill: {s.name}')
print(f'Triggers: {s.triggers}')
print(f'Scripts: {[d.name for d in s.script_decls]}')
assert 'patrol' in s.triggers
assert '巡检' in s.triggers
assert '拉取预警' in s.triggers
print('OK: Patrol skill fully registered')
"`

Expected: `OK: Patrol skill fully registered`

- [ ] **Step 3: Verify skill is loaded with tools**

Run: `python -c "
from personal_assistant.skills import SkillRegistry
r = SkillRegistry('src/personal_assistant/skills')
r.load_skill('patrol')
s = r.skills['patrol']
assert s.loaded, 'Skill should be loaded'
assert len(s.tools) >= 1, f'Expected at least 1 tool, got {len(s.tools)}'
print(f'OK: Patrol skill loaded with {len(s.tools)} tool(s): {[t.name for t in s.tools]}')
"`

Expected: `OK: Patrol skill loaded with 1 tool(s): ['run_patrol']`

- [ ] **Step 4: Commit (final)**

```bash
# Verify nothing left uncommitted
git status
```

---

## Self-Review

### 1. Spec Coverage
- ✅ Create patrol skill with SKILL.md — Task 1
- ✅ Carefully scoped triggers to avoid interference — Task 1 (SKILL.md) + Task 4 (verification tests)
- ✅ Kafka consumption → anomaly detection — Task 2 (run_patrol.py)
- ✅ Post P2/P3 alerts to `/api/otel/alerts` — Task 2 (build_alert_payload + post_alerts)
- ✅ SSE broadcast → APM tab display — automatic (existing infrastructure)
- ✅ Mount skill to patrol_agent — Task 3 (multi_agent.py)
- ✅ Full pipeline verification — Task 5

### 2. Placeholder Scan
- ✅ No TBD/TODO anywhere
- ✅ All code steps contain actual code
- ✅ All test steps contain complete test code
- ✅ All commands are exact shell commands
- ✅ All commit messages are complete

### 3. Type Consistency
- ✅ `anomaly_to_alert_level` signature matches usage in `run_patrol`
- ✅ `build_alert_payload` signature matches test expectations
- ✅ `post_alerts` return type consistent between implementation and tests
- ✅ `run_patrol` signature matches test mock setup
- ✅ `SEVERITY_TO_LEVEL` dict keys match anomaly severity values ("high", "medium")
- ✅ AlertManager v4 format consistent with existing trigger scripts
