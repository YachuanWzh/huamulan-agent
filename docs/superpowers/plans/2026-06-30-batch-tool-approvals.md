# Batch Tool Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace multiple per-tool approval resumes with one batch approval flow.

**Architecture:** Keep the existing single-approval API for compatibility and add a batch streaming endpoint. The frontend renders normal approvals as a batch card and submits all decisions through one stream.

**Tech Stack:** FastAPI, Pydantic, LangGraph streaming events, React, TypeScript, Vitest, Testing Library.

## Global Constraints

- Write failing tests before production changes.
- Do not change memory approval behavior.
- Do not remove existing single-approval APIs.
- Use existing stream parsing and message rendering patterns.

---

### Task 1: Backend Batch Stream

**Files:**
- Modify: `backend/src/personal_assistant/api/schemas.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Test: `backend/tests/test_stream_error_handling.py`

**Interfaces:**
- Produces: `ApprovalBatchDecision(thread_id: str, decisions: list[ApprovalBatchItem])`
- Produces: `AgentHarness.resume_after_approvals_stream(thread_id, decisions, llm_config=None, callbacks=None)`

- [ ] Write failing tests for batch stream behavior.
- [ ] Run the backend test and confirm it fails because the endpoint or method is missing.
- [ ] Add schemas, endpoint, and harness method.
- [ ] Run the backend test and confirm it passes.

### Task 2: Frontend Batch API And Hook

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/hooks/useChat.ts`
- Test: `frontend/src/lib/api.test.ts`
- Test: `frontend/src/hooks/useChat.test.ts`

**Interfaces:**
- Produces: `api.approveBatchStream({ thread_id, decisions })`
- Produces: `useChat(...).approveBatch(decisions)`

- [ ] Write failing tests for `approveBatchStream` and `approveBatch`.
- [ ] Run the frontend tests and confirm they fail because the batch API is missing.
- [ ] Add batch request types, API method, and hook method.
- [ ] Run the frontend tests and confirm they pass.

### Task 3: Frontend Batch Card

**Files:**
- Create: `frontend/src/components/ToolApprovalBatchCard.tsx`
- Create: `frontend/src/components/ToolApprovalBatchCard.test.tsx`
- Modify: `frontend/src/components/ChatPanel.tsx`
- Test: `frontend/src/components/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: `ToolCallApproval[]`
- Produces: `onSubmit(decisions: { approval_id: string; approved: boolean }[])`

- [ ] Write failing tests for grouped approval rendering and single submit.
- [ ] Run the component tests and confirm they fail because the batch card is missing.
- [ ] Add the batch card and wire it into `ChatPanel`.
- [ ] Run the component tests and confirm they pass.

### Task 4: Verification

**Files:**
- No production files.

- [ ] Run backend targeted tests.
- [ ] Run frontend targeted tests.
- [ ] Run broader frontend test suite.
- [ ] Report exact verification commands and results.
