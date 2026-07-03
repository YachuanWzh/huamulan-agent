from typing import Any

from pydantic import BaseModel, Field


class GoldenSkillCase(BaseModel):
    id: str
    query: str
    category: str | None = None
    difficulty: str = "medium"
    expected_skills: list[str] = Field(default_factory=list)
    negative_skills: list[str] = Field(default_factory=list)
    expected_tool: str | None = None
    expected_args: dict[str, Any] = Field(default_factory=dict)
    expected_tool_calls: list["ToolCallExpectation"] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_behavior: str | None = None
    expected_answer_contains: list[str] = Field(default_factory=list)
    forbidden_answer_contains: list[str] = Field(default_factory=list)
    expected_security_event: str | None = None
    judge_rubric: str | None = None


class ToolCallExpectation(BaseModel):
    tool: str
    args_contains: dict[str, Any] = Field(default_factory=dict)


class AgentEvaluationCase(GoldenSkillCase):
    query: str | None = None
    turns: list[str] = Field(default_factory=list)


class RoutingMetrics(BaseModel):
    total_cases: int
    selection_accuracy: float | None = None
    false_positive_rate: float | None = None
    parameter_extraction_fidelity: float | None = None
    skill_selection_precision: float | None = None
    skill_selection_recall: float | None = None
    skill_selection_f1: float | None = None
    skill_over_selection_rate: float | None = None
    skill_under_selection_rate: float | None = None


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


class SafetyEvaluationMetrics(BaseModel):
    total_cases: int
    attack_block_rate: float | None = None
    unsafe_tool_call_rate: float | None = None
    secret_leak_rate: float | None = None
    security_event_precision: float | None = None


class ToolEvaluationMetrics(BaseModel):
    total_cases: int
    tool_selection_accuracy: float | None = None
    argument_fidelity: float | None = None
    forbidden_tool_violation_rate: float | None = None
    tool_call_precision: float | None = None
    tool_call_recall: float | None = None
    tool_call_f1: float | None = None
    unnecessary_tool_call_rate: float | None = None
    missing_tool_call_rate: float | None = None
    duplicate_tool_call_rate: float | None = None
    argument_precision: float | None = None
    argument_recall: float | None = None
    argument_f1: float | None = None
    argument_schema_validity_rate: float | None = None
    argument_value_hallucination_rate: float | None = None


class AnswerEvaluationMetrics(BaseModel):
    total_cases: int
    answer_contains_rate: float | None = None
    forbidden_answer_violation_rate: float | None = None


class HallucinationEvaluationMetrics(BaseModel):
    total_cases: int
    answer_hallucination_rate: float | None = None
    repeated_tool_call_rate: float | None = None
    tool_argument_hallucination_rate: float | None = None
    tool_evidence_usage_rate: float | None = None
    unsupported_answer_rate: float | None = None


class EvaluationCheck(BaseModel):
    name: str
    stage: str
    passed: bool
    expected: Any = None
    actual: Any = None
    reason: str = ""


class CaseDiagnosis(BaseModel):
    stage: str
    severity: str = "info"
    summary: str
    signals: list[str] = Field(default_factory=list)
    recommendation: str = ""


class JudgeEvaluation(BaseModel):
    score: float | None = None
    passed: bool | None = None
    failure_stage: str | None = None
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""
    model: str
    available: bool = True


class CaseEvaluationDetail(BaseModel):
    case_id: str
    mode: str
    query: str
    turns: list[str] = Field(default_factory=list)
    expected_skills: list[str] = Field(default_factory=list)
    selected_skills: list[str] = Field(default_factory=list)
    expected_tool_calls: list[ToolCallExpectation] = Field(default_factory=list)
    actual_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    final_answer: str = ""
    checks: list[EvaluationCheck] = Field(default_factory=list)
    diagnosis: CaseDiagnosis
    status: str = "pass"  # pass / warning / fail
    skill_selection_precision: float | None = None
    skill_selection_recall: float | None = None
    skill_selection_f1: float | None = None
    judge: JudgeEvaluation | None = None
    log_summary: list[dict[str, Any]] = Field(default_factory=list)


class SkillEvaluationResult(BaseModel):
    skill_name: str
    overall_score: float
    static: StaticSkillMetrics
    runtime: RuntimeSkillMetrics | None = None
    score_components: dict[str, float] = Field(default_factory=dict)


class SkillEvaluationReport(BaseModel):
    skills: list[SkillEvaluationResult]
    routing: RoutingMetrics | None = None
    safety: SafetyEvaluationMetrics | None = None
    tools: ToolEvaluationMetrics | None = None
    answers: AnswerEvaluationMetrics | None = None
    hallucinations: HallucinationEvaluationMetrics | None = None
    case_details: list[CaseEvaluationDetail] = Field(default_factory=list)
