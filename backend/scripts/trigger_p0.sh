#!/usr/bin/env bash
# Trigger a P0 (critical) OTEL alert via AlertManager webhook
# Usage: ./trigger_p0.sh [service_name] [alert_name] [summary]
# Example: ./trigger_p0.sh frontend ServiceDown "frontend is down"
set -euo pipefail

SERVICE="${1:-frontend}"
ALERT="${2:-ServiceDown}"
SUMMARY="${3:-${SERVICE} is DOWN — critical alert}"
BASE_URL="${OTEL_BASE_URL:-http://192.168.5.7:8000}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "🚨 Triggering P0 (critical) alert: ${ALERT} on ${SERVICE}"

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
      "severity": "critical",
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
    "severity": "critical",
    "service_name": "${SERVICE}"
  },
  "commonAnnotations": {
    "summary": "${SUMMARY}"
  },
  "externalURL": "",
  "version": "4"
}
EOF
