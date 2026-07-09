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
        description: "Time window to scan (e.g. 5m, 30m, 1h)."
        default: "15m"
      topic:
        type: string
        description: "Kafka topic to consume (default: spans topic from config)."
        default: ""
      limit:
        type: integer
        description: "Maximum number of Kafka messages to consume."
        default: 50
      server_url:
        type: string
        description: "Base URL of the langgraph-claw server to post alerts to."
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
|------------------------|---------------------|
| `high` (IQR outlier)   | P2 (info)           |
| `medium` (Z-score ≥2)  | P3 (none)           |

P2 alerts are higher priority — they indicate clear outliers. P3 alerts are
lower priority — they indicate statistical deviations worth monitoring.

## Configuration

The script reads Kafka broker/topic settings from the standard config
(`OTEL_KAFKA_BROKERS`, etc.). The target server URL for posting alerts
defaults to `http://localhost:8000`.
