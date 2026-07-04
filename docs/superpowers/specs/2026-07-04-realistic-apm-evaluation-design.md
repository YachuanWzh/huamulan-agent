# Realistic APM Evaluation Fixtures Design

## Goal

Build a synthetic production-like APM evaluation set for pre-launch prevention when real telemetry is not yet available.

## Scope

Create a small hand-authored fixture library and matching golden routing dataset. The data should exercise APM metric knowledge, automated patrol, troubleshooting/RCA, runbook lookup, and governance audit flows without requiring live production access.

## Data Shape

Each golden case in `backend/evaluation/golden/apm_realistic.jsonl` includes:

- `id`
- `category`
- `difficulty`
- `query`
- `expected_skills`
- `expected_tool_calls` when the flow should call a tool
- `expected_answer_contains`
- `fixture`, a relative path to a JSON payload in `backend/evaluation/fixtures/apm_realistic/`

Each fixture payload can include:

- `rum_events`: web vitals, custom timings, JS errors, resource errors, session IDs, trace IDs, route, release, region, device class, and feature flag metadata.
- `execution_logs`: agent/backend tool events, retries, approvals, security blocks, token usage, and duration.
- `checks`: patrol rule inputs compatible with `patrol/checks/health_check.py`.
- `incident_meta`: high-level incident context such as deploy window, owner, impact estimate, baseline, current SLO, and suspected dependency.

## Scenario Set

1. Checkout release regression: TypeError burst, LCP regression, feature flag, and cart API evidence.
2. CDN chunk 404: white-screen sessions, missing asset hash, deploy manifest mismatch, and CDN region metadata.
3. Orders API slow dependency: TTFB/API latency p95 jump, DB slow query, cache miss, and retry chain.
4. Payment callback timeout: third-party timeout, order state inconsistency, retry budget, and rollback signal.
5. Dashboard memory leak: heap growth across route transitions, long tasks, listener leak hints.
6. WebSocket reconnect storm: disconnect spikes, reconnect attempts, gateway errors, and affected session cohort.
7. Closed-loop patrol: multiple pass/fail checks, then RCA and runbook validation.
8. APM metric design: custom business conversion metrics with numerator/denominator, dedupe rules, and alert thresholds.
9. Governance audit: cross-thread SLA compliance, approval latency, tool success rate, security blocks, token growth.

## Testing

Add `backend/tests/test_apm_realistic_fixtures.py` to verify:

- The golden file exists and has at least 9 cases.
- Every case references an existing fixture.
- Fixture `rum_events` validate as `FrontendRumEvent`.
- Fixture `execution_logs` validate as `ExecutionLog`.
- Fixtures with RUM/log data can build an observability snapshot.
- Fixtures with `checks` can run through the patrol check script contract.
- Quick skill routing over `apm_realistic.jsonl` reaches 1.0 selection accuracy.

## Non-Goals

- No live data ingestion.
- No synthetic data generator in this first pass.
- No changes to the public golden schema model; unknown JSONL fields are acceptable for external fixture linkage.
