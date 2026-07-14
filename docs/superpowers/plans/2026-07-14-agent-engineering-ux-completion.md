# Agent Engineering UX Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Agent Engineering workspace so users can inspect traces, create and compare EvalRuns, and create/review SBS tasks without hidden prerequisite screens.

**Architecture:** Keep backend contracts intact and add the missing frontend producer flows. `EngineeringPanel` orchestrates active-tool state, small local components render focused workflows, `api.ts` supplies exact transport types, and `App.css` provides a fixed-row workspace with bounded internal scrolling.

**Tech Stack:** React 19, TypeScript 6, Vitest, Testing Library, MSW, Vite 8, existing FastAPI/Postgres APIs.

## Global Constraints

- Follow RED-GREEN-REFACTOR for every behavior change.
- Preserve the existing huamulan palette and typography; add no dependency.
- Keep header and tab rows intrinsic-height and put overflow in the active module row.
- Use only real persisted EvalRuns and existing SBS endpoints; do not seed fake comparison data.
- Preserve unrelated dirty worktree files and stage only task-owned paths.

---

### Task 1: Complete frontend transport contracts

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Consumes: existing `/api/skills/evaluation/run/stream` and `POST /api/sbs/tasks` contracts.
- Produces: `SkillEvaluationStreamEvent.run_id`, `SBSCandidate`, full `SBSTask`, and `api.createSBSTask(task)`.

- [ ] **Step 1: Write the failing SBS client test**

Add an MSW handler that captures `POST /api/sbs/tasks`, call `api.createSBSTask`, and assert the exact task payload and returned `task_id`.

```ts
it('creates an SBS task', async () => {
  const task: SBSTask = {
    task_id: 'sbs-new', prompt: 'Which answer is better?', status: 'pending', provenance: {},
    candidate_a: { candidate_id: 'baseline', output: 'A' },
    candidate_b: { candidate_id: 'candidate', output: 'B' },
  }
  server.use(http.post(`${BASE}/api/sbs/tasks`, async ({ request }) => {
    expect(await request.json()).toEqual(task)
    return HttpResponse.json(task)
  }))
  await expect(api.createSBSTask(task)).resolves.toMatchObject({ task_id: 'sbs-new' })
})
```

- [ ] **Step 2: Run the focused API test and verify RED**

Run: `npm test -- --run src/lib/api.test.ts`

Expected: TypeScript/runtime failure because `SBSTask` lacks candidate fields and `createSBSTask` does not exist.

- [ ] **Step 3: Implement the minimal transport types and method**

Add candidate fields to `SBSTask`, add `run_id` to started/done evaluation events, and add:

```ts
createSBSTask: (task: SBSTask) => request<SBSTask>('/api/sbs/tasks', {
  method: 'POST', body: JSON.stringify(task),
}),
```

- [ ] **Step 4: Run the focused API test and verify GREEN**

Run: `npm test -- --run src/lib/api.test.ts`

Expected: all API tests pass.

### Task 2: Add Trace evidence disclosures and stable scroll layout

**Files:**
- Modify: `frontend/src/components/EngineeringPanel.test.tsx`
- Modify: `frontend/src/components/EngineeringPanel.tsx`
- Modify: `frontend/src/AppCss.test.ts`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `TraceSpan.input`, `output`, `error`, `metadata`, `token_usage`, IDs, timestamps, and status.
- Produces: accessible `details/summary` span disclosures and a three-row scroll layout.

- [ ] **Step 1: Write failing component and CSS tests**

Extend the trace fixture with input/output/token/metadata, click the trace, expand `user_turn`, and assert visible `Input`, `Output`, `Tokens`, `Span ID`, and status evidence. Add CSS assertions for:

```ts
expect(appCss).toMatch(/\.engineering-workspace\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0,\s*1fr\)/s)
expect(appCss).toMatch(/\.engineering-grid\s*\{[^}]*min-height:\s*0/s)
expect(appCss).toMatch(/\.evidence-index,\s*\.evidence-canvas\s*\{[^}]*overflow:\s*auto/s)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `npm test -- --run src/components/EngineeringPanel.test.tsx src/AppCss.test.ts`

Expected: trace detail labels and fixed-row/overflow CSS are absent.

- [ ] **Step 3: Implement disclosure and layout**

Render each span as:

```tsx
<details className={`trace-branch status-${span.status}`}>
  <summary className="trace-node">...</summary>
  <div className="trace-detail">...</div>
