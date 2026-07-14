# Agent Engineering Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Agent Engineering Platform vertical slice with hierarchical traces, durable evaluation runs and regression gates, checkpoint state diffing, blinded SBS review, a React workspace, and a detailed Chinese delivery report.

**Architecture:** Reuse `agent_execution_logs`, `AgentHarness`, ClawEval, PostgreSQL memory, checkpoint replay, and the existing workspace. Add focused modules for trace reconstruction, EvalOps comparison, replay diffing, and SBS; expose them through backward-compatible FastAPI endpoints and one frontend engineering workspace.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, LangGraph, PostgreSQL/psycopg, React 19, TypeScript, Vitest, pytest, Ruff.

## Global Constraints

- Work directly on `master`; the user explicitly declined worktree isolation.
- Preserve existing uncommitted `.gitignore`, root `package.json`, and root `package-lock.json` changes.
- Follow strict RED-GREEN-REFACTOR TDD for every production-code change.
- Existing API callers that only provide `thread_id` must continue to work.
- Agent execution must remain usable when Langfuse and external telemetry are disabled.
- Recording failures must not mask the original Agent error.
- Replay fork creation must never execute external tools.
- PostgreSQL schema changes must be additive and idempotent.
- Final documentation path is `C:\idea\langgraph-claw\Agent工程平台建设报告.md`.

## File Map

- Create `backend/src/personal_assistant/observability/traces.py` for trace context, redaction, hierarchy, and summaries.
- Create `backend/src/personal_assistant/skills/evaluation/ops.py` for EvalRun records and regression comparison.
- Create `backend/src/personal_assistant/skills/evaluation/regression_cli.py` for CI gating.
- Create `backend/src/personal_assistant/debugging/replay.py` for checkpoint lookup, diff, and fork descriptors.
- Create `backend/src/personal_assistant/skills/evaluation/sbs.py` for blinded review and export.
- Modify `backend/src/personal_assistant/api/schemas.py` and `server.py` for platform APIs.
- Modify `backend/src/personal_assistant/memory/postgres.py` for trace/EvalOps/SBS persistence.
- Modify `backend/src/personal_assistant/agent/harness.py`, `agent.py`, and `multi_agent.py` for trace propagation.
- Create `frontend/src/components/EngineeringPanel.tsx` and its tests.
- Modify `frontend/src/lib/api.ts`, `App.tsx`, `Sidebar.tsx`, and `App.css`.
- Create backend test modules for traces, EvalOps, CLI, replay, and SBS.
- Create `Agent工程平台建设报告.md` and link it from `README.md`.

---

### Task 1: Trace Context and Read Model

**Files:**
- Create: `backend/src/personal_assistant/observability/__init__.py`
- Create: `backend/src/personal_assistant/observability/traces.py`
- Test: `backend/tests/test_agent_traces.py`

**Interfaces:**
- Produces: `TraceContext.create(thread_id, metadata=None)`, `TraceContext.child()`, `context_from_config(config)`, `trace_metadata(context)`, `redact_payload(value)`, and `build_trace_view(logs, trace_id)`.
- Consumes: existing `ExecutionLog` fields.

- [ ] **Step 1: Write failing context and redaction tests**

```python
def test_trace_context_child_preserves_trace_and_sets_parent():
    root = TraceContext.create("thread-1")
    child = root.child()
    assert child.trace_id == root.trace_id
    assert child.parent_span_id == root.span_id
    assert child.span_id != root.span_id

def test_redact_payload_hides_nested_secrets_and_truncates_text():
    value = redact_payload({"api_key": "secret", "nested": {"password": "pw"}, "text": "x" * 3000})
    assert value["api_key"] == "[REDACTED]"
    assert value["nested"]["password"] == "[REDACTED]"
    assert value["text"].endswith("…[truncated]")
```

- [ ] **Step 2: Verify RED**

Run: `cd backend && uv run pytest tests/test_agent_traces.py -q`

Expected: collection fails because the observability module does not exist.

- [ ] **Step 3: Implement immutable context and redaction**

```python
@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    run_id: str
    span_id: str
    parent_span_id: str | None
    thread_id: str
    metadata: dict[str, Any]

    @classmethod
    def create(cls, thread_id: str, metadata: dict[str, Any] | None = None) -> "TraceContext":
        span_id = uuid4().hex
        return cls(uuid4().hex, uuid4().hex, span_id, None, thread_id, dict(metadata or {}))

    def child(self, **metadata: Any) -> "TraceContext":
        return replace(self, span_id=uuid4().hex, parent_span_id=self.span_id,
                       metadata={**self.metadata, **metadata})
```

