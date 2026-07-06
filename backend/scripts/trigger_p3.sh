#!/usr/bin/env bash
# Trigger a P3 (none) OTEL alert via AlertManager webhook
# Usage: ./trigger_p3.sh [service_name] [alert_name] [summary]
# Example: ./trigger_p3.sh accounting SloComplianceDrift "SLO drift detected"
set -euo pipefail

SERVICE="${1:-accounting}"
ALERT="${2:-SloComplianceDrift}"
SUMMARY="${3:-${SERVICE} governance check — none}"
BASE_URL="${OTEL_BASE_URL:-http://localhost:8000}"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "⚪ Triggering P3 (none) alert: ${ALERT} on ${SERVICE}"

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
      "severity": "none",
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
    "severity": "none",
    "service_name": "${SERVICE}"
  },
  "commonAnnotations": {
    "summary": "${SUMMARY}"
  },
  "externalURL": "",
  "version": "4"
}
EOF