</details>
```

Render only non-empty JSON payload sections through a bounded `<pre>`. Change the workspace CSS to `height: 100%; min-height: 0; grid-template-rows: auto auto minmax(0, 1fr); overflow: hidden`, set module grids/forms to `min-height: 0; overflow: auto`, and preserve the mobile single-column scroll behavior.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `npm test -- --run src/components/EngineeringPanel.test.tsx src/AppCss.test.ts`

Expected: all focused tests pass.

### Task 3: Create EvalRuns inside Regression

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/EngineeringPanel.test.tsx`
- Modify: `frontend/src/components/EngineeringPanel.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `agentMode`, `api.listSkillEvaluationDatasets(agentMode)`, `api.runSkillEvaluationStream(request)`, and `api.listEvaluationRuns()`.
- Produces: inline dataset/mode controls, progress, refreshed run options, automatic baseline/candidate selection, and disabled invalid comparisons.

- [ ] **Step 1: Write failing Regression workflow tests**

Mock datasets, an evaluation stream with `started`, `case_progress`, and `done`, and a refreshed completed run. Assert the empty-state instruction, run controls, request body including `agent_mode`, progress, and automatic baseline selection. Add a separate test that identical or incomplete runs cannot execute the gate.

```ts
api.runSkillEvaluationStream.mockImplementation(async function* () {
  yield { type: 'started', run_id: 'run-new', mode: 'quick', source: 'golden:smoke', total: 1, completed: 0 }
  yield { type: 'done', run_id: 'run-new', mode: 'quick', source: 'golden:smoke', total: 1, completed: 1, percent: 100, results: [] }
})
```

- [ ] **Step 2: Run the component test and verify RED**

Run: `npm test -- --run src/components/EngineeringPanel.test.tsx`

Expected: run controls, stream call, progress, and automatic selection are absent.

- [ ] **Step 3: Implement the EvalRun producer**

Pass `agentMode` from `App` to `EngineeringPanel`. Load datasets and runs on Regression entry. Stream the selected Quick/E2E run, retain `run_id`, show progress, refresh runs on `done`, and select the new completed run as baseline when baseline is empty or candidate otherwise. Filter gate choices/actions to distinct completed runs and format option labels with date, dataset, mode, case counts, and status.

- [ ] **Step 4: Run the component test and verify GREEN**

Run: `npm test -- --run src/components/EngineeringPanel.test.tsx src/App.test.tsx`

Expected: Regression workflow and App integration tests pass.

### Task 4: Add module guidance and complete SBS creation/review loop

**Files:**
- Modify: `frontend/src/components/EngineeringPanel.test.tsx`
- Modify: `frontend/src/components/EngineeringPanel.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `api.createSBSTask`, `api.listSBSTasks`, `api.getSBSTask`, `api.submitSBSReview`, and Regression findings.
- Produces: module introductions, manual SBS creation, Regression prefill, queue refresh, and review completion status.

- [ ] **Step 1: Write failing module-guidance and SBS tests**

Assert every tab exposes a one-sentence module description. In SBS, fill Prompt, Candidate A, and Candidate B, submit, assert the backend task contract, and assert the new task opens blinded. In Regression, render a finding with baseline/candidate values, click `Review side by side`, and assert SBS opens with prefilled values. Submit a review and assert the queue refreshes and a completion status appears.

- [ ] **Step 2: Run the component test and verify RED**

Run: `npm test -- --run src/components/EngineeringPanel.test.tsx`

Expected: descriptions, create-task controls, prefill action, and completion status are absent.

- [ ] **Step 3: Implement the SBS loop and guidance**

Add a `ToolIntro` at the top of each active module. Add controlled SBS creation fields and create:

```ts
const task: SBSTask = {
  task_id: `sbs-${crypto.randomUUID()}`,
  prompt: sbsDraft.prompt.trim(),
  candidate_a: { candidate_id: sbsDraft.candidateAId || 'baseline', output: sbsDraft.candidateA.trim() },
  candidate_b: { candidate_id: sbsDraft.candidateBId || 'candidate', output: sbsDraft.candidateB.trim() },
  status: 'pending', provenance: sbsDraft.provenance,
}
```

After creation, refresh and open the task. From a Regression finding, set prompt to the finding message, stringify baseline/candidate values into outputs, record run IDs in provenance, and switch to SBS. After review, refresh, clear the selected task, reset review fields, and show `Review saved`.

- [ ] **Step 4: Run the component test and verify GREEN**

Run: `npm test -- --run src/components/EngineeringPanel.test.tsx`

Expected: all Agent Engineering component tests pass.

### Task 5: Verify the complete correction

**Files:**
- Verify only: all modified files above.

**Interfaces:**
- Consumes: the completed implementation and local backend/frontend.
- Produces: fresh automated and browser evidence for every acceptance criterion.

- [ ] **Step 1: Run frontend verification**

Run: `npm test -- --run` then `npm run build` then `npm run lint` from `frontend`.

Expected: zero failing tests, successful production build, and zero lint errors.

- [ ] **Step 2: Run relevant backend verification**

Run: `pytest backend/tests/test_sbs_evaluation.py backend/tests/test_evaluation_ops.py backend/tests/test_agent_traces.py -q` from the repository root.

Expected: all selected backend tests pass.

- [ ] **Step 3: Verify in the local browser**

Measure tab heights on Trace, Regression, Replay Diff, and SBS; confirm equal values. Confirm Trace and module canvases report `overflow: auto`. Exercise Trace disclosure, run empty state/producer controls, SBS creation, and saved-review status against the local app.

- [ ] **Step 4: Review the diff and working tree**

Run: `git diff --check`, `git diff --stat`, and `git status --short`.

Expected: no whitespace errors; only task-owned frontend/spec/plan files are staged or committed; pre-existing user changes remain untouched.
