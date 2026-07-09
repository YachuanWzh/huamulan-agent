# TTFT Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce and expose first-token latency for chat streaming while leaving skill routing behavior unchanged.

**Architecture:** Add lightweight TTFT phase events in the streaming harness, cache compiled graph objects outside the hot path, and make non-routing pre-token work observable. Keep routing code untouched and update the technical report with the implemented optimization plan.

**Tech Stack:** Python 3.11, FastAPI SSE, LangGraph, pytest.

## Global Constraints

- Do not modify `backend/src/personal_assistant/agent/router.py` or skill routing behavior.
- Use TDD: each behavior change starts with a failing test.
- Keep changes scoped to `AgentHarness`, tests, and `技术方案报告.md`.
- Verify with focused pytest commands and a final relevant regression run.

---

### Task 1: Stream TTFT Phase Events

**Files:**
- Modify: `backend/tests/test_stream_error_handling.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`

**Interfaces:**
- Produces: SSE event `ttft_phase` with payload `{"phase": str, "elapsed_ms": int}`.

- [ ] **Step 1: Write the failing test**

```python
async def test_stream_emits_ttft_phase_before_first_token():
    harness = BackgroundReflectionHarness()
    chunks = [
        chunk async for chunk in harness.run_user_turn_stream("thread-1", "hello")
    ]
    first_token = next(i for i, chunk in enumerate(chunks) if "event: token" in chunk)
    phases_before_token = [
        chunk
        for chunk in chunks[:first_token]
        if "event: ttft_phase" in chunk
    ]
    assert any('"phase": "request_received"' in chunk for chunk in phases_before_token)
    assert any('"phase": "graph_compiled"' in chunk for chunk in phases_before_token)
    assert any('"phase": "llm_stream_started"' in chunk for chunk in phases_before_token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_stream_error_handling.py::test_stream_emits_ttft_phase_before_first_token -q`
Expected: FAIL because no `ttft_phase` event is emitted.

- [ ] **Step 3: Write minimal implementation**

Add a monotonic request timer in `run_user_turn_stream()` and yield `_sse_event("ttft_phase", ...)` at request start, after prompt guard, after graph compile, immediately before graph streaming, and on first visible token.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_stream_error_handling.py::test_stream_emits_ttft_phase_before_first_token -q`
Expected: PASS.

### Task 2: Cache Compiled Graphs

**Files:**
- Modify: `backend/tests/test_agent_split.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`

**Interfaces:**
- Produces: `AgentHarness._compile_cached(llm_config, enable_memory_reflection=False, requires_approval=None, multi_agent=False)`.

- [ ] **Step 1: Write the failing test**

```python
def test_compile_cached_reuses_single_agent_graph(monkeypatch):
    calls = []
    sentinel = object()

    def fake_compile_agent(settings, registry, memory, decisions, llm_config=None, **kwargs):
        calls.append((llm_config, kwargs.get("enable_memory_reflection")))
        return sentinel

    monkeypatch.setattr(agent_module, "compile_agent", fake_compile_agent)
    harness = AgentHarness(settings="settings", registry="registry", memory="memory")

    assert harness._compile_cached("llm-config", enable_memory_reflection=False) is sentinel
    assert harness._compile_cached("llm-config", enable_memory_reflection=False) is sentinel
    assert calls == [("llm-config", False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_agent_split.py::test_compile_cached_reuses_single_agent_graph -q`
Expected: FAIL because `_compile_cached` does not exist.

- [ ] **Step 3: Write minimal implementation**

Initialize `self._compiled_app_cache = {}` in `AgentHarness.__init__`, compute a stable cache key from mode, reflection flag, llm config model/base/temperature identity, and approval policy identity, then use cached compile in streaming hot paths.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_agent_split.py::test_compile_cached_reuses_single_agent_graph -q`
Expected: PASS.

### Task 3: Update Technical Report

**Files:**
- Modify: `技术方案报告.md`

**Interfaces:**
- Produces: Section `## TTFT 优化方案与落地记录`.

- [ ] **Step 1: Add report verification test**

Run a PowerShell check that fails when `技术方案报告.md` does not contain `TTFT 优化方案与落地记录`.

- [ ] **Step 2: Verify failure**

Run: `Select-String -Path 技术方案报告.md -Pattern "TTFT 优化方案与落地记录" -Quiet; if ($?) { exit 1 }`
Expected: exit 1 before the report is updated.

- [ ] **Step 3: Append the report section**

Append a concise Chinese section covering measurement, graph compile cache, prompt guard/RAG/compaction strategy, and follow-up tuning items.

- [ ] **Step 4: Verify report section exists**

Run: `Select-String -Path 技术方案报告.md -Pattern "TTFT 优化方案与落地记录" -Quiet`
Expected: exit 0.

### Task 4: Regression Verification

**Files:**
- Test only.

**Interfaces:**
- Consumes: all changes above.

- [ ] **Step 1: Run focused backend tests**

Run: `pytest backend/tests/test_stream_error_handling.py backend/tests/test_agent_split.py -q`
Expected: PASS.

- [ ] **Step 2: Inspect diff**

Run: `git diff -- backend/src/personal_assistant/agent/harness.py backend/tests/test_stream_error_handling.py backend/tests/test_agent_split.py 技术方案报告.md`
Expected: only scoped TTFT optimization, cache, tests, and report changes.
