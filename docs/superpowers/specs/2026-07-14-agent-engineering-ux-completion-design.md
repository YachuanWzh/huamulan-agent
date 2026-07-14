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

## Chinese Copy Contract

- All static user-facing copy inside Agent Engineering is written in Simplified Chinese.
- Necessary technical abbreviations remain abbreviated where the abbreviation is the established product or engineering term: `SBS`, `ID`, `E2E`, `API`, `JSON`, `UX`, `A/B`, and `EvalRun`.
- Domain concepts such as Agent Engineering, Trace, Regression, Replay Diff, Golden Dataset, Baseline, Candidate, checkpoint, span, and token are translated into concise Chinese interface terms. Backend-provided identifiers, tool names, dataset names, rule names, paths, and evidence payloads remain unchanged because they are source evidence rather than interface copy.
- Enumerated backend states and kinds are mapped to Chinese display labels while their transport values remain unchanged.
- This contract applies only to Agent Engineering. The surrounding huamulan application shell and its thematic navigation copy are outside this module's localization scope.

## Layout Contract

- `engineering-workspace` occupies the available application-body height with rows `auto auto minmax(0, 1fr)`.
- Header and tabs keep intrinsic height across every selected module.
- The active module owns the remaining row and scrolls internally.
- Trace index and trace canvas scroll independently so long traces do not disappear below the viewport.
- Mobile uses a single scrolling column while preserving visible keyboard focus and reduced-motion behavior.

## Module Design

### Trace

**Purpose:** Trace is the execution-evidence module. It explains what happened during one Agent turn and where time, tokens, tool calls, retries, or failures were introduced.

**Inputs:** A selected conversation thread, its persisted trace summaries, and the complete span tree returned by the trace API. Each span may contain timestamps, identifiers, kind, status, duration, token usage, input, output, error, and metadata.

**Outputs:** A navigable execution spine, aggregate facts, and expandable span evidence. Trace does not mutate the Agent run or create new execution data.

**Workflow:** Select a thread, select a trace, inspect the aggregate facts, then expand the relevant spans and their payloads. The trace list and evidence canvas scroll independently so large runs remain inspectable.

**Out of scope:** Trace does not score answer quality, compare releases, replay checkpoints, rerun tools, or collect human preference.

- Show summary facts for latency, spans, tokens, tools, retries, and errors.
- Render every span as an accessible disclosure control.
- The collapsed row shows name, kind, status, and duration.
- The expanded panel shows start time, span and parent IDs, token usage, input, output, error, and metadata. Empty payload sections are omitted.
- JSON payloads wrap and have bounded height with their own overflow.
- The trace index indicates the selected trace and both columns remain scrollable.

### Regression

**Purpose:** Regression is the automated quality-gate module. It produces persisted EvalRuns from a Golden Dataset and determines whether a Candidate run improves, preserves, or degrades quality relative to a Baseline run.

**Inputs:** A Golden Dataset, Quick or E2E evaluation mode, the active single-agent or multi-agent mode, and two distinct completed EvalRuns for comparison.

**Outputs:** Persisted EvalRuns, evaluation progress, pass-rate changes, a gate status, and case-level findings. Findings that contain comparable evidence can be forwarded into SBS Review.

**Workflow:** Choose a Golden Dataset and evaluation mode, create at least two EvalRuns, select a completed Baseline and Candidate, then run the gate. The module refreshes persisted runs after evaluation so both selectors are populated without leaving Agent Engineering.

**Out of scope:** Regression does not invent demo runs, compare incomplete runs, perform blinded human review, or replace the underlying Golden Dataset authoring workflow.

- Explain that Regression compares two persisted runs over the same dataset.
- Add an inline EvalRun producer using the existing Golden Dataset selector and Quick/E2E evaluation stream.
- Inherit the global single-agent or multi-agent mode by passing `agentMode` from `App` to `EngineeringPanel`.
- Show evaluation progress and the current case. On completion, refresh persisted runs.
- If no baseline is selected, the first newly completed run becomes the baseline. Otherwise the new run becomes the candidate.
- Run options display creation time, dataset label/path, mode, status, completed case count, and pass rate when available.
- Only two distinct completed runs can execute the gate. Empty, loading, incomplete, and error states state the next action.
- Comparison results keep the gate outcome and findings; findings with case output evidence can prefill an SBS task.

### Replay Diff

**Purpose:** Replay Diff is the checkpoint-state inspection module. It shows how persisted Agent state changed between two checkpoints and can describe a provenance-preserving branch point without executing tools.

**Inputs:** A Before checkpoint ID and an After checkpoint ID from the same thread. Safe-fork description additionally requires a source thread and source checkpoint.

**Outputs:** Added, removed, and changed state paths plus an optional safe-fork descriptor containing source provenance and target state. All outputs are read-only descriptions.

**Workflow:** Enter two checkpoint IDs, request the state diff, inspect the changed values, and optionally describe a safe fork from a chosen source checkpoint.

**Out of scope:** Replay Diff does not invoke the Agent, execute tools, write checkpoint state, or automatically start the described fork.

- Explain that Replay Diff compares two checkpoints without executing tools.
- Keep the existing diff and safe-fork descriptor actions.
- Clarify missing thread/checkpoint prerequisites and keep large change values scrollable.

### SBS Review

**Purpose:** SBS Review is the human-preference module. It turns two candidate outputs into a blinded A/B task so a reviewer can select the stronger result and record an auditable reason without seeing candidate identity.

**Inputs:** A shared prompt plus two model/Agent-mode configurations. The backend runs both configurations through the project's `AgentHarness` in parallel and records their outputs and Trace provenance. Existing outputs may still be imported manually or prefilled from Regression findings. Review input includes reviewer, winner, reason, and optional dimension scores.

**Outputs:** A persisted SBS task, a randomized blinded candidate order, and a persisted review mapped back to canonical candidate identities by the backend.

**Workflow:** Configure two distinct candidates, run the same prompt through both project Agent paths, automatically create a task from the completed outputs, then read the blinded candidates, choose A, B, Tie, or Both bad, enter the required review evidence, and save. The queue refreshes and the reviewed task no longer appears pending.

**Out of scope:** SBS Review does not expose model identity before judgment, automatically choose a winner, auto-approve risky tools, or replace automated Regression gates. It uses the project's evaluation approval policy so read-only safe tools can run without interruption while dangerous or mutating calls remain blocked.

- Explain blinded A/B review and provenance.
- Add a primary run panel with a shared prompt, model and Agent mode for configuration 1/2, plus run progress. Keep manual output import in a collapsed advanced section.
- Add `GET /api/sbs/run-options` and `POST /api/sbs/tasks/run`; run both candidates concurrently via `AgentHarness`, attach independent thread/Trace evidence, and persist only when both outputs complete.
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
