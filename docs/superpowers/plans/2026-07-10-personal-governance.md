# Personal Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect alert ingress and add persistent incidents, execution budget controls, and versioned governance policies to the personal Agent console.

**Architecture:** A new `personal_assistant.governance` package owns typed policy, alert-guard, incident, and budget behavior. PostgreSQL is durable storage and Redis is used only for alert replay/rate keys. FastAPI adapts domain errors to HTTP and the existing frontend consumes typed APIs through small feature panels.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, psycopg, Redis, pytest; React 19, TypeScript, Vitest.

## Global Constraints

- No authentication, RBAC, or tenant model is introduced.
- Alert signatures use `HMAC-SHA256("<timestamp>.<raw-body>")`, 300-second freshness, and constant-time comparison.
- Redis failure fails closed for alert ingress only.
- PostgreSQL is the source of truth for incidents, policy versions, and usage entries.
- A new behavior is implemented only after its focused test is observed failing.

---

### Task 1: Domain Models and Versioned Policy Store

**Files:**
- Create: `backend/src/personal_assistant/governance/__init__.py`
- Create: `backend/src/personal_assistant/governance/models.py`
- Create: `backend/src/personal_assistant/governance/policy.py`
- Create: `backend/tests/test_governance_policy.py`
- Modify: `backend/src/personal_assistant/config.py`

**Interfaces:**
- Produces `GovernancePolicy`, `PolicyVersion`, `PolicyDecision`, and `PolicyService`.
- `PolicyService.create(document: GovernancePolicy) -> PolicyVersion` creates the next active immutable version.
- `PolicyService.activate(version: int) -> PolicyVersion` clones the selected document into a new active version.

- [ ] **Step 1: Write the failing policy tests**

```python
def test_create_activates_an_immutable_policy_version():
    store = InMemoryPolicyStore()
    service = PolicyService(store)
    created = service.create(GovernancePolicy(auto_rca_levels=["P0"], max_thread_tokens=10))
    assert created.version == 1
    assert service.active().document.max_thread_tokens == 10

def test_activate_clones_a_historical_policy_instead_of_mutating_it():
    service = PolicyService(InMemoryPolicyStore())
    first = service.create(GovernancePolicy(auto_rca_levels=["P0"]))
    service.create(GovernancePolicy(auto_rca_levels=["P1"]))
    restored = service.activate(first.version)
    assert restored.version == 3
    assert restored.document.auto_rca_levels == ["P0"]
```

- [ ] **Step 2: Run the test to verify RED**

Run: `cd backend; uv run pytest tests/test_governance_policy.py -q`

Expected: FAIL because `personal_assistant.governance` does not exist.

- [ ] **Step 3: Implement models, validation, and store protocol**

```python
class GovernancePolicy(BaseModel):
    max_global_tokens: int | None = Field(default=None, ge=0)
    max_thread_tokens: int | None = Field(default=None, ge=0)
    max_global_cost_usd: Decimal | None = Field(default=None, ge=0)
    max_thread_cost_usd: Decimal | None = Field(default=None, ge=0)
    auto_rca_levels: list[str] = Field(default_factory=lambda: ["P0"])
    auto_approved_tools: list[str] = Field(default_factory=list)
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)

class PolicyService:
    def create(self, document: GovernancePolicy) -> PolicyVersion: ...
    def active(self) -> PolicyVersion: ...
    def activate(self, version: int) -> PolicyVersion: ...
```

- [ ] **Step 4: Run the policy tests to verify GREEN**

Run: `cd backend; uv run pytest tests/test_governance_policy.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/src/personal_assistant/governance backend/src/personal_assistant/config.py backend/tests/test_governance_policy.py
git commit -m "feat: add versioned governance policies"
```

### Task 2: Alert Ingress Guard

**Files:**
- Create: `backend/src/personal_assistant/governance/alert_guard.py`
- Create: `backend/tests/test_alert_ingress_guard.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/src/personal_assistant/config.py`

**Interfaces:**
- `AlertIngressGuard.verify(timestamp: str | None, signature: str | None, raw_body: bytes) -> None` raises `AlertSignatureError`, `AlertReplayError`, `AlertRateLimitError`, or `AlertIngressUnavailable`.
- Alert endpoint obtains `Request.body()` before parsing and calls the guard before persistence.

- [ ] **Step 1: Write failing verification tests**

```python
def test_accepts_a_fresh_valid_hmac_signature():
    guard, now = build_guard(secret="secret")
    body, timestamp = b'{"alerts":[]}', str(int(now))
    signature = sign("secret", timestamp, body)
    guard.verify(timestamp, signature, body)

def test_rejects_reused_signature():
    guard, now = build_guard(secret="secret")
    body, timestamp = b'{}', str(int(now))
    signature = sign("secret", timestamp, body)
    guard.verify(timestamp, signature, body)
    with pytest.raises(AlertReplayError):
        guard.verify(timestamp, signature, body)
```

- [ ] **Step 2: Run the guard tests to verify RED**