- [ ] **Step 4: Add failing topology tests**

```python
def test_build_trace_view_reconstructs_children_and_aggregates():
    view = build_trace_view([root_log, llm_log, failed_tool_log], "trace-1")
    assert view.summary.total_spans == 3
    assert view.summary.total_tokens == 42
    assert view.summary.error_count == 1
    assert view.roots[0].children[0].span.name == "llm"
```

- [ ] **Step 5: Implement `TraceSpan`, `TraceNode`, `TraceSummary`, and `TraceView`**

Index by `run_id`, attach using `parent_id`, place orphans under a synthetic root, and sort by `created_at` then `id`.

- [ ] **Step 6: Verify GREEN and commit**

Run: `cd backend && uv run pytest tests/test_agent_traces.py -q`

Commit: `feat(observability): add agent trace context and topology`.

### Task 2: Trace Persistence, APIs, and Instrumentation

**Files:**
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Modify: `backend/src/personal_assistant/agent/agent.py`
- Modify: `backend/src/personal_assistant/agent/multi_agent.py`
- Test: `backend/tests/test_agent_traces.py`
- Test: `backend/tests/test_execution_logs.py`

**Interfaces:**
- Consumes: Task 1 trace functions.
- Produces: `list_trace_logs`, `list_thread_trace_ids`, `GET /api/traces/{trace_id}`, and `GET /api/threads/{thread_id}/traces`.

- [ ] **Step 1: Add failing PostgreSQL query tests**

Assert trace queries filter `metadata->>'trace_id'`, preserve `run_id/parent_id`, and map rows to `ExecutionLog`.

- [ ] **Step 2: Implement trace query methods and verify GREEN**

Run: `cd backend && uv run pytest tests/test_execution_logs.py tests/test_agent_traces.py -q`.

- [ ] **Step 3: Add failing harness hierarchy test**

```python
async def test_run_user_turn_records_hierarchical_trace():
    await harness.run_user_turn("thread-1", "hello")
    root = next(log for log in memory.execution_logs if log.event_type == "turn" and log.status == "completed")
    llm = next(log for log in memory.execution_logs if log.event_type == "llm")
    assert root.run_id
    assert llm.parent_id == root.run_id
    assert llm.metadata["trace_id"] == root.metadata["trace_id"]
```

- [ ] **Step 4: Propagate context in `RunnableConfig.configurable`**

Create a root context in sync and streaming turns; populate `run_id`, `parent_id`, and redacted trace metadata for LLM, tool, retry, approval, security, and multi-Agent logs.

- [ ] **Step 5: Replace multi-Agent `duration_ms=0` with measured time**

Wrap supervisor, child, gate, and synthesize operations with `time.perf_counter()` and record child spans.

- [ ] **Step 6: Write failing API tests and implement trace endpoints**

```python
def test_get_trace_returns_summary_and_roots(client):
    response = client.get("/api/traces/trace-1")
    assert response.status_code == 200
    assert response.json()["summary"]["trace_id"] == "trace-1"
```

- [ ] **Step 7: Verify and commit**

Run: `cd backend && uv run pytest tests/test_agent_traces.py tests/test_execution_logs.py tests/test_tracing.py tests/test_multi_agent_graph.py -q`.

Commit: `feat(observability): instrument hierarchical agent traces`.

### Task 3: Durable EvalRun and EvalCaseResult

**Files:**
- Create: `backend/src/personal_assistant/skills/evaluation/ops.py`
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Test: `backend/tests/test_evaluation_ops.py`

**Interfaces:**
- Produces: `EvaluationRun`, `EvaluationCaseResult`, `create_run_snapshot`, `case_result_from_detail`, CRUD methods, and evaluation-run list/detail APIs.
- Consumes: `SkillEvaluationReport`, `CaseEvaluationDetail`, settings, dataset file, and trace metadata.

- [ ] **Step 1: Write failing snapshot tests**

```python
def test_create_run_snapshot_hashes_dataset_and_captures_versions():
    run = create_run_snapshot(run_id="eval-1", mode="e2e", agent_mode="single",
                              dataset_path=golden_path, settings=settings, git_sha="abc123")
    assert len(run.dataset_hash) == 64
    assert run.git_sha == "abc123"
    assert run.status == "running"
```

- [ ] **Step 2: Implement Pydantic records, SHA-256 dataset hashing, and secret-safe config snapshots**

- [ ] **Step 3: Add failing schema and CRUD tests**

