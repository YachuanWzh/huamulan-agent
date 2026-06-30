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


class ApprovalDecision(BaseModel):
    thread_id: str
    approval_id: str
    approved: bool


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


class SkillInfo(BaseModel):
    name: str
    description: str
    tool_names: list[str]
    path: str
    loaded: bool = False


class ReplayResponse(BaseModel):
    thread_id: str
    states: list[dict[str, Any]]


class ThreadSummary(BaseModel):
    thread_id: str
    updated_at: datetime | None = None


class DeleteThreadResponse(BaseModel):
    thread_id: str
    deleted: bool = True


class ClearThreadsResponse(BaseModel):
    thread_ids: list[str]
    deleted: int
