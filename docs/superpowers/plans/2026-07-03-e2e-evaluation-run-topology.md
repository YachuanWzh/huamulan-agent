# E2E Evaluation Run Topology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an animated per-case run topology during e2e Skill evaluation so users can see the active case flow and final execution path.

**Architecture:** Keep the change frontend-first: consume existing `SkillEvaluationStreamEvent` data, remember the latest e2e `case_progress.detail`, derive a topology model from checks, routing trace, logs, tools, final answer, and judge output, then render it above the progress bar. The topology resets naturally because each new `case_progress` replaces the active detail.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, CSS animations.

## Global Constraints

- Strict TDD: write failing tests before production code.
- Do not modify unrelated user changes.
- Show topology only for e2e evaluation progress, not quick evaluation.
- Adapt nodes to actual case data: safety-only, routing-only, tool-backed, answer, judge, and failed stages.
- Include motion: animated active node, flowing connectors, and case-change entry animation with reduced-motion support.

---

### Task 1: Track Active E2E Case Progress

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Test: `frontend/src/components/WorkspacePanel.test.tsx`

**Interfaces:**
- Consumes: `SkillEvaluationStreamEvent` union from `frontend/src/lib/api.ts`.
- Produces: `activeEvaluationCase: CaseEvaluationDetail | null` state and a reset when a new evaluation starts.

- [ ] **Step 1: Write the failing test**

Add a test in `WorkspacePanel.test.tsx` that streams two e2e `case_progress` events and asserts the UI shows the latest case topology while the earlier case is gone.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- WorkspacePanel.test.tsx --runInBand`
Expected: FAIL because "运行拓扑" / active case topology is not rendered.

- [ ] **Step 3: Write minimal implementation**

In `WorkspacePanel.tsx`, add state for active e2e case detail, clear it at the start of `runSkillEvaluation`, and set it when `event.type === 'case_progress' && event.mode === 'e2e'`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- WorkspacePanel.test.tsx --runInBand`
Expected: PASS for the new test.

### Task 2: Derive and Render Adaptive Topology

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Test: `frontend/src/components/WorkspacePanel.test.tsx`

**Interfaces:**
- Consumes: `CaseEvaluationDetail`.
- Produces: `EvaluationRunTopology({ detail, running })` component.

- [ ] **Step 1: Write the failing test**

Add assertions that a tool-backed case renders lanes for input, routing, skill/tool execution, and answer, while a safety-blocked case renders safety and does not render tool execution.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- WorkspacePanel.test.tsx --runInBand`
Expected: FAIL because adaptive nodes do not exist.

- [ ] **Step 3: Write minimal implementation**

Create helper functions in `WorkspacePanel.tsx`: `buildTopologyNodes(detail)` and `topologyStageStatus(detail, stage)`. Render grouped lanes with nodes and connectors.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- WorkspacePanel.test.tsx --runInBand`
Expected: PASS.

### Task 3: Add Motion and Visual Styling

**Files:**
- Modify: `frontend/src/App.css`
- Test: `frontend/src/AppCss.test.ts`

**Interfaces:**
- Consumes: CSS classes emitted by `EvaluationRunTopology`.
- Produces: animated topology panel styles.

- [ ] **Step 1: Write the failing test**

Add CSS checks for `.evaluation-run-topology`, `.topology-node.is-active`, `.topology-connector::after`, `@keyframes topology-flow`, and reduced-motion coverage.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- AppCss.test.ts --runInBand`
Expected: FAIL because CSS classes and keyframes do not exist.

- [ ] **Step 3: Write minimal implementation**

Add CSS for a compact dark topology band, lane columns, node cards, status dots, flowing connector lines, active pulse, and reduced-motion overrides.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- AppCss.test.ts --runInBand`
Expected: PASS.

### Task 4: Full Verification

**Files:**
- Verify: `frontend/src/components/WorkspacePanel.test.tsx`
- Verify: `frontend/src/AppCss.test.ts`

- [ ] **Step 1: Run focused tests**

Run: `npm test -- WorkspacePanel.test.tsx AppCss.test.ts --runInBand`
Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run: `npm run build`
Expected: PASS.