Assert creation of `evaluation_runs` and `evaluation_case_results`, uniqueness on `(run_id, case_id)`, complete report retention, and ordered retrieval.

- [ ] **Step 4: Implement additive tables and CRUD**

Add `_setup_evaluation_runs`, `create_evaluation_run`, `record_evaluation_case_result`, `complete_evaluation_run`, `list_evaluation_runs`, and `get_evaluation_run`.

- [ ] **Step 5: Add failing SSE lifecycle tests**

Verify `started` contains `run_id`, each case is persisted with trace/thread correlation, case errors mark the run incomplete, and `done` stores the full report.

- [ ] **Step 6: Integrate the lifecycle and APIs**

Create the row before the first SSE event, record every detail, finalize to `completed`, `incomplete`, or `failed`, and expose list/detail endpoints.

- [ ] **Step 7: Verify and commit**

Run: `cd backend && uv run pytest tests/test_evaluation_ops.py tests/test_execution_logs.py tests/test_skill_evaluation.py tests/test_agent_evaluation_diagnostics.py -q`.

Commit: `feat(eval): persist complete evaluation runs and cases`.

### Task 4: Regression Comparator and CI Gate

**Files:**
- Modify: `backend/src/personal_assistant/skills/evaluation/ops.py`
- Create: `backend/src/personal_assistant/skills/evaluation/regression_cli.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Test: `backend/tests/test_evaluation_ops.py`
- Test: `backend/tests/test_regression_cli.py`

**Interfaces:**
- Produces: `RegressionThresholds`, `RegressionFinding`, `EvaluationComparison`, `compare_evaluation_runs`, comparison API, and CLI.
- Consumes: persisted runs from Task 3.

- [ ] **Step 1: Write failing comparison tests**

Cover pass-to-fail, safety regression, forbidden tools, missing cases, pass-rate thresholds, latency/token warnings, and fail-to-pass improvements.

```python
def test_safety_pass_to_fail_blocks_candidate():
    result = compare_evaluation_runs(baseline, candidate)
    assert result.status == "failed"
    assert any(item.rule == "safety_pass_to_fail" for item in result.findings)
```

- [ ] **Step 2: Implement one evidence-rich comparator**

Each finding contains `rule`, `severity`, `case_id`, `baseline`, `candidate`, and `message`.

- [ ] **Step 3: Add failing report/exit-code tests**

Assert CLI exit `0` for pass/warnings, `1` for failed gates, and `2` for invalid or incomplete inputs.

- [ ] **Step 4: Implement JSON/Markdown report renderers and CLI**

Arguments: `--baseline-json`, `--candidate-json`, `--output-json`, and `--output-md`.

- [ ] **Step 5: Implement `POST /api/evaluations/compare` after failing API tests**

Reject missing or incomplete runs with HTTP 409 and reuse the exact comparator used by the CLI.

- [ ] **Step 6: Verify and commit**

Run: `cd backend && uv run pytest tests/test_evaluation_ops.py tests/test_regression_cli.py -q`.

Commit: `feat(eval): add explainable regression gate`.

### Task 5: Replay Diff and Safe Fork Descriptor

**Files:**
- Create: `backend/src/personal_assistant/debugging/__init__.py`
- Create: `backend/src/personal_assistant/debugging/replay.py`
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Test: `backend/tests/test_replay_debugging.py`

**Interfaces:**
- Produces: `CheckpointRef`, `StateChange`, `ReplayDiff`, `diff_checkpoint_states`, `find_checkpoint`, `create_fork_descriptor`, diff API, and fork API.
- Consumes: serialized states from `AgentHarness.replay`.

- [ ] **Step 1: Write failing recursive diff tests**

```python
def test_diff_checkpoint_states_groups_changes():
    diff = diff_checkpoint_states({"values": {"route": "a"}},
                                  {"values": {"route": "b", "tools": ["x"]}})
    assert "values.route" in [item.path for item in diff.changed]
    assert "values.tools" in [item.path for item in diff.added]
```

Also cover lists, messages, truncation, type changes, and unchanged inputs.

- [ ] **Step 2: Implement stable path diffing and message summaries**

Sort keys, summarize LangChain messages, and cap individual serialized values at 2,000 characters.

- [ ] **Step 3: Write failing lookup and fork safety tests**

Assert explicit missing-checkpoint errors, generated target thread IDs, retained provenance, and zero harness execution calls.

- [ ] **Step 4: Implement lookup, descriptor, and endpoints**

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/test_replay_debugging.py tests/test_checkpoint_replay.py -q`.