Run: `cd backend; uv run pytest tests/test_alert_ingress_guard.py -q`

Expected: FAIL because `AlertIngressGuard` is undefined.

- [ ] **Step 3: Implement HMAC, freshness, replay, and fixed-window rate limiting**

```python
expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, signature or ""):
    raise AlertSignatureError()
if abs(clock() - int(timestamp)) > 300:
    raise AlertSignatureError("timestamp expired")
if not await replay_store.claim(signature, ttl_seconds=300):
    raise AlertReplayError()
if not await rate_store.allow("otel-alerts", limit=limit, ttl_seconds=60):
    raise AlertRateLimitError()
```

- [ ] **Step 4: Adapt the alert endpoint and verify HTTP behavior**

```python
@app.post("/api/otel/alerts")
async def handle_otel_alert(request: Request):
    raw_body = await request.body()
    await alert_ingress_guard.verify(
        request.headers.get("X-Alert-Timestamp"),
        request.headers.get("X-Alert-Signature"),
        raw_body,
    )
    payload = AlertManagerWebhook.model_validate_json(raw_body)
```

Run: `cd backend; uv run pytest tests/test_alert_ingress_guard.py tests/test_otel_alert_api_persistence.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/src/personal_assistant/governance/alert_guard.py backend/src/personal_assistant/api/server.py backend/src/personal_assistant/config.py backend/tests/test_alert_ingress_guard.py
git commit -m "feat: protect OTEL alert ingress"
```

### Task 3: Incidents and Timeline Persistence

**Files:**
- Create: `backend/src/personal_assistant/governance/incidents.py`
- Create: `backend/tests/test_incidents.py`
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/src/personal_assistant/api/schemas.py`

**Interfaces:**
- `IncidentService.open_from_alert(alert: dict) -> Incident`.
- `IncidentService.transition(id: str, status: IncidentStatus, owner: str | None) -> Incident`.
- `IncidentService.add_action(id: str, text: str) -> IncidentAction`.
- API endpoints match the approved spec.

- [ ] **Step 1: Write failing incident tests**

```python
async def test_open_alert_creates_open_incident_and_received_timeline_entry():
    service = IncidentService(InMemoryIncidentStore())
    incident = await service.open_from_alert({"id": "a1", "level": "P0", "alert_name": "Latency"})
    assert incident.status == "open"
    assert incident.timeline[0].event_type == "alert_received"

async def test_transition_and_action_completion_append_timeline_entries():
    service = IncidentService(InMemoryIncidentStore())
    incident = await service.open_from_alert({"id": "a1", "level": "P1", "alert_name": "Errors"})
    await service.transition(incident.id, "investigating", "me")
    action = await service.add_action(incident.id, "Roll back")
    updated = await service.complete_action(incident.id, action.id, True)
    assert updated.actions[0].completed is True
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/test_incidents.py -q`

Expected: FAIL because `IncidentService` is undefined.

- [ ] **Step 3: Implement service, Postgres tables/repository, schemas, and endpoints**

```python
class IncidentService:
    async def open_from_alert(self, alert: dict[str, Any]) -> Incident: ...
    async def transition(self, incident_id: str, status: IncidentStatus, owner: str | None = None) -> Incident: ...
    async def add_action(self, incident_id: str, text: str) -> IncidentAction: ...
    async def complete_action(self, incident_id: str, action_id: str, completed: bool) -> Incident: ...
```

- [ ] **Step 4: Verify GREEN**

Run: `cd backend; uv run pytest tests/test_incidents.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/src/personal_assistant/governance/incidents.py backend/src/personal_assistant/memory/postgres.py backend/src/personal_assistant/api/server.py backend/src/personal_assistant/api/schemas.py backend/tests/test_incidents.py
git commit -m "feat: add persistent incident command center"
```

### Task 4: Usage Ledger and Budget Guard

**Files:**
- Create: `backend/src/personal_assistant/governance/budget.py`
- Create: `backend/tests/test_budget.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Modify: `backend/src/personal_assistant/api/server.py`

**Interfaces:**
- `BudgetService.check(thread_id: str, operation: str, estimated_tokens: int = 0) -> BudgetDecision`.
- `BudgetService.record(entry: UsageEntry) -> UsageEntry`.
- `BudgetExceeded` prevents an LLM/tool/child-Agent operation before it begins.

- [ ] **Step 1: Write failing budget tests**

```python
async def test_denies_new_operation_when_thread_token_budget_is_exhausted():
    service = BudgetService(InMemoryUsageStore(), fixed_policy(max_thread_tokens=10))
    await service.record(UsageEntry(thread_id="t", operation="llm", input_tokens=8, output_tokens=2))
    decision = await service.check("t", "llm", estimated_tokens=1)
    assert decision.allowed is False
    assert decision.reason == "thread_token_budget_exhausted"

def test_cost_uses_configured_model_prices():
    service = BudgetService(InMemoryUsageStore(), fixed_policy(price=ModelPrice(input_per_million=1, output_per_million=2)))
    assert service.estimate_cost("model", input_tokens=1_000_000, output_tokens=1_000_000) == Decimal("3")
```

