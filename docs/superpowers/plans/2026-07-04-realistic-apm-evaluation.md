# Realistic APM Evaluation Fixtures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-like synthetic APM evaluation dataset that can be used before real telemetry is available.

**Architecture:** Store golden routing cases in `backend/evaluation/golden/apm_realistic.jsonl` and payload fixtures in `backend/evaluation/fixtures/apm_realistic/`. Add tests that validate fixture schemas, tool compatibility, observability snapshot generation, and quick routing accuracy.

**Tech Stack:** Python, pytest, Pydantic models in `personal_assistant.apm` and `personal_assistant.api.schemas`, existing skill evaluation CLI/runtime.

## Global Constraints

- Strict TDD: write fixture validation tests before adding fixture files.
- Fixtures must use deterministic static JSON, not generated random data.
- Fixtures must not require network, live production logs, or secrets.
- Keep the first pass hand-authored and focused on 9 high-value scenarios.

---

### Task 1: Fixture Contract Tests

**Files:**
- Create: `backend/tests/test_apm_realistic_fixtures.py`

**Interfaces:**
- Consumes: `FrontendRumEvent.model_validate`, `ExecutionLog.model_validate`, `build_observability_snapshot`, `evaluate_routing_cases`
- Produces: regression coverage for the new realistic APM dataset

- [ ] **Step 1: Write the failing test**

Create tests that load `backend/evaluation/golden/apm_realistic.jsonl`, verify every `fixture` file exists, validate `rum_events` and `execution_logs`, run observability snapshots, validate `checks`, assert scenario categories, and run quick routing.

- [ ] **Step 2: Run RED**

Run: `python -m pytest backend/tests/test_apm_realistic_fixtures.py -q`

Expected: FAIL because the test file or dataset does not exist yet.

### Task 2: Golden Dataset And Fixtures

**Files:**
- Create: `backend/evaluation/golden/apm_realistic.jsonl`
- Create: `backend/evaluation/fixtures/apm_realistic/checkout_release_regression.json`
- Create: `backend/evaluation/fixtures/apm_realistic/cdn_chunk_404.json`
- Create: `backend/evaluation/fixtures/apm_realistic/orders_api_slow_dependency.json`
- Create: `backend/evaluation/fixtures/apm_realistic/payment_callback_timeout.json`
- Create: `backend/evaluation/fixtures/apm_realistic/dashboard_memory_leak.json`
- Create: `backend/evaluation/fixtures/apm_realistic/websocket_reconnect_storm.json`
- Create: `backend/evaluation/fixtures/apm_realistic/closed_loop_patrol.json`
- Create: `backend/evaluation/fixtures/apm_realistic/business_metrics_design.json`
- Create: `backend/evaluation/fixtures/apm_realistic/governance_sla_audit.json`

**Interfaces:**
- Consumes: test contract from Task 1
- Produces: reusable synthetic production APM case library

- [ ] **Step 1: Add minimal data to pass schema validation**

Each fixture includes realistic `incident_meta`, enough `rum_events`/`execution_logs`/`checks` for its flow, and no secrets.

- [ ] **Step 2: Run GREEN**

Run: `python -m pytest backend/tests/test_apm_realistic_fixtures.py -q`

Expected: PASS.

### Task 3: Routing And Evaluation Verification

**Files:**
- No additional files unless a routing gap is exposed.

**Interfaces:**
- Consumes: `personal_assistant.skills.evaluation`
- Produces: verification evidence for the new dataset

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest backend/tests/test_apm_realistic_fixtures.py backend/tests/test_router.py -q`

Expected: PASS.

- [ ] **Step 2: Run quick evaluation CLI**

From `backend`, run:

`python -m personal_assistant.skills.evaluation --skills-dir src/personal_assistant/skills --golden evaluation/golden/apm_realistic.jsonl --output-json $env:TEMP\apm_realistic_eval.json`

Expected: exit 0 and routing selection accuracy 1.0.
