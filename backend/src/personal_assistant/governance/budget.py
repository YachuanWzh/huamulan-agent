"""Usage-ledger aggregation and active-policy budget decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models import GovernancePolicy, PolicyDecision
from .policy import PolicyService


class UsageEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    thread_id: str
    operation_type: str
    name: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: Decimal = Field(ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    incident_id: str | None = None
    created_at: datetime

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")


class BudgetDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    estimated_cost_usd: Decimal
    policy_decision: PolicyDecision


class UsageLedger(Protocol):
    def append(self, entry: UsageEntry) -> UsageEntry: ...

    def list(self, thread_id: str | None = None, limit: int | None = None) -> list[UsageEntry]: ...


class InMemoryUsageLedger:
    """Test/local ledger that returns deep copies to prevent external mutation."""

    def __init__(self) -> None:
        self._entries: list[UsageEntry] = []

    def append(self, entry: UsageEntry) -> UsageEntry:
        stored = entry.model_copy(deep=True)
        self._entries.append(stored)
        return stored.model_copy(deep=True)

    def list(self, thread_id: str | None = None, limit: int | None = None) -> list[UsageEntry]:
        entries = self._entries
        if thread_id is not None:
            entries = [entry for entry in entries if entry.thread_id == thread_id]
        result = sorted(entries, key=lambda entry: entry.created_at, reverse=True)
        if limit is not None:
            result = result[:limit]
        return [entry.model_copy(deep=True) for entry in result]


class BudgetService:
    """Records actual usage and blocks projected usage beyond active policy limits."""

    def __init__(self, ledger: UsageLedger, policies: PolicyService) -> None:
        self._ledger = ledger
        self._policies = policies

    def record(
        self,
        *,
        thread_id: str,
        operation_type: str,
        name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        elapsed_ms: int = 0,
        incident_id: str | None = None,
        estimated_cost_usd: Decimal | None = None,
    ) -> UsageEntry:
        if input_tokens < 0 or output_tokens < 0 or elapsed_ms < 0:
            raise ValueError("usage values cannot be negative")
        cost = estimated_cost_usd if estimated_cost_usd is not None else self._cost(name, input_tokens, output_tokens)
        if cost < 0:
            raise ValueError("estimated cost cannot be negative")
        return self._ledger.append(
            UsageEntry(
                id=str(uuid4()),
                thread_id=thread_id,
                operation_type=operation_type,
                name=name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
                elapsed_ms=elapsed_ms,
                incident_id=incident_id,
                created_at=datetime.now(timezone.utc),
            )
        )

    def totals(self, thread_id: str | None = None) -> UsageTotals:
        entries = self._ledger.list(thread_id=thread_id)
        return UsageTotals(
            total_tokens=sum(entry.total_tokens for entry in entries),
            estimated_cost_usd=sum((entry.estimated_cost_usd for entry in entries), Decimal("0")),
        )

    def check(
        self,
        *,
        thread_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        name: str = "",
        estimated_cost_usd: Decimal | None = None,
    ) -> BudgetDecision:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("usage values cannot be negative")
        policy_version = self._policies.active()
        projected_tokens = input_tokens + output_tokens
        projected_cost = (
            estimated_cost_usd
            if estimated_cost_usd is not None
            else self._cost(name, input_tokens, output_tokens, policy_version.document)
        )
        if projected_cost < 0:
            raise ValueError("estimated cost cannot be negative")
        thread = self.totals(thread_id=thread_id)
        global_totals = self.totals()
        policy = policy_version.document
        allowed, reason = _allowed(policy, thread, global_totals, projected_tokens, projected_cost)
        return BudgetDecision(
            allowed=allowed,
            reason=reason,
            estimated_cost_usd=projected_cost,
            policy_decision=PolicyDecision(
                policy_version=policy_version.version,
                decision_kind="budget",
                allowed=allowed,
                reason=reason,
                metadata={"thread_id": thread_id},
            ),
        )

    def _cost(
        self,
        name: str,
        input_tokens: int,
        output_tokens: int,
        policy: GovernancePolicy | None = None,
    ) -> Decimal:
        document = policy or self._policies.active().document
        price = document.model_prices.get(name)
        if price is None:
            return Decimal("0")
        return (Decimal(input_tokens) * price.input_per_million + Decimal(output_tokens) * price.output_per_million) / Decimal(1_000_000)


def _allowed(
    policy: GovernancePolicy,
    thread: UsageTotals,
    global_totals: UsageTotals,
    projected_tokens: int,
    projected_cost: Decimal,
) -> tuple[bool, str]:
    if policy.max_thread_tokens is not None and thread.total_tokens + projected_tokens > policy.max_thread_tokens:
        return False, "thread token budget exhausted"
    if policy.max_global_tokens is not None and global_totals.total_tokens + projected_tokens > policy.max_global_tokens:
        return False, "global token budget exhausted"
    if policy.max_thread_cost_usd is not None and thread.estimated_cost_usd + projected_cost > policy.max_thread_cost_usd:
        return False, "thread cost budget exhausted"
    if policy.max_global_cost_usd is not None and global_totals.estimated_cost_usd + projected_cost > policy.max_global_cost_usd:
        return False, "global cost budget exhausted"
    return True, "within budget"
