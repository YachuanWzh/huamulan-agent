# Agent Engineering UX Completion Design

## Goal

Turn the existing Agent Engineering shell into a usable end-to-end workspace. A user must be able to inspect trace evidence, create and compare real evaluation runs, create SBS tasks, complete blinded reviews, and understand each module without leaving the workspace to discover hidden prerequisites.

## Confirmed Root Causes

- Trace nodes render only name, kind, and duration even though the API already returns status, start time, token usage, input, output, error, and metadata.
- The application body is a fixed-height grid with hidden overflow. `engineering-workspace` is also a grid with stretch alignment, so short Regression and Replay content expands the header and tab rows while long Trace content is clipped.
- Module names have no in-product explanation or empty-state action.
- Regression only consumes persisted `EvaluationRun` rows. Those rows are produced by the separate Skill evaluation stream, and the current database returns an empty list, so both selectors are empty.
- SBS only consumes existing tasks. The backend has `POST /api/sbs/tasks`, but the frontend has no client method or creation form.

## Chosen Approach

Build a closed loop inside Agent Engineering and reuse the existing evaluation stream and SBS APIs. This is preferred over linking users to the separate Skill review screen because it keeps the workflow coherent, and preferred over seed/demo data because comparisons must be based on real persisted runs.

The existing huamulan visual language remains intact. The design signature is an evidence workflow rail: each module starts with one sentence explaining the decision it supports and presents its required producer action in the same surface as its result.

## Layout Contract

- `engineering-workspace` occupies the available application-body height with rows `auto auto minmax(0, 1fr)`.
- Header and tabs keep intrinsic height across every selected module.
- The active module owns the remaining row and scrolls internally.
- Trace index and trace canvas scroll independently so long traces do not disappear below the viewport.
- Mobile uses a single scrolling column while preserving visible keyboard focus and reduced-motion behavior.

## Module Design

### Trace

- Show summary facts for latency, spans, tokens, tools, retries, and errors.
- Render every span as an accessible disclosure control.
- The collapsed row shows name, kind, status, and duration.
- The expanded panel shows start time, span and parent IDs, token usage, input, output, error, and metadata. Empty payload sections are omitted.
- JSON payloads wrap and have bounded height with their own overflow.
- The trace index indicates the selected trace and both columns remain scrollable.

### Regression

- Explain that Regression compares two persisted runs over the same dataset.
- Add an inline EvalRun producer using the existing Golden Dataset selector and Quick/E2E evaluation stream.
- Inherit the global single-agent or multi-agent mode by passing `agentMode` from `App` to `EngineeringPanel`.
- Show evaluation progress and the current case. On completion, refresh persisted runs.
- If no baseline is selected, the first newly completed run becomes the baseline. Otherwise the new run becomes the candidate.
- Run options display creation time, dataset label/path, mode, status, completed case count, and pass rate when available.
- Only two distinct completed runs can execute the gate. Empty, loading, incomplete, and error states state the next action.
- Comparison results keep the gate outcome and findings; findings with case output evidence can prefill an SBS task.

### Replay Diff

- Explain that Replay Diff compares two checkpoints without executing tools.
- Keep the existing diff and safe-fork descriptor actions.
- Clarify missing thread/checkpoint prerequisites and keep large change values scrollable.

### SBS Review

- Explain blinded A/B review and provenance.
- Add a task-creation panel with prompt, candidate A output, candidate B output, and optional candidate IDs. Generate a task ID when the user does not provide one.
- Add `createSBSTask` to the frontend API client and send the backend `SBSTask` contract.
- After creation, refresh the queue and open the blinded task returned by the read endpoint.
- Regression can switch to SBS and prefill the same creation form. Manual creation remains available.
- After a review is saved, refresh the queue, clear the active review, and show a success status so the task does not appear pending indefinitely.

## Component Boundaries

- `EngineeringPanel.tsx` owns module selection and shared orchestration.
- Small local components own module introduction, EvalRun creation/progress, trace disclosure, and SBS task creation to keep render logic testable.
- `api.ts` owns transport types and client methods only; it does not contain UI state.
- `App.css` owns the fixed-row layout and bounded scroll surfaces.
- Existing backend contracts remain unchanged unless testing reveals that the current POST schema cannot support the UI contract.

## Error Handling

- API failures appear in the active module with the failed action named.
- An empty run list says that no EvalRuns exist and offers the create-run controls.
- An evaluation stream case error keeps progress visible and the persisted run remains incomplete; incomplete runs cannot be selected for a gate.
- SBS validation requires non-empty prompt and outputs; both-bad reviews still require a reason.
- Creation and review buttons remain disabled while their action is running to prevent duplicates.

## Testing Strategy

All behavior follows RED-GREEN-REFACTOR.

- Component tests cover stable tab height classes/layout contract, module descriptions, independent scroll CSS, trace disclosure details, empty Regression state, EvalRun stream creation and auto-selection, distinct completed-run gate validation, SBS manual creation, Regression-to-SBS prefill, and post-review refresh.
- API tests cover the new SBS task client request and existing evaluation stream contract usage.
- Existing backend tests are rerun because the frontend relies on persistence and blinded-task contracts.
- Browser verification measures tab heights before and after switching modules, confirms scroll containers have `scrollHeight > clientHeight` with `overflow: auto`, expands a real trace, creates or observes real EvalRuns, and exercises SBS task creation/review against the local backend.
- Final verification includes focused tests, the full frontend suite, production build, relevant backend suites, and a diff review that excludes unrelated user changes.

## Acceptance Criteria

- Trace exposes meaningful span evidence beyond duration and remains usable with more spans than fit in the viewport.
- Trace, Regression, Replay Diff, and SBS tabs have the same measured height before and after switching.
- Every module states what it does and how to get started.
- A user can create a persisted EvalRun from Regression; completed runs populate both selectors without visiting another module.
- A user can compare two distinct completed runs and see an explainable outcome.
- A user can create an SBS task manually or from Regression evidence, then complete its blinded review from the SBS module.
- Automated tests and real browser checks demonstrate each acceptance criterion before completion is claimed.
