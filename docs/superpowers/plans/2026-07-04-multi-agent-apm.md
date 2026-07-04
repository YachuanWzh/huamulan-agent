# Multi-Agent APM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional multi-agent APM orchestration without breaking the existing single-agent assistant.

**Architecture:** Add request-level `agent_mode`, dispatch `multi` requests to a new LangGraph graph, and keep `single` as default. Frontend stores the mode globally and passes it to chat and evaluation.

**Tech Stack:** FastAPI, Pydantic, LangGraph, React, Vitest, pytest.

## Global Constraints

- TDD: write failing tests before production changes.
- Existing single-agent API behavior remains default-compatible.
- Multi-agent communication uses JSON objects.
- PostgreSQL remains execution-log storage; Redis and Qdrant integration points stay configuration-driven.

---

### Task 1: Backend API Contract

**Files:**
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Test: `backend/tests/test_multi_agent_contract.py`

**Interfaces:**
- Produces: `ChatRequest.agent_mode: Literal["single", "multi"]`
- Produces: `SkillEvaluationRunRequest.agent_mode: Literal["single", "multi"]`

- [ ] Write tests for schema defaults and server forwarding.
- [ ] Run pytest and confirm failures.
- [ ] Add schema fields and forward mode through chat and e2e evaluation.
- [ ] Run pytest and confirm pass.

### Task 2: Multi-Agent LangGraph

**Files:**
- Create: `backend/src/personal_assistant/agent/multi_agent.py`
- Modify: `backend/src/personal_assistant/agent/state.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Test: `backend/tests/test_multi_agent_graph.py`

**Interfaces:**
- Produces: `rewrite_query_and_slots(query: str) -> dict[str, Any]`
- Produces: `compile_multi_agent(settings, registry, memory, llm_config=None, hook_manager=None, cache=None)`
- Consumes: existing `build_llm`, `ExecutionLogCreate`, and memory execution-log recorder.

- [ ] Write tests for query rewrite, slot extraction, JSON child reports, and harness dispatch.
- [ ] Run pytest and confirm failures.
- [ ] Implement graph nodes and harness mode dispatch.
- [ ] Run pytest and confirm pass.

### Task 3: Frontend Global Mode

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/hooks/useChat.ts`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`
- Test: `frontend/src/App.test.tsx`
- Test: `frontend/src/hooks/useChat.test.ts`
- Test: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `AgentMode = "single" | "multi"`
- Chat and evaluation requests include `agent_mode`.

- [ ] Write tests for global toggle and request payloads.
- [ ] Run frontend tests and confirm failures.
- [ ] Add UI, props, API types, and request payload wiring.
- [ ] Run frontend tests and confirm pass.
