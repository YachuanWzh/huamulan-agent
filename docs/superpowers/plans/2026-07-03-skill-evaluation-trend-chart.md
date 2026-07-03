# Skill Evaluation Trend Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add card-level and expanded trend charts for Skill Evaluation history.

**Architecture:** Reuse the existing skill evaluation history API. Render lightweight SVG charts inside `WorkspacePanel.tsx`, with CSS in `App.css`.

**Tech Stack:** React, TypeScript, CSS, Vitest, Testing Library.

## Global Constraints

- No new charting dependency.
- Normalize scores from `0..100` to `0..1`.
- Keep card UI compact and responsive.

---

### Task 1: Trend Chart Rendering

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.test.tsx`
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `SkillEvaluationSnapshot[]`
- Produces: `SkillTrendSparkline`, `SkillEvaluationHistoryList`, `MetricTrendChart`

- [ ] Add failing tests for sparkline, expanded multi-metric chart, and single-run empty state.
- [ ] Run `npm test -- --run src/components/WorkspacePanel.test.tsx` and verify failures.
- [ ] Implement SVG path helpers and components.
- [ ] Add CSS for sparkline, expandable chart, legend, and empty state.
- [ ] Run `npm test -- --run src/components/WorkspacePanel.test.tsx`.

### Task 2: Regression Verification

**Files:**
- Test: `frontend/src/lib/api.test.ts`
- Test: `frontend/src/components/WorkspacePanel.test.tsx`
- Test: `backend/tests/test_postgres_long_term_memory.py`
- Test: `backend/tests/test_skill_evaluation.py`

- [ ] Run frontend focused tests.
- [ ] Run backend history tests.
- [ ] Confirm all commands pass.
