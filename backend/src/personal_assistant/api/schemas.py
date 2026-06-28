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


class SkillInfo(BaseModel):
    name: str
    description: str
    tool_names: list[str]
    path: str
    loaded: bool = False


class ReplayResponse(BaseModel):
    thread_id: str
    states: list[dict[str, Any]]