- [ ] **Step 2: Verify RED**

Run: `cd backend; uv run pytest tests/test_budget.py -q`

Expected: FAIL because `BudgetService` is undefined.

- [ ] **Step 3: Implement ledger repository, pricing, enforcement, and harness integration**

```python
async def check(self, thread_id: str, operation: str, estimated_tokens: int = 0) -> BudgetDecision:
    totals = await self._store.totals(thread_id)
    if self._policy.max_thread_tokens is not None and totals.tokens + estimated_tokens > self._policy.max_thread_tokens:
        return BudgetDecision(allowed=False, reason="thread_token_budget_exhausted")
    return BudgetDecision(allowed=True, reason="within_budget")
```

- [ ] **Step 4: Verify GREEN and API totals**

Run: `cd backend; uv run pytest tests/test_budget.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/src/personal_assistant/governance/budget.py backend/src/personal_assistant/agent/harness.py backend/src/personal_assistant/api/server.py backend/tests/test_budget.py
git commit -m "feat: enforce agent execution budgets"
```

### Task 5: Console APIs and Frontend Governance Panels

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/IncidentPanel.tsx`
- Create: `frontend/src/components/IncidentPanel.test.tsx`
- Create: `frontend/src/components/GovernancePanel.tsx`
- Create: `frontend/src/components/GovernancePanel.test.tsx`
- Modify: `frontend/src/components/WorkspacePanel.tsx`
- Modify: `frontend/src/components/WorkspacePanel.test.tsx`

**Interfaces:**
- `api.listIncidents`, `api.getIncident`, `api.updateIncident`, `api.createIncidentAction`, `api.updateIncidentAction`.
- `api.getBudget`, `api.listPolicies`, `api.createPolicy`, `api.activatePolicy`.
- `IncidentPanel` and `GovernancePanel` receive only API callbacks/typed data they render.

- [ ] **Step 1: Write failing component/API tests**

```tsx
it('filters incidents and completes an action', async () => {
  render(<IncidentPanel api={api} />)
  await userEvent.selectOptions(screen.getByLabelText('状态'), 'open')
  await userEvent.click(screen.getByRole('checkbox', { name: 'Roll back' }))
  expect(api.updateIncidentAction).toHaveBeenCalledWith('i1', 'a1', { completed: true })
})

it('activates a historical policy version', async () => {
  render(<GovernancePanel api={api} />)
  await userEvent.click(screen.getByRole('button', { name: '启用 v1' }))
  expect(api.activatePolicy).toHaveBeenCalledWith(1)
})
```

- [ ] **Step 2: Verify RED**

Run: `cd frontend; npm test -- IncidentPanel GovernancePanel --run`

Expected: FAIL because components and API methods do not exist.

- [ ] **Step 3: Implement typed client and focused panels**

```ts
export const api = {
  listIncidents: (status?: IncidentStatus) => request<Incident[]>(`/api/incidents${status ? `?status=${status}` : ''}`),
  updateIncidentAction: (incidentId: string, actionId: string, body: { completed: boolean }) => request<Incident>(`/api/incidents/${incidentId}/actions/${actionId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  activatePolicy: (version: number) => request<PolicyVersion>(`/api/governance/policies/${version}/activate`, { method: 'POST' }),
}
```

- [ ] **Step 4: Verify GREEN and production build**

Run: `cd frontend; npm test -- IncidentPanel GovernancePanel --run; npm run build`

Expected: tests PASS and TypeScript build succeeds.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/lib/api.ts frontend/src/components/IncidentPanel.tsx frontend/src/components/IncidentPanel.test.tsx frontend/src/components/GovernancePanel.tsx frontend/src/components/GovernancePanel.test.tsx frontend/src/components/WorkspacePanel.tsx frontend/src/components/WorkspacePanel.test.tsx
git commit -m "feat: add incident and governance console panels"
```

### Task 6: Final Regression and Documentation

**Files:**
- Modify: `README.md`
- Modify: `backend/.env.example`

- [ ] **Step 1: Document signing headers, alert sender example, policies, and budgets**

```markdown
OTEL alerts require `X-Alert-Timestamp` and `X-Alert-Signature`. Calculate the signature as HMAC-SHA256 of `<timestamp>.<body>` with `OTEL_ALERT_WEBHOOK_SECRET`.
```

- [ ] **Step 2: Run backend regression and lint**

Run: `cd backend; uv run ruff check src tests; uv run pytest -q`

Expected: lint and all backend tests pass.

- [ ] **Step 3: Run frontend regression and build**

Run: `cd frontend; npm run lint; npm test; npm run build`

Expected: lint, all frontend tests, and build pass.

- [ ] **Step 4: Commit**

```powershell
git add README.md backend/.env.example
git commit -m "docs: document governance controls"
```
