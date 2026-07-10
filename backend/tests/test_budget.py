from decimal import Decimal

from personal_assistant.governance.budget import BudgetService, InMemoryUsageLedger
from personal_assistant.governance.models import GovernancePolicy, ModelPrice
from personal_assistant.governance.policy import InMemoryPolicyStore, PolicyService


def _policy_service(policy: GovernancePolicy) -> PolicyService:
    service = PolicyService(InMemoryPolicyStore())
    service.create(policy)
    return service


def test_record_calculates_cost_from_model_prices_and_aggregates_thread_usage() -> None:
    service = BudgetService(
        InMemoryUsageLedger(),
        _policy_service(
            GovernancePolicy(
                model_prices={
                    "model-a": ModelPrice(
                        input_per_million=Decimal("1.50"),
                        output_per_million=Decimal("2.00"),
                    )
                }
            )
        ),
    )

    entry = service.record(
        thread_id="thread-1",
        operation_type="llm",
        name="model-a",
        input_tokens=1_000_000,
        output_tokens=500_000,
    )

    assert entry.estimated_cost_usd == Decimal("2.50")
    totals = service.totals(thread_id="thread-1")
    assert totals.total_tokens == 1_500_000
    assert totals.estimated_cost_usd == Decimal("2.50")


def test_check_denies_operation_when_thread_token_budget_would_be_exceeded() -> None:
    service = BudgetService(
        InMemoryUsageLedger(),
        _policy_service(GovernancePolicy(max_thread_tokens=10)),
    )
    service.record(
        thread_id="thread-1",
        operation_type="llm",
        name="unknown-model",
        input_tokens=8,
        output_tokens=0,
    )

    decision = service.check(thread_id="thread-1", input_tokens=3, output_tokens=0)

    assert decision.allowed is False
    assert decision.reason == "thread token budget exhausted"


def test_check_allows_unknown_model_at_zero_cost_when_only_cost_budget_exists() -> None:
    service = BudgetService(
        InMemoryUsageLedger(),
        _policy_service(GovernancePolicy(max_thread_cost_usd=Decimal("1.00"))),
    )

    decision = service.check(
        thread_id="thread-1", input_tokens=1000, output_tokens=1000, name="unknown-model"
    )

    assert decision.allowed is True
    assert decision.estimated_cost_usd == Decimal("0")
