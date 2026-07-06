#!/usr/bin/env bash
# Trigger a P1 (warning) OTEL alert via AlertManager webhook
# Usage: ./trigger_p1.sh [service_name] [alert_name] [summary]
# Example: ./trigger_p1.sh checkout HighLatencyP95 "P95 > 800ms"
set -euo pipefail

SERVICE="${1:-checkout}"
ALERT="${2:-HighLatencyP95}"
SUMMARY="${3:-${SERVICE} P95 latency exceeding SLO — warning}"
BASE_URL="${OTEL_BASE_URL:-http://192.168.5.7:8000}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "🟡 Triggering P1 (warning) alert: ${ALERT} on ${SERVICE}"

curl -s -X POST "${BASE_URL}/api/otel/alerts" \
  -H "Content-Type: application/json" \
  -d @- <<EOF | python3 -m json.tool 2>/dev/null || cat
{
  "receiver": "langgraph-claw",
  "status": "firing",
  "alerts": [{
    "status": "firing",
    "labels": {
      "alertname": "${ALERT}",
      "severity": "warning",
      "service_name": "${SERVICE}"
    },
    "annotations": {
      "summary": "${SUMMARY}"
    },
    "startsAt": "${NOW}",
    "endsAt": "",
    "generatorURL": ""
  }],
  "groupLabels": {},
  "commonLabels": {
    "alertname": "${ALERT}",
    "severity": "warning",
    "service_name": "${SERVICE}"
  },
  "commonAnnotations": {
    "summary": "${SUMMARY}"
  },
  "externalURL": "",
  "version": "4"
}
EOF
