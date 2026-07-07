"""子 Agent 通信协议：结构化输入输出模型。

与现有的 JSON-over-prompt 软约束不同，这些模型通过
``with_structured_output()`` 强制子 Agent 返回符合 schema 的 JSON。

仅用于 multi-agent 模式，不影响 single-agent 路径。
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 子 Agent 输入协议 ──────────────────────────────────────────────────


class SubAgentInput(BaseModel):
    """主 Agent 派发给子 Agent 的结构化任务输入。

    替代当前 hand-crafted JSON dict payload（multi_agent.py:131-141），
    确保每个子 Agent 收到一致的、完整的上下文信息。
    """

    task_id: str = Field(description="主 agent 分配的任务 ID，用于关联父子任务")
    agent: str = Field(description="子 agent 名称: metrics|troubleshoot|patrol|audit")
    query: str = Field(description="用户原始查询（已 rewrite）")
    intent_slots: dict[str, Any] = Field(
        default_factory=dict,
        description="意图槽位: intent, metrics, entities, confidence 等",
    )
    user_vector_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Qdrant 检索到的用户向量知识文档",
    )
    plan_hint: dict[str, Any] = Field(
        default_factory=dict,
        description="supervisor 的调度计划摘要（subagents 列表等）",
    )
    communication_contract: dict[str, Any] = Field(
        default_factory=lambda: {
            "format": "json",
            "required_fields": [
                "agent",
                "task_id",
                "status",
                "findings",
                "evidence",
                "recommendations",
                "confidence",
            ],
        },
        description="子 agent 输出必须包含的字段",
    )


# ── 子 Agent 输出协议 ──────────────────────────────────────────────────


class SubAgentReport(BaseModel):
    """子 Agent 返回的结构化报告。

    对比旧格式新增了 ``task_id``、``status``、``tools_used``、``error``
    字段，用于前端状态跟踪和排障。
    """

    agent: str = Field(description="子 agent 名称")
    task_id: str = Field(description="关联的任务 ID（与 SubAgentInput.task_id 一致）")
    status: Literal["completed", "failed"] = Field(
        default="completed",
        description="执行状态: completed 或 failed",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="分析发现（每条一句话）",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="支撑 findings 的具体证据（日志片段、指标值、trace 摘要）",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="可执行的建议列表",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="分析结论的置信度 (0.0-1.0)",
    )
    tools_used: list[str] = Field(
        default_factory=list,
        description="执行期间调用的工具名称列表，用于审计和前端展示",
    )
    error: str | None = Field(
        default=None,
        description="失败时的错误信息；status=completed 时必须为 null",
    )


# ── 子 Agent 任务状态（前端跟踪用）──────────────────────────────────────


class ChildAgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ChildAgentTask(BaseModel):
    """单个子 Agent 任务的状态快照。

    存储在 ``AgentState.child_agent_tasks`` 中，gate 节点
    轮询此列表判断是否所有任务已完成。
    """

    task_id: str
    agent_name: str  # metrics / troubleshoot / patrol / audit
    status: ChildAgentStatus = ChildAgentStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
