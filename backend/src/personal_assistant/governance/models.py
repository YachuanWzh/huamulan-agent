"""Typed, immutable governance policy documents and decisions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelPrice(BaseModel):
    """Per-million-token price for one model, in USD."""

    model_config = ConfigDict(frozen=True)

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class GovernancePolicy(BaseModel):
    """Versioned controls for automatic RCA and execution budgets."""

    model_config = ConfigDict(frozen=True)

    max_global_tokens: int | None = Field(default=None, ge=0)
    max_thread_tokens: int | None = Field(default=None, ge=0)
    max_global_cost_usd: Decimal | None = Field(default=None, ge=0)
    max_thread_cost_usd: Decimal | None = Field(default=None, ge=0)
    auto_rca_levels: list[str] = Field(default_factory=lambda: ["P0"])
    auto_approved_tools: list[str] = Field(default_factory=list)
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)


class PolicyVersion(BaseModel):
    """An immutable historical snapshot of a governance policy."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    document: GovernancePolicy
    is_active: bool
    created_at: datetime


class PolicyDecision(BaseModel):
    """Auditable outcome from applying a policy rule."""

    model_config = ConfigDict(frozen=True)

    policy_version: int = Field(ge=1)
    decision_kind: str
    allowed: bool
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)
