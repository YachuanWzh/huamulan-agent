---
name: patrol
description: Automated governance patrol skill for alert rules, scheduled health checks, anomaly triage, alert deduplication, and human-approved repair workflows.
triggers:
  - patrol
  - inspection
  - alert rule
  - automatic repair
  - governance
scripts:
  - name: run_patrol_checks
    description: Evaluate simple JSON patrol checks and return pass/fail findings.
    command: ["python", "checks/health_check.py"]
---

# Patrol Skill

Use this skill for JD-3 style alert patrol and closed-loop operations.

## Patrol Loop

1. Evaluate alert rules: `{metric} {operator} {threshold} for {duration}`.
2. Run scheduled checks for API health, DB connectivity, disk capacity,
   frontend error rate, latency, and retry rate.
3. Deduplicate similar alerts by metric, route, signature, and dependency.
4. Route abnormal findings to `troubleshoot` for RCA.
5. Produce a repair proposal. Never deploy automatically without explicit
   human approval.
6. Verify the repair with tests, ClawEval, and fresh telemetry.

## Report Shape

Return: passing checks, failing checks, impact, evidence, suggested owner,
repair proposal, approval gate, and verification command.
