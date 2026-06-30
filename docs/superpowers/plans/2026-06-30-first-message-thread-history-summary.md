# First Message Thread History Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First message on an unbound session creates a thread without losing the live request, and history rows show a readable summary with the thread id.

**Architecture:** Keep the chat panel mounted when auto-creating the first thread so the in-flight hook state survives. Add a lightweight backend summary to `list_threads` derived from checkpoint messages, and render that summary in the History tab.

**Tech Stack:** React, Vitest, Testing Library, FastAPI, pytest, Pydantic, LangGraph checkpoint data.

## Global Constraints

- Follow strict TDD: failing test first, then minimal implementation.
- Do not overwrite unrelated dirty worktree changes.
- Keep UI consistent with the existing compact console layout.

---

### Task 1: Preserve First Unbound Send

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `ChatPanel` props `onThreadCreated` and `onNewConversation`.
- Produces: auto thread creation that updates the header/sidebar without remounting `ChatPanel`.

- [ ] **Step 1: Write the failing test**

Add a test that records the mocked `ChatPanel` mount id, clicks `onThreadCreated`, and expects the mount id to remain stable.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- App.test.tsx --run`
Expected: FAIL because `key={threadId ?? 'empty-thread'}` remounts the chat panel.

- [ ] **Step 3: Write minimal implementation**

Replace thread-id-based `ChatPanel` key with a conversation key that changes only for explicit new conversation or history selection, not for first auto thread creation.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- App.test.tsx --run`
Expected: PASS.

### Task 2: Return Thread Summaries

**Files:**
- Modify: `backend/tests/test_thread_history_delete.py`
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Modify: `backend/src/personal_assistant/memory/postgres.py`

**Interfaces:**
- Produces: `ThreadSummary.summary: str | None`.
- Produces: `PostgresMemory.list_threads()` rows with `summary`.

- [ ] **Step 1: Write the failing test**

Update the list threads endpoint test to expect `summary`, and add a memory helper test that derives a summary from a latest checkpoint user message.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_history_delete.py -q`
Expected: FAIL because `summary` is not in `ThreadSummary`.

- [ ] **Step 3: Write minimal implementation**

Add `summary` to `ThreadSummary`, select the latest checkpoint per thread, and derive a clipped summary from the first user message in checkpoint messages.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_history_delete.py -q`
Expected: PASS.

### Task 3: Display Summary In History

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/components/Sidebar.test.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `ThreadSummary.summary`.
- Produces: history row containing summary text plus a compact thread id.

- [ ] **Step 1: Write the failing tests**

Update API and Sidebar tests to include and assert the summary text.

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- api.test.ts Sidebar.test.tsx --run`
Expected: FAIL because the type and UI do not expose summary.

- [ ] **Step 3: Write minimal implementation**

Add `summary?: string | null` to the frontend type and render it as the primary row label, with thread id as secondary metadata.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- api.test.ts Sidebar.test.tsx --run`
Expected: PASS.

### Task 4: Final Verification

- [ ] Run frontend focused tests: `npm test -- App.test.tsx api.test.ts Sidebar.test.tsx --run`
- [ ] Run backend focused tests: `uv run pytest tests/test_thread_history_delete.py -q`
- [ ] Run broader available test command if project scripts make it practical.
