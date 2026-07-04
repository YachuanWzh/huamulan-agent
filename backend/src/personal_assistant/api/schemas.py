from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float | None = None


class ChatRequest(BaseModel):
    thread_id: str
    message: str
    llm: LLMConfig | None = None
    agent_mode: Literal["single", "multi"] = "single"


class ApprovalDecision(BaseModel):
    thread_id: str
    approval_id: str
    approved: bool


class ApprovalBatchItem(BaseModel):
    approval_id: str
    approved: bool


class ApprovalBatchDecision(BaseModel):
    thread_id: str
    decisions: list[ApprovalBatchItem]


class ToolCallApproval(BaseModel):
    approval_id: str
    tool_call_id: str
    name: str
    args: dict[str, Any]


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "requires_approval"]
    message: str | None = None
    approvals: list[ToolCallApproval] = Field(default_factory=list)


class AuditEventCreate(BaseModel):
    thread_id: str | None = None
    source: Literal["prompt", "tool"]
    category: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    reason: str
    subject: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(AuditEventCreate):
    id: int
    created_at: datetime


class ToolError(BaseModel):
    id: int
    created_at: datetime
    thread_id: str | None = None
    tool_call_id: str
    tool_name: str
    tool_args: dict[str, Any]
    attempt: int
    max_attempts: int
    error_type: str
    error_message: str
    will_retry: bool


class ExecutionLogCreate(BaseModel):
    thread_id: str
    run_id: str | None = None
    parent_id: str | None = None
    event_type: Literal[
        "turn",
        "skill_route",
        "llm",
        "tool",
        "tool_retry",
        "approval",
        "security",
        "multiagent",
    ]
    status: Literal[
        "started",
        "completed",
        "failed",
        "blocked",
        "retrying",
        "approved",
        "denied",
    ]
    name: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionLog(ExecutionLogCreate):
    id: int
    created_at: datetime


class ExecutionSummary(BaseModel):
    thread_id: str
    total_events: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    tool_retries: int = 0
    security_events: int = 0
    total_duration_ms: int = 0


class SkillInfo(BaseModel):
    name: str
    description: str
    tool_names: list[str]
    path: str
    loaded: bool = False
    evaluation: "SkillEvaluationSummary | None" = None
    latest_evaluation: "SkillEvaluationSnapshot | None" = None


class SkillEvaluationSummary(BaseModel):
    overall_score: float
    description_tokens: int
    skill_md_lines: int
    python_lines: int
    max_cyclomatic_complexity: int
    tool_count: int


class SkillEvaluationSnapshot(BaseModel):
    id: int
    created_at: datetime
    skill_name: str
    overall_score: float
    routing_score: float | None = None
    runtime_score: float | None = None
    usage_score: float | None = None
    static_score: float | None = None
    source: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)


class SkillEvaluationRunRequest(BaseModel):
    golden_path: str | None = None
    evaluation_mode: Literal["quick", "e2e"] = "quick"
    agent_mode: Literal["single", "multi"] = "single"


class SkillEvaluationDataset(BaseModel):
    name: str
    path: str
    label: str


class SkillEvaluationRunResponse(BaseModel):
    source: str
    results: list[SkillEvaluationSnapshot]


class SkillEvaluationResetResponse(BaseModel):
    deleted: int
    results: list[SkillEvaluationSnapshot] = Field(default_factory=list)


class ReplayResponse(BaseModel):
    thread_id: str
    states: list[dict[str, Any]]


class ThreadSummary(BaseModel):
    thread_id: str
    updated_at: datetime | None = None
    summary: str | None = None


class DeleteThreadResponse(BaseModel):
    thread_id: str
    deleted: bool = True


class ClearThreadsResponse(BaseModel):
    thread_ids: list[str]
    deleted: int
