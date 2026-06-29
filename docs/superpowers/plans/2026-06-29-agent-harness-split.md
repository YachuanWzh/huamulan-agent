# Agent Harness Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move LangGraph agent assembly from `harness.py` into `agent.py`, leaving `harness.py` focused on approval limits, thread/memory methods, and API/SSE response adaptation.

**Architecture:** `personal_assistant.agent.agent` will own graph construction and LLM/tool execution through `compile_agent(...)`. `AgentHarness` will keep the public API and delegate compilation to that module, preserving existing private routing helpers in `harness.py` because they enforce approval and API-compliance limits.

**Tech Stack:** Python 3.11, LangGraph `StateGraph`, LangChain messages/tools, pytest.

## Global Constraints

- Strict TDD: add the failing test first and verify RED before production edits.
- Do not overwrite existing dirty worktree changes.
- Keep public `AgentHarness` behavior and existing route helper imports compatible.
- Add no new runtime dependencies.

---

### Task 1: Agent Graph Module

**Files:**
- Create: `backend/src/personal_assistant/agent/agent.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Test: `backend/tests/test_agent_split.py`

**Interfaces:**
- Consumes: `Settings`, `SkillRegistry`, `PostgresMemory`, `LLMConfig`, `ApprovalGate`, `_entry_route`, `_approval_route`, `_sanitize_messages_for_api`.
- Produces: `compile_agent(settings, registry, memory, decisions, llm_config=None)` returning a compiled LangGraph app.

- [ ] **Step 1: Write the failing test**

```python
from personal_assistant.agent import agent as agent_module
from personal_assistant.agent.harness import AgentHarness


def test_harness_delegates_compilation_to_agent_module(monkeypatch):
    calls = []
    sentinel = object()

    def fake_compile_agent(settings, registry, memory, decisions, llm_config=None):
        calls.append((settings, registry, memory, decisions, llm_config))
        return sentinel

    monkeypatch.setattr(agent_module, "compile_agent", fake_compile_agent)
    harness = AgentHarness(settings="settings", registry="registry", memory="memory")

    assert harness._compile("llm-config") is sentinel
    assert calls == [
        ("settings", "registry", "memory", harness.decisions, "llm-config")
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_split.py::test_harness_delegates_compilation_to_agent_module -v`
Expected: FAIL because `personal_assistant.agent.agent` does not exist or `AgentHarness._compile` does not delegate.

- [ ] **Step 3: Write minimal implementation**

Create `agent.py` with the graph construction currently inside `AgentHarness._compile`, then reduce `_compile` to:

```python
return agent_module.compile_agent(
    self.settings,
    self.registry,
    self.memory,
    self.decisions,
    llm_config,
)
```

- [ ] **Step 4: Run focused tests**

Run: `cd backend; uv run pytest tests/test_agent_split.py tests/test_approval_routing.py tests/test_stream_error_handling.py -v`
Expected: PASS.

- [ ] **Step 5: Run backend tests**

Run: `cd backend; uv run pytest -v`
Expected: PASS or report any pre-existing unrelated failures with output.
