# Thread History Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real PostgreSQL-backed thread history deletion and a UI path to clear the current fixed thread ID.

**Architecture:** Reuse LangGraph Postgres checkpointer's `adelete_thread(thread_id)` through `PostgresMemory`, expose it through `AgentHarness` and `DELETE /api/threads/{thread_id}`, then wire the frontend API and History controls. The current thread ID remains stable during normal use, but the UI can delete its database history and replace the localStorage-backed ID with a new UUID.

**Tech Stack:** FastAPI, Pydantic, LangGraph `AsyncPostgresSaver`, React, TypeScript, Vitest, pytest.

## Global Constraints

- Preserve unrelated user changes in the working tree.
- Use TDD: write failing tests before production edits.
- Do not connect tests to the real PostgreSQL database.
- Deleting history must call LangGraph's real thread deletion API so PostgreSQL checkpoint rows are removed.

---

### Task 1: Backend Thread Deletion

**Files:**
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Test: `backend/tests/test_thread_history_delete.py`

**Interfaces:**
- Produces: `PostgresMemory.delete_thread(thread_id: str) -> None`
- Produces: `AgentHarness.delete_thread(thread_id: str) -> None`
- Produces: `DELETE /api/threads/{thread_id}` returning `{"thread_id": "...", "deleted": true}`

- [x] **Step 1: Write failing backend tests**

- [x] **Step 2: Run backend tests to verify RED**

- [x] **Step 3: Implement minimal backend deletion path**

- [x] **Step 4: Run backend tests to verify GREEN**

### Task 2: Frontend API And History Controls

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/Sidebar.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Produces: `api.deleteThread(threadId: string) -> Promise<DeleteThreadResponse>`
- Produces: `Sidebar` props `onThreadCleared?: () => void`

- [x] **Step 1: Write failing frontend tests**

- [x] **Step 2: Run frontend tests to verify RED**

- [x] **Step 3: Implement minimal frontend API and UI controls**

- [x] **Step 4: Run frontend tests to verify GREEN**

### Task 3: Final Verification

**Files:**
- Verify: backend and frontend test suites affected by this change.

- [x] **Step 1: Run targeted backend tests**

- [x] **Step 2: Run targeted frontend tests**

- [x] **Step 3: Review diff for accidental unrelated edits**
