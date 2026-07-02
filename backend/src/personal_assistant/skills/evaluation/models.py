from typing import Any

from pydantic import BaseModel, Field


class GoldenSkillCase(BaseModel):
    id: str
    query: str
    expected_skills: list[str] = Field(default_factory=list)
    expected_tool: str | None = None
    expected_args: dict[str, Any] = Field(default_factory=dict)


class RoutingMetrics(BaseModel):
    total_cases: int
    selection_accuracy: float | None = None
    false_positive_rate: float | None = None
    parameter_extraction_fidelity: float | None = None


class StaticSkillMetrics(BaseModel):
    skill_name: str
    description_tokens: int
    skill_md_lines: int
    python_lines: int
    max_cyclomatic_complexity: int
    tool_count: int


class RuntimeSkillMetrics(BaseModel):
    skill_name: str
    tool_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    retry_count: int = 0
    execution_success_rate: float | None = None
    retry_ratio: float | None = None
    p95_latency_ms: int | None = None
    p99_latency_ms: int | None = None
    token_consumption_per_call: float | None = None


class SkillEvaluationResult(BaseModel):
    skill_name: str
    overall_score: float
    static: StaticSkillMetrics
    runtime: RuntimeSkillMetrics | None = None
    score_components: dict[str, float] = Field(default_factory=dict)


class SkillEvaluationReport(BaseModel):
    skills: list[SkillEvaluationResult]
    routing: RoutingMetrics | None = None
