# Evaluation Diagnostic Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose richer ClawEval case diagnostics to the frontend, including final answers, execution outputs, judge evidence, and a likely failing node.

**Architecture:** Extend the existing `CaseEvaluationDetail` API contract so backend diagnosis remains the source of truth. The frontend renders the new fields in each failed/warning case without changing the SSE transport shape.

**Tech Stack:** Python 3.11, Pydantic, pytest, React 19, TypeScript, Vitest.

## Global Constraints

- Keep changes scoped to evaluation diagnostics and documentation.
- Preserve existing quick/e2e modes and SSE event payload structure.
- Use deterministic diagnosis first; LLM-as-a-judge remains optional through the existing judge hook.
- Write failing tests before production changes.

---

### Task 1: Backend Diagnostic Fields

**Files:**
- Modify: `backend/src/personal_assistant/skills/evaluation/models.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/diagnostics.py`
- Test: `backend/tests/test_agent_evaluation_diagnostics.py`

**Interfaces:**
- Produces: `CaseEvaluationDetail.suspected_node: str | None`
- Produces: `CaseEvaluationDetail.diagnostic_outputs: dict[str, Any]`

- [ ] **Step 1: Write failing backend tests**
- [ ] **Step 2: Run backend diagnostics tests and confirm RED**
- [ ] **Step 3: Add model fields and deterministic suspected-node/output builders**
- [ ] **Step 4: Run backend diagnostics tests and confirm GREEN**

### Task 2: Frontend Detail Rendering

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Test: `frontend/src/components/WorkspacePanel.test.tsx`

**Interfaces:**
- Consumes: `CaseEvaluationDetail.suspected_node`
- Consumes: `CaseEvaluationDetail.diagnostic_outputs`

- [ ] **Step 1: Write failing frontend test for visible diagnostics**
- [ ] **Step 2: Run the focused frontend test and confirm RED**
- [ ] **Step 3: Render suspected node, final answer, judge evidence, and log/output JSON**
- [ ] **Step 4: Run the focused frontend test and confirm GREEN**

### Task 3: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `技术方案报告.md`

- [ ] **Step 1: Document the richer diagnostic payload and frontend display**
- [ ] **Step 2: Run focused backend and frontend tests**
- [ ] **Step 3: Run broader verification where practical**
