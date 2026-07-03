---
name: troubleshoot
description: Intelligent troubleshooting skill for frontend APM incidents, abnormal performance metrics, JS errors, resource failures, backend retry chains, and root-cause analysis reports.
triggers:
  - troubleshoot
  - root cause
  - RCA
  - APM incident
  - frontend error
  - performance anomaly
scripts:
  - name: analyze_apm_incident
    description: Build a deterministic troubleshooting report from RUM events and execution logs JSON.
    command: ["python", "scripts/analyze_apm_incident.py"]
---

# Troubleshoot Skill

Use this skill when the user asks to diagnose an incident, explain a frontend
performance anomaly, analyze JS/resource errors, or turn execution logs into a
root-cause report.

## Procedure

1. Identify the incident surface: page URL, metric name, error signature,
   affected thread, and time window.
2. Collect evidence from frontend RUM events, execution logs, retry chains,
   approval/security events, and relevant runbook entries.
3. Cluster symptoms by signature: JS error name/message, failed resource URL,
   slow metric, tool_call_id, or API dependency.
4. Run RCA in this order:
   - JS runtime errors
   - Resource loading failures
   - Web Vitals or custom timing threshold breach
   - Backend tool retry/error chains
   - Normal/no dominant signal
5. Return a report with: Summary, Impact, Evidence, Root Cause, Fix Plan,
   Verification, and Prevention.

Do not invent telemetry. If the evidence is missing, state which API or log
source is missing.
