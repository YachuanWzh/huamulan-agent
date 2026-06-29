# System Security Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add system-level prompt and tool safety guards with PostgreSQL audit logging and a frontend audit view.

**Architecture:** Prompt guard runs at the outer `AgentHarness` entry before LangGraph or LLM invocation. Tool guard wraps tool execution inside the graph and records blocked actions. Audit events are stored through `PostgresMemory` and exposed by FastAPI for the Sidebar audit tab.

**Tech Stack:** Python 3.11, FastAPI, LangGraph, psycopg async pool, pytest, React, TypeScript, Vitest.

## Global Constraints

- Strict TDD: write failing tests first and observe expected failures.
- Keep regexes conservative with close-distance anchors to reduce false positives.
- Store audit logs in PostgreSQL.
- Frontend must display audit logs.
- Do not route blocked prompt messages into the LLM.

---

### Task 1: Prompt Guard And Audit Model

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Test: `backend/tests/test_security_harness.py`

**Interfaces:**
- Produces: `scan_prompt_guard(message: str) -> GuardMatch | None`
- Produces: `PostgresMemory.record_audit_event(event: AuditEventCreate) -> None`
- Produces: `AuditEvent`, `AuditEventCreate` pydantic models

- [ ] Write tests for blocked instruction override and non-blocked normal business language.
- [ ] Run `python -m pytest backend/tests/test_security_harness.py -v` and confirm the new tests fail because guard APIs do not exist.
- [ ] Implement `GuardMatch`, conservative prompt regexes, and blocked `ChatResponse` behavior.
- [ ] Add PostgreSQL audit table setup and insert method.
- [ ] Run the same pytest command and confirm prompt/audit tests pass.

### Task 2: Tool Guard

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Modify: `backend/src/personal_assistant/agent/agent.py`
- Test: `backend/tests/test_security_harness.py`

**Interfaces:**
- Produces: `SecurityError`
- Produces: `scan_tool_guard(tool_name: str, args: Any) -> GuardMatch | None`
- Produces: `guard_tool_call(tool_name: str, args: Any) -> None`

- [ ] Write tests for blocking `curl ... | bash`, `rm -rf`, `sudo`, fork bomb, reverse shell, and allowing benign commands.
- [ ] Run targeted pytest and confirm failures are missing implementation.
- [ ] Implement tool scanning and wrap active tools before `ToolNode` execution.
- [ ] Record audit events when a tool call is blocked.
- [ ] Run targeted pytest and confirm tool guard tests pass.

### Task 3: Audit API And Frontend View

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.css`
- Test: `backend/tests/test_security_harness.py`
- Test: `frontend/src/lib/api.test.ts`
- Test: `frontend/src/components/Sidebar.test.tsx`

**Interfaces:**
- Produces: `GET /api/audit-events?thread_id=<id>&limit=100`
- Produces: `api.listAuditEvents(threadId?: string)`
- Produces: Sidebar `Audit` tab

- [ ] Write failing backend API test for audit event listing.
- [ ] Write failing frontend tests for API client and Sidebar audit tab rendering.
- [ ] Implement backend route and memory query.
- [ ] Implement frontend types/client and compact audit list UI.
- [ ] Run backend and frontend targeted tests.
- [ ] Run the full relevant verification commands before completion.
