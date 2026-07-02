# Evaluation Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend and frontend evaluation diagnostics so E2E runs show case-level details, failure attribution, and Pro-model LLM judge output.

**Architecture:** Add pure diagnostic models/functions under `personal_assistant.skills.evaluation`, wire them into the existing SSE evaluation run, then render the new `case_details` report data in `WorkspacePanel`. Keep deterministic scoring independent from judge availability.

**Tech Stack:** Python 3.11, Pydantic, FastAPI SSE, LangChain DeepSeek-compatible chat client, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- TDD first: each behavior starts with a failing test.
- Judge default model is `deepseek-v4-pro`.
- Judge model names containing `flash` are invalid.
- LLM judge must be injectable in tests and unavailable judge failures must not break deterministic evaluation.
- Frontend details must support keyboard-accessible expandable rows through native `details`/`summary`.

---

### Task 1: Diagnostic Models And Pure Case Checks

**Files:**
- Modify: `backend/src/personal_assistant/skills/evaluation/models.py`
- Create: `backend/src/personal_assistant/skills/evaluation/diagnostics.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/__init__.py`
- Test: `backend/tests/test_agent_evaluation_diagnostics.py`

**Interfaces:**
- Produces: `EvaluationCheck`, `CaseDiagnosis`, `JudgeEvaluation`, `CaseEvaluationDetail`.
- Produces: `build_case_evaluation_detail(case, outcome, mode, judge=None) -> CaseEvaluationDetail`.

- [ ] Write failing tests for tool, routing, answer, and safety diagnosis.
- [ ] Run `cd backend; uv run pytest tests/test_agent_evaluation_diagnostics.py -q` and confirm failures are missing models/functions.
- [ ] Implement models and deterministic diagnostics.
- [ ] Run the focused backend diagnostic tests and confirm they pass.

### Task 2: Judge Configuration And Evaluation

**Files:**
- Modify: `backend/src/personal_assistant/config.py`
- Create: `backend/src/personal_assistant/skills/evaluation/judge.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/__init__.py`
- Test: `backend/tests/test_agent_evaluation_diagnostics.py`

**Interfaces:**
- Produces: `EvaluationJudgeConfig`.
- Produces: `evaluate_case_with_judge(case, outcome, *, judge_client, model) -> JudgeEvaluation`.

- [ ] Write failing tests proving default judge model is Pro and Flash names are rejected.
- [ ] Write failing tests proving judge JSON is parsed and invalid judge output becomes unavailable.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement judge config and parser.
- [ ] Run focused tests and confirm they pass.

### Task 3: Stream And Report Case Details

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/report.py`
- Test: `backend/tests/test_skill_evaluation.py`

**Interfaces:**
- `case_progress` event includes `detail`.
- `done.report.case_details` includes all case details.

- [ ] Write failing stream tests for progress detail and final report case details.
- [ ] Run focused stream tests and confirm failures.
- [ ] Wire diagnostics and injectable judge into `_iter_skill_evaluation_events`.
- [ ] Run focused backend tests and confirm they pass.

### Task 4: Frontend Types And Details UI

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Modify: `frontend/src/App.css`
- Test: `frontend/src/lib/api.test.ts`
- Test: `frontend/src/components/WorkspacePanel.test.tsx`

**Interfaces:**
- Frontend `SkillEvaluationReport.case_details`.
- `WorkspacePanel` renders Evaluation Details rows.

- [ ] Write failing API/component tests for streamed case details and expandable detail rendering.
- [ ] Run focused frontend tests and confirm failures.
- [ ] Implement TypeScript types, state, rendering, and CSS.
- [ ] Run focused frontend tests and confirm they pass.

### Task 5: Verification

**Files:**
- Test-only verification across backend and frontend.

- [ ] Run `cd backend; uv run pytest tests/test_agent_evaluation_diagnostics.py tests/test_agent_evaluation_quality.py tests/test_agent_evaluation_safety.py tests/test_skill_evaluation.py -q`.
- [ ] Run `cd frontend; npm test -- WorkspacePanel.test.tsx api.test.ts AppCss.test.ts`.
- [ ] Run `cd frontend; npm run build`.
- [ ] Report exact command outputs and any remaining gaps.