Commit: `feat(debugging): add checkpoint state diff and safe forks`.

### Task 6: Blinded SBS Review

**Files:**
- Create: `backend/src/personal_assistant/skills/evaluation/sbs.py`
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Test: `backend/tests/test_sbs_evaluation.py`

**Interfaces:**
- Produces: `SBSTask`, `SBSReview`, `present_blinded_task`, `canonical_winner`, `export_sbs_jsonl`, persistence, and `/api/sbs/*`.
- Consumes: manual candidate outputs or Task 4 regression pairs.

- [ ] **Step 1: Write failing blind-order and winner-mapping tests**

Use an injected random seed; verify identity hiding, swapped presentation, and display-to-canonical winner mapping.

- [ ] **Step 2: Implement SBS domain rules**

Require a reason for `both_bad`, enforce dimension scores from 1 to 5, and retain provenance outside blinded payloads.

- [ ] **Step 3: Add failing persistence tests**

Assert idempotent tables, revision increments instead of overwrite, task status changes, and ordered export.

- [ ] **Step 4: Implement tables, methods, endpoints, and NDJSON export**

- [ ] **Step 5: Verify and commit**

Run: `cd backend && uv run pytest tests/test_sbs_evaluation.py -q`.

Commit: `feat(eval): add blinded SBS review workflow`.

### Task 7: Engineering Frontend Workspace

**Files:**
- Create: `frontend/src/components/EngineeringPanel.tsx`
- Create: `frontend/src/components/EngineeringPanel.test.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: Tasks 2-6 APIs.
- Produces: one panel with `trace`, `regression`, `replay`, and `sbs` tabs.

- [ ] **Step 1: Add failing API client tests**

Cover `getTrace`, `listThreadTraces`, `listEvaluationRuns`, `compareEvaluationRuns`, `diffReplay`, `createReplayFork`, `listSBSTasks`, `getSBSTask`, `submitSBSReview`, and `exportSBS`.

- [ ] **Step 2: Implement clients and verify GREEN**

Run: `cd frontend && npm test -- --run src/lib/api.test.ts`.

- [ ] **Step 3: Write failing EngineeringPanel tests**

Cover trace tree, regression findings, replay changes, blinded SBS, required reason, loading, empty, and error states.

- [ ] **Step 4: Implement four compact tabs**

Use forms, tables, `<details>`, nested lists, and existing API error conventions.

- [ ] **Step 5: Add failing App/Sidebar routing tests, then wire `engineering` panel**

Expose a sidebar entry labelled `Agent Engineering`.

- [ ] **Step 6: Add responsive styles, verify, and commit**

Run: `cd frontend && npm test -- --run src/components/EngineeringPanel.test.tsx src/lib/api.test.ts src/App.test.tsx src/components/Sidebar.test.tsx`.

Commit: `feat(web): add agent engineering workspace`.

### Task 8: Documentation and Full Verification

**Files:**
- Create: `Agent工程平台建设报告.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified behavior and command output from Tasks 1-7.
- Produces: the requested detailed Chinese report.

- [ ] **Step 1: Run backend lint**

Run: `cd backend && uv run ruff check src tests`.

Expected: exit code 0; fix only task-related violations.

- [ ] **Step 2: Run complete backend tests**

Run: `cd backend && uv run pytest -q`.

Expected: zero failures.

- [ ] **Step 3: Run complete frontend tests and build**

Run: `cd frontend && npm test -- --run`.

Run: `cd frontend && npm run build`.

Expected: both commands exit 0.

- [ ] **Step 4: Write the root Chinese report**

Include JD mapping, before/after comparison, Mermaid architecture/trace/eval/replay/SBS flows, module explanations, database/API contracts, UI guide, CI commands, safety behavior, verified test output, trade-offs, limitations, and roadmap.

- [ ] **Step 5: Link the report and check documentation**

Run: `rg -n "TBD|TODO|PLACEHOLDER|FIXME" Agent工程平台建设报告.md README.md`.

Run: `git diff --check`.

Run: `git status --short`.

Expected: no placeholders or whitespace errors; unrelated user files remain unstaged.

- [ ] **Step 6: Audit every design acceptance criterion and commit**

Record limitations honestly, then commit with `docs: explain agent engineering platform`.

## Execution Decision

The user explicitly requested direct work on `master` and did not request subagent delegation. Execute inline in the current session with `superpowers:executing-plans`, preserving checkpoints between backend, frontend, and documentation phases.
