from __future__ import annotations

import json
import random
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SBSCandidate(BaseModel):
    candidate_id: str
    output: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SBSTask(BaseModel):
    task_id: str
    prompt: str
    candidate_a: SBSCandidate
    candidate_b: SBSCandidate
    status: Literal["pending", "reviewed"] = "pending"
    provenance: dict[str, Any] = Field(default_factory=dict)


class SBSTaskSummary(BaseModel):
    task_id: str
    prompt: str
    status: Literal["pending", "reviewed"]


class SBSCandidateRunConfig(BaseModel):
    model: str | None = None
    agent_mode: Literal["single", "multi"] = "single"

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SBSRunRequest(BaseModel):
    prompt: str
    candidate_a: SBSCandidateRunConfig
    candidate_b: SBSCandidateRunConfig

    @field_validator("prompt")
    @classmethod
    def require_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt is required")
        return normalized


class SBSRunOptions(BaseModel):
    default_model: str
    known_models: list[str]
    agent_modes: list[Literal["single", "multi"]] = Field(
        default_factory=lambda: ["single", "multi"]
    )


class BlindedCandidate(BaseModel):
    label: Literal["A", "B"]
    output: str


class BlindedSBSTask(BaseModel):
    task_id: str
    prompt: str
    candidates: list[BlindedCandidate]
    identity_map: dict[str, str] = Field(exclude=True)


class SBSReview(BaseModel):
    task_id: str
    reviewer: str
    winner: Literal["A", "B", "tie", "both_bad"]
    reason: str = ""
    dimension_scores: dict[str, int] = Field(default_factory=dict)
    revision: int = 1
    canonical_winner: str | None = None

    @field_validator("dimension_scores")
    @classmethod
    def validate_scores(cls, value: dict[str, int]) -> dict[str, int]:
        if any(score < 1 or score > 5 for score in value.values()):
            raise ValueError("dimension scores must be between 1 and 5")
        return value

    @model_validator(mode="after")
    def require_both_bad_reason(self):
        if self.winner == "both_bad" and not self.reason.strip():
            raise ValueError("both_bad requires a reason")
        return self


def present_blinded_task(task: SBSTask, *, seed: int | str | None = None) -> BlindedSBSTask:
    candidates = [task.candidate_a, task.candidate_b]
    random.Random(seed).shuffle(candidates)
    labels = ["A", "B"]
    return BlindedSBSTask(
        task_id=task.task_id,
        prompt=task.prompt,
        candidates=[
            BlindedCandidate(label=label, output=candidate.output)
            for label, candidate in zip(labels, candidates, strict=True)
        ],
        identity_map={
            label: candidate.candidate_id
            for label, candidate in zip(labels, candidates, strict=True)
        },
    )


def summarize_sbs_task(task: SBSTask) -> SBSTaskSummary:
    return SBSTaskSummary(task_id=task.task_id, prompt=task.prompt, status=task.status)


def canonical_winner(review: SBSReview, task: BlindedSBSTask) -> str:
    if review.winner in {"tie", "both_bad"}:
        return review.winner
    return task.identity_map[review.winner]


def export_sbs_jsonl(tasks: list[SBSTask]) -> str:
    return "".join(
        json.dumps(task.model_dump(mode="json"), ensure_ascii=False) + "\n"
        for task in sorted(tasks, key=lambda item: item.task_id)
    )
