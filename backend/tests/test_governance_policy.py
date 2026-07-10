from decimal import Decimal

import pytest
from pydantic import ValidationError

from personal_assistant.governance.models import GovernancePolicy, ModelPrice
from personal_assistant.governance.policy import InMemoryPolicyStore, PolicyService


def test_create_activates_an_immutable_policy_version() -> None:
    store = InMemoryPolicyStore()
    service = PolicyService(store)

    created = service.create(
        GovernancePolicy(auto_rca_levels=["P0"], max_thread_tokens=10)
    )

    assert created.version == 1
    assert created.is_active is True
    assert service.active().document.max_thread_tokens == 10


def test_activate_clones_a_historical_policy_instead_of_mutating_it() -> None:
    service = PolicyService(InMemoryPolicyStore())
    first = service.create(GovernancePolicy(auto_rca_levels=["P0"]))
    service.create(GovernancePolicy(auto_rca_levels=["P1"]))

    restored = service.activate(first.version)

    assert restored.version == 3
    assert restored.is_active is True
    assert restored.document.auto_rca_levels == ["P0"]
    assert first.is_active is True


def test_policy_rejects_negative_budget_limits() -> None:
    with pytest.raises(ValidationError):
        GovernancePolicy(max_global_tokens=-1)


def test_policy_uses_decimal_model_prices() -> None:
    policy = GovernancePolicy(
        model_prices={
            "model": ModelPrice(
                input_per_million=Decimal("1.25"),
                output_per_million=Decimal("2.50"),
            )
        }
    )

    assert policy.model_prices["model"].input_per_million == Decimal("1.25")
