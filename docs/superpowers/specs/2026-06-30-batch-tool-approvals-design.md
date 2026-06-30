# Batch Tool Approvals Design

## Goal

When a turn produces multiple tool approval requests, the UI should present them as one approval batch and resume the LangGraph run only once after the user submits the batch decisions.

## Approach

Add a batch approval API beside the existing single-approval API. The backend records every approval or denial decision, then resumes the graph once with an approval turn count equal to the number of decisions. Existing single-approval endpoints remain for compatibility.

On the frontend, normal tool approvals are rendered in a single batch card. Users can approve all, deny all, or change individual decisions before submitting. The hook sends one batch stream request, so the conversation receives one continuous stream card instead of one stream per approved tool.

Memory approvals stay separate because they are background notifications and should not block normal input.

## Components

- `backend/src/personal_assistant/api/schemas.py`: define `ApprovalBatchDecision` and `ApprovalBatchItem`.
- `backend/src/personal_assistant/api/server.py`: expose `POST /api/approvals/stream`.
- `backend/src/personal_assistant/agent/harness.py`: add `resume_after_approvals_stream`.
- `frontend/src/lib/api.ts`: add batch approval request types and `approveBatchStream`.
- `frontend/src/hooks/useChat.ts`: add `approveBatch` and keep single approval methods as wrappers.
- `frontend/src/components/ToolApprovalBatchCard.tsx`: render grouped decisions and submit once.
- `frontend/src/components/ChatPanel.tsx`: use the batch card for normal pending approvals.

## Testing

Backend tests verify the batch stream records all decisions and resumes the graph once. Frontend tests verify multiple approval requests render as one batch and submit a single batch stream request.
