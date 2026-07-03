# Evaluation History And Hallucination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix surprising percentage displays, expose stored evaluation history, and add deterministic hallucination signals for evaluation runs.

**Architecture:** Keep scores internally normalized to `0..1`, normalize legacy `0..100` values at API/UI boundaries, and reuse the existing `skill_evaluation_results` PostgreSQL table for trend history. Add deterministic hallucination checks to the evaluation quality layer and render them alongside existing ClawEval metrics.

**Tech Stack:** Python/FastAPI/Pydantic/PostgreSQL backend, React/TypeScript frontend, pytest and Vitest.

## Global Constraints

- Strict TDD: write failing tests first and verify RED before implementation.
- Store evaluation history in PostgreSQL.
- Preserve existing latest-score behavior while adding history visibility.
- Keep metrics as normalized floats (`0..1`) in current reports.

---

### Task 1: Normalize Percent Display

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.test.tsx`
- Modify: `frontend/src/components/WorkspacePanel.tsx`

**Interfaces:**
- Produces: `formatPercent(value)` and meter width handle both normalized and legacy percent-scale values.

- [ ] Add a failing frontend test where `overall_score: 88` renders `88%`, not `8800%`.
- [ ] Run `npm test -- WorkspacePanel.test.tsx -t percent`.
- [ ] Add a local score normalizer used by `formatPercent` and meter width.
- [ ] Re-run the targeted frontend test.

### Task 2: Expose Evaluation History

**Files:**
- Modify: `backend/tests/test_skill_evaluation.py`
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/WorkspacePanel.test.tsx`
- Modify: `frontend/src/components/WorkspacePanel.tsx`

**Interfaces:**
- Produces backend method `list_skill_evaluation_history(skill_name: str | None = None, limit: int = 100) -> list[SkillEvaluationSnapshot]`.
- Produces API endpoint `GET /api/skills/evaluation/history`.
- Produces frontend API method `listSkillEvaluationHistory(skillName?: string)`.

- [ ] Add failing backend tests for history ordering and endpoint helper behavior.
- [ ] Add failing frontend tests showing history rows and trend delta.
- [ ] Implement backend history query over existing `skill_evaluation_results`.
- [ ] Wire API and frontend.
- [ ] Re-run targeted backend and frontend tests.

### Task 3: Add Hallucination Metrics

**Files:**
- Modify: `backend/tests/test_agent_evaluation_quality.py`
- Modify: `backend/tests/test_agent_evaluation_diagnostics.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/models.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/quality.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/diagnostics.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/report.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/WorkspacePanel.tsx`

**Interfaces:**
- Produces `HallucinationEvaluationMetrics` with `answer_hallucination_rate`, `repeated_tool_call_rate`, and `tool_argument_hallucination_rate`.
- Adds `hallucinations` to `SkillEvaluationReport`.

- [ ] Add failing backend tests for answer forbidden hallucination, repeated tool calls, and argument mismatch hallucination.
- [ ] Add failing diagnostic test for repeated tool call check.
- [ ] Implement deterministic hallucination metrics from case expectations and actual logs.
- [ ] Render hallucination metrics in report markdown and frontend summary.
- [ ] Re-run targeted tests and then broader relevant suites.
