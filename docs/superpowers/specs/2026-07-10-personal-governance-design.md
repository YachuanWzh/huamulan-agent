# Personal Governance Design

## Goal

Provide a production-safe governance layer for this personal Agent project: protect the OTEL alert ingress, persist and manage incidents, enforce execution budgets, and version the policies that control automatic behavior.

## Scope and Constraints

- This is a single-user personal project. No user authentication, RBAC, or tenant isolation is introduced.
- PostgreSQL is the durable source of truth; Redis is used for shared, short-lived replay and rate-limit keys.
- Existing alert SSE, alert persistence, Agent execution logs, and tool approval behavior remain compatible.
- No new external service is required.
- All behavioral changes use strict TDD: a focused failing test must be observed before production implementation.

## Architecture

Create a `personal_assistant.governance` domain package so the FastAPI server remains an adapter rather than the home of policy logic. The package exposes four independently testable services:

1. `AlertIngressGuard` validates alert webhook HMAC signatures, timestamp freshness, replay protection, and rate limits.
2. `IncidentService` persists incident records and append-only timeline entries, linking an OTEL alert, RCA thread, and actions.
3. `BudgetService` records usage ledger entries and decides whether a new LLM, tool, or child-Agent operation may proceed.
4. `PolicyService` persists immutable policy versions, resolves the active policy, and records policy decisions.

`api/server.py` receives HTTP requests and delegates to these services. `AgentHarness` and the multi-Agent graph ask `BudgetService` for permission before an execution unit begins and report usage after it finishes. The frontend consumes small, typed APIs rather than duplicating decision logic.

## 1. Signed Alert Ingress

The AlertManager sender sends:

- `X-Alert-Timestamp`: UNIX epoch seconds.
- `X-Alert-Signature`: lowercase hex HMAC-SHA256 of `"<timestamp>.<raw request body>"`, keyed by `OTEL_ALERT_WEBHOOK_SECRET`.

The ingress guard uses constant-time comparison and accepts timestamps within 300 seconds of server time. The replay key is the signature digest and expires after 300 seconds. A token-bucket or fixed-window Redis key limits ingress to a configurable `OTEL_ALERT_WEBHOOK_RATE_LIMIT_PER_MINUTE` (default 60).

The secret is required when alert ingress is enabled. Invalid/missing signatures, stale timestamps, replayed signatures, and exhausted rate limits return HTTP 401, 409, or 429 respectively and do not persist an alert or schedule RCA. If Redis is unavailable, ingress fails closed with HTTP 503: accepting a P0 alert without replay/limit guarantees is unsafe. A valid request preserves the existing processing path.

## 2. Incident Command Center

Each accepted OTEL alert creates or updates a durable Incident. An Incident has a stable UUID, alert ID, severity, title, service, lifecycle status (`open`, `investigating`, `mitigated`, `closed`), RCA thread ID/result, owner text, timestamps, and action items. A timeline is append-only, with event type, timestamp, structured metadata, and readable message.

Alert receipt creates an `open` incident and timeline entry. Background RCA changes it to `investigating` then records `rca_completed`, `rca_failed`, or `rca_waiting_approval`. Users may change status and create/complete action items. Existing alert history remains available and includes its linked incident ID.

API surface:

- `GET /api/incidents?status=&limit=` lists incidents.
- `GET /api/incidents/{incident_id}` returns the incident with timeline and actions.
- `PATCH /api/incidents/{incident_id}` changes status or owner.
- `POST /api/incidents/{incident_id}/actions` creates an action.
- `PATCH /api/incidents/{incident_id}/actions/{action_id}` toggles completion.

The frontend adds an Incident section to the workspace. It displays filters, concise cards, a details timeline, RCA state, and action controls. The alert panel is kept for realtime notification.

## 3. Usage Ledger and Budget Enforcement

The durable usage ledger stores thread ID, incident ID when applicable, operation type (`llm`, `tool`, `child_agent`), model/tool name, input tokens, output tokens, estimated USD cost, elapsed milliseconds, and timestamp. Cost calculation uses configurable per-million-token input/output prices by model, with zero cost for unknown models until configured.

The active policy defines global and per-thread token and USD budgets. Before an execution unit begins, `BudgetService.check()` aggregates current usage and returns an allow/deny decision. A denied decision prevents scheduling the operation, produces a budget audit/timeline event, and is streamed as a clear error event. After execution, `record()` appends the actual usage. Tool and child-Agent entries record zero tokens unless a measured cost is supplied.

API surface:

- `GET /api/governance/usage?thread_id=&limit=` returns ledger entries and aggregate totals.
- `GET /api/governance/budget?thread_id=` returns the active limits and remaining budget.

## 4. Versioned Policy Center

Policies are immutable JSON documents in PostgreSQL. A version contains:

- Budget limits and price table.
- Automatic RCA severity levels (default P0 only).
- Tool names/categories eligible for automatic approval during RCA.

Exactly one policy is active. Creating a policy validates its typed schema, increments its version, and activates it atomically. Activating a historical version creates a new version with that document rather than mutating history. Each policy evaluation emits a decision audit record that contains policy version, decision kind, allow/deny result, and structured reason.

API surface:

- `GET /api/governance/policies` lists versions and identifies the active one.
- `GET /api/governance/policies/active` returns the active policy.
- `POST /api/governance/policies` creates and activates a policy document.
- `POST /api/governance/policies/{version}/activate` clones and activates a prior version.

The workspace Policy section shows the active policy, version history, editable JSON with validation errors, and an explicit activate action. It intentionally does not expose identities or roles.

## Error Handling and Compatibility

All governance errors are explicit typed domain errors mapped by the API to stable HTTP status codes. PostgreSQL failures return 503 and do not claim a state change. Redis failure is fail-closed only for alert ingress; read-only dashboards can show unavailable state. Existing deployments require a migration that creates governance tables and seeds policy version 1 with current behavior: P0 auto-RCA, no budget ceiling, and the current automatic approval rules.

## Testing

- Unit tests cover raw-body HMAC verification, timestamp expiry, replay, Redis outage, and rate limiting.
- Unit tests cover incident state transitions, timeline append order, and action completion.
- Unit tests cover budget aggregation, price calculation, and denial before an execution operation.
- Unit tests cover immutable policy versioning, typed validation, activation-by-copy, and decision audit events.
- API tests cover the status codes and payload contracts above.
- Frontend tests cover incident rendering/filtering/action update, budget display, and policy activation/error display.
- Existing backend and frontend test suites, lint, and production build are run before completion.
