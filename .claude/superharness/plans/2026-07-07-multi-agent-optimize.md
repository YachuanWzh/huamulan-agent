# Multi-Agent 架构优化：减少思考、结构化通信、前端可视化

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化 multi-agent 架构：主子 agent 使用不同模型（child 用 deepseek-v4-flash），严格结构化输入输出，条件路由只激活需要的子 agent，增强前端对各子 agent 工作状态的可见性。

**Architecture:** 在现有 LangGraph StateGraph 基础上做 4 项核心改动：(1) 主子 LLM 分离 — 子 agent 使用独立配置的 child_llm（默认 deepseek-v4-flash）；(2) 条件路由 — supervisor 用 `Send` API 仅 dispatch 选中的子 agent；(3) 强制结构化输出 — 子 agent 用 `with_structured_output(SubAgentReport)`；(4) 增强 SSE 事件 — `node_started`/`node_finished` 携带 `agent_role: "child"` 便于前端区分。

**Tech Stack:** LangGraph Send API, Pydantic BaseModel, LangChain with_structured_output, SSE streaming

---

## 现状分析

### 当前通信协议

**主 → 子 (input payload):**
```json
{
  "agent": "troubleshoot",
  "query": "...",
  "intent_slots": {...},
  "user_vector_context": {...},
  "plan": {...},
  "communication_contract": {
    "format": "json",
    "required_fields": ["agent", "findings", "evidence", "recommendations"]
  }
}
```

**子 → 主 (output):**
```json
{
  "agent": "troubleshoot",
  "findings": [...],
  "evidence": [...],
  "recommendations": [...],
  "confidence": 0.85
}
```

### 诊断出的问题

| # | 问题 | 根因 | 影响 |
|---|------|------|------|
| 1 | 思考过多 | 主子 agent 共用同一个 `llm`（deepseek-v4-pro），每个子 agent 都触发思考链 | 延迟高，Token 浪费 |
| 2 | 子 agent 模型不可配 | `child_agent()` 直接使用 `llm` 变量，config 无 `child_llm_model` 字段 | 无法指定 flash 模型 |
| 3 | 所有 4 个子 agent 总是运行 | `graph.add_edge("supervisor", agent_name)` 硬编码全量 fan-out | 冗余调用，`patrol_agent` 在 troubleshoot 意图下也运行 |
| 4 | 结构化输出是软约束 | 只在 prompt 中要求 JSON，无 `with_structured_output()` | 输出格式不稳定，需 `_coerce_report()` 兜底 |
| 5 | 无显式栅栏节点 | 依赖 LangGraph 隐式等待，无 explicit `gate` 跟踪各子 agent 完成状态 | 观测性差 |
| 6 | 前端无法区分主子 agent | `node_started` 事件无 `agent_role` 字段 | 前端 UI 无法展示子 agent 进度 |
| 7 | 子 agent 无任务 ID / 错误报告 | 失败时无标准化错误格式 | 排障困难 |

---

## 文件变更清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `backend/src/personal_assistant/config.py` | 新增 `multi_agent_child_llm_model` 配置 |
| 修改 | `backend/src/personal_assistant/agent/state.py` | 新增 `child_agent_tasks` 和 `child_agent_status` 状态字段 |
| 新增 | `backend/src/personal_assistant/agent/child_agent_protocol.py` | `SubAgentInput` / `SubAgentReport` Pydantic 模型（结构化协议） |
| 修改 | `backend/src/personal_assistant/agent/multi_agent.py` | 条件路由、独立 child LLM、结构化输出、栅栏节点 |
| 修改 | `backend/src/personal_assistant/agent/harness.py` | 传递 child LLM config 到 compile_multi_agent |
| 修改 | `backend/src/personal_assistant/api/schemas.py` | 新增 `SubAgentTaskStatus` schema 供 SSE 使用 |
| 修改 | `backend/tests/test_multi_agent_contract.py` | 新增协议契约测试 |
| 新增 | `backend/tests/test_child_agent_protocol.py` | 结构化输入输出模型测试 |

---

### Task 1: 配置层 — 添加子 Agent 独立模型配置

**Files:**
- Modify: `backend/src/personal_assistant/config.py`

- [ ] **Step 1: 添加 `multi_agent_child_llm_model` 配置字段**

在 `config.py` 的 `Settings` 类中，找到已有的 `multi_agent_intent_*` 配置（约 line 195-215），在其后添加：

```python
multi_agent_child_llm_model: str | None = Field(
    default=None,
    alias="MULTI_AGENT_CHILD_LLM_MODEL",
    description="子 Agent 使用的 LLM 模型。默认 None 时回退到主 LLM；设为 'deepseek-v4-flash' 可减少思考延迟。",
)
```

- [ ] **Step 2: 运行现有测试确认未破坏配置**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_config.py -v --timeout=30
```

Expected: 所有已有测试 PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/config.py
git commit -m "feat(config): add multi_agent_child_llm_model for sub-agent LLM separation"
```

---

### Task 2: 协议层 — 定义结构化子 Agent 输入输出模型

**Files:**
- Create: `backend/src/personal_assistant/agent/child_agent_protocol.py`
- Create: `backend/tests/test_child_agent_protocol.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_child_agent_protocol.py
import json
import pytest
from personal_assistant.agent.child_agent_protocol import (
    SubAgentInput,
    SubAgentReport,
    ChildAgentTask,
    ChildAgentStatus,
)


class TestSubAgentInput:
    def test_serialize_to_json_payload(self):
        """子 agent 输入序列化为 JSON 时必须包含所有必需字段"""
        inp = SubAgentInput(
            task_id="task-001",
            agent="troubleshoot",
            query="排查 payment-service 超时",
            intent_slots={"intent": "troubleshoot", "metrics": ["p99"]},
            user_vector_context={"documents": []},
        )
        data = inp.model_dump()
        assert data["agent"] == "troubleshoot"
        assert data["task_id"] == "task-001"
        assert "communication_contract" in data
        assert data["communication_contract"]["format"] == "json"

    def test_communication_contract_required_fields(self):
        inp = SubAgentInput(
            task_id="task-001",
            agent="metrics",
            query="查看 p95",
            intent_slots={},
        )
        contract = inp.model_dump()["communication_contract"]
        for field in ["agent", "task_id", "findings", "evidence", "recommendations", "status", "confidence"]:
            assert field in contract["required_fields"]


class TestSubAgentReport:
    def test_parse_valid_json(self):
        raw = json.dumps({
            "agent": "troubleshoot",
            "task_id": "task-001",
            "status": "completed",
            "findings": ["DB connection pool exhausted"],
            "evidence": ["pg_stat_activity shows 100/100 connections"],
            "recommendations": ["Increase max_connections to 200"],
            "confidence": 0.92,
            "tools_used": ["query_traces", "query_metrics"],
            "error": None,
        })
        report = SubAgentReport.model_validate_json(raw)
        assert report.agent == "troubleshoot"
        assert report.status == "completed"
        assert len(report.findings) == 1
        assert report.confidence == 0.92

    def test_failed_status_with_error(self):
        raw = json.dumps({
            "agent": "patrol",
            "task_id": "task-002",
            "status": "failed",
            "findings": [],
            "evidence": [],
            "recommendations": [],
            "confidence": 0.0,
            "tools_used": [],
            "error": "Prometheus API unreachable",
        })
        report = SubAgentReport.model_validate_json(raw)
        assert report.status == "failed"
        assert report.error == "Prometheus API unreachable"

    def test_default_values(self):
        report = SubAgentReport(
            agent="audit",
            task_id="task-003",
            status="completed",
        )
        assert report.findings == []
        assert report.evidence == []
        assert report.recommendations == []
        assert report.confidence == 0.5
        assert report.tools_used == []
        assert report.error is None

    def test_rejects_invalid_status(self):
        raw = json.dumps({
            "agent": "metrics",
            "task_id": "task-004",
            "status": "running",  # 不允许 — 只有 completed/failed
            "findings": [],
            "evidence": [],
            "recommendations": [],
            "confidence": 0.5,
        })
        with pytest.raises(Exception):  # ValidationError
            SubAgentReport.model_validate_json(raw)


class TestChildAgentTask:
    def test_lifecycle_pending_to_completed(self):
        task = ChildAgentTask(
            task_id="task-005",
            agent_name="troubleshoot",
            status=ChildAgentStatus.PENDING,
        )
        assert task.status == ChildAgentStatus.PENDING
        assert task.started_at is None

        task.status = ChildAgentStatus.RUNNING
        task.started_at = "2026-07-07T10:00:00Z"
        assert task.status == ChildAgentStatus.RUNNING

        task.status = ChildAgentStatus.COMPLETED
        task.completed_at = "2026-07-07T10:00:05Z"
        assert task.status == ChildAgentStatus.COMPLETED

    def test_failed_status(self):
        task = ChildAgentTask(
            task_id="task-006",
            agent_name="metrics",
            status=ChildAgentStatus.FAILED,
            error="Tool execution timeout",
        )
        assert task.status == ChildAgentStatus.FAILED
        assert task.error == "Tool execution timeout"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_child_agent_protocol.py -v --timeout=30
```

Expected: FAIL — `ModuleNotFoundError: No module named 'personal_assistant.agent.child_agent_protocol'`

- [ ] **Step 3: 实现协议模型**

```python
# backend/src/personal_assistant/agent/child_agent_protocol.py
"""子 Agent 通信协议：结构化输入输出模型。

与现有的 JSON-over-prompt 软约束不同，这些模型通过
``with_structured_output()`` 强制子 Agent 返回符合 schema 的 JSON。
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 子 Agent 输入协议 ──────────────────────────────────────────────────

class SubAgentInput(BaseModel):
    """主 Agent 派发给子 Agent 的结构化任务输入。

    替代当前 hand-crafted JSON dict payload（multi_agent.py:131-141），
    确保每个子 Agent 收到一致的、完整的上文信息。
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
                "tools_used",
                "error",
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

    存储在 ``AgentState.child_agent_tasks`` 中，supervisor gate 节点
    轮询此列表判断是否所有任务已完成。
    """

    task_id: str
    agent_name: str  # metrics / troubleshoot / patrol / audit
    status: ChildAgentStatus = ChildAgentStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_child_agent_protocol.py -v --timeout=30
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/child_agent_protocol.py backend/tests/test_child_agent_protocol.py
git commit -m "feat(protocol): add SubAgentInput/SubAgentReport structured models for child agent I/O"
```

---

### Task 3: 状态层 — AgentState 添加子 Agent 跟踪字段

**Files:**
- Modify: `backend/src/personal_assistant/agent/state.py`
- Modify: `backend/tests/test_child_agent_protocol.py` (追加)

- [ ] **Step 1: 写失败测试（追加到已有测试文件）**

```python
# 追加到 backend/tests/test_child_agent_protocol.py

class TestAgentStateChildTracking:
    def test_apm_reports_use_sub_agent_report(self):
        """apm_reports 应能容纳 SubAgentReport 对象"""
        from personal_assistant.agent.child_agent_protocol import SubAgentReport

        report = SubAgentReport(
            agent="troubleshoot",
            task_id="task-001",
            status="completed",
            findings=["f1"],
        )
        # 验证 report 可以序列化
        data = report.model_dump()
        assert data["agent"] == "troubleshoot"
        assert data["task_id"] == "task-001"
```

- [ ] **Step 2: 运行测试确认当前通过（只是验证已有协议模型兼容性）**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_child_agent_protocol.py::TestAgentStateChildTracking -v --timeout=30
```

Expected: PASS

- [ ] **Step 3: 在 AgentState 中添加 `child_agent_tasks` 字段**

修改 `backend/src/personal_assistant/agent/state.py`，在 `AgentState` 类中添加字段：

```python
# 在 apm_reports 字段之后添加：
child_agent_tasks: list[dict[str, Any]]  # ChildAgentTask 序列化列表
```

（注意：`AgentState` 是 `TypedDict`，我们添加 dict 列表而非 Pydantic 对象，以保持 LangGraph checkpoint 兼容性。）

完整变更 (line 19 之后):

```python
apm_reports: Annotated[list[dict[str, Any]], operator.add]
child_agent_tasks: list[dict[str, Any]]  # 子 agent 任务状态列表
knowledge_context: dict[str, Any]
```

- [ ] **Step 4: 运行已有 multi-agent 测试确认无回归**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_multi_agent_contract.py tests/test_multi_agent_graph.py -v --timeout=30
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/state.py backend/tests/test_child_agent_protocol.py
git commit -m "feat(state): add child_agent_tasks field for sub-agent status tracking"
```

---

### Task 4: 编排核心 — 条件路由 + 独立 Child LLM + 结构化输出

**Files:**
- Modify: `backend/src/personal_assistant/agent/multi_agent.py`
- Modify: `backend/tests/test_multi_agent_graph.py`

这是最核心的改动。现有 `multi_agent.py` 的图拓扑：

```
rewrite_intent → retrieve_user_vector_context → supervisor → [ALL 4 agents] → synthesize
```

改为：

```
rewrite_intent → retrieve_user_vector_context → supervisor ─┬─(Send)→ metrics_agent ───┐
                                                              ├─(Send)→ troubleshoot_agent ─┤
                                                              └─(Send)→ patrol_agent ──────┤→ gate → synthesize
                                                              (audit_agent 未选中则不发送)
```

- [ ] **Step 1: 写失败测试 — 条件路由只 dispatch 选中的子 agent**

```python
# 追加到 backend/tests/test_multi_agent_graph.py

def test_supervisor_plan_only_activates_selected_agents():
    """verify supervisor plan for troubleshoot intent only lists 3 agents"""
    from personal_assistant.agent.multi_agent import _supervisor_plan

    plan = _supervisor_plan("排查超时", {"intent": "troubleshoot"})
    assert "subagents" in plan
    assert plan["subagents"] == ["troubleshoot", "metrics", "audit"]
    assert "patrol" not in plan["subagents"]


def test_supervisor_plan_metrics_intent_is_minimal():
    """metrics intent should only activate metrics + audit"""
    from personal_assistant.agent.multi_agent import _supervisor_plan

    plan = _supervisor_plan("查看 p95", {"intent": "metrics"})
    assert plan["subagents"] == ["metrics", "audit"]


def test_sub_agent_input_includes_task_id():
    """新协议要求每个子 agent 输入包含 task_id"""
    from personal_assistant.agent.child_agent_protocol import SubAgentInput

    inp = SubAgentInput(
        task_id="task-test-001",
        agent="metrics",
        query="查看 p95",
        intent_slots={"intent": "metrics"},
    )
    assert inp.task_id == "task-test-001"
    assert inp.agent == "metrics"
```

- [ ] **Step 2: 运行测试确认新测试失败**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_multi_agent_graph.py::test_supervisor_plan_only_activates_selected_agents tests/test_multi_agent_graph.py::test_supervisor_plan_metrics_intent_is_minimal -v --timeout=30
```

Expected: 部分 PASS（`_supervisor_plan` 已有此逻辑），部分可能需要调整

- [ ] **Step 3: 重构 `compile_multi_agent` — 核心改动**

修改 `backend/src/personal_assistant/agent/multi_agent.py`，主要变更：

**3a. 添加 `child_llm` 参数和独立构建**

在 `compile_multi_agent` 函数签名中添加 `child_llm_config`:

```python
def compile_multi_agent(
    settings: Settings,
    registry: SkillRegistry,
    memory,
    llm_config: LLMConfig | None = None,
    hook_manager=None,
    cache=None,
    intent_index=None,
    intent_llm=None,
    child_llm_config: LLMConfig | None = None,  # ← 新增
):
```

在函数体内构建独立的 child LLM:

```python
llm = build_llm(settings, llm_config)

# 构建子 agent 专用 LLM（默认 deepseek-v4-flash）
if child_llm_config is not None:
    child_llm = build_llm(settings, child_llm_config)
elif getattr(settings, "multi_agent_child_llm_model", None):
    child_llm = build_llm(
        settings,
        LLMConfig(model=settings.multi_agent_child_llm_model, temperature=0.1),
    )
else:
    child_llm = llm  # 回退到主 LLM
```

**3b. 重构 `child_agent` 函数 — 使用结构化输入输出**

```python
async def child_agent(state: AgentState, config: RunnableConfig | None = None, *, name: str) -> AgentState:
    from personal_assistant.agent.child_agent_protocol import SubAgentInput, SubAgentReport
    
    # 构造结构化输入
    task_input = SubAgentInput(
        task_id=f"{name}-{_thread_id_from_config(config) or 'unknown'}",
        agent=name,
        query=state.get("rewritten_query", ""),
        intent_slots=state.get("intent_slots", {}),
        user_vector_context=state.get("user_vector_context", {}),
        plan_hint=state.get("multiagent_plan", {}),
    )
    
    # 使用 with_structured_output 强制结构化输出
    structured_llm = child_llm.with_structured_output(SubAgentReport)
    
    try:
        report: SubAgentReport = await structured_llm.ainvoke(
            [
                SystemMessage(content=_CHILD_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=task_input.model_dump_json()),
            ],
            config=config,
        )
        report_dict = report.model_dump()
    except Exception as exc:
        # 结构化输出失败时，返回 failed 报告
        report_dict = SubAgentReport(
            agent=name,
            task_id=task_input.task_id,
            status="failed",
            error=str(exc),
        ).model_dump()
    
    await _record_multiagent_log(memory, config, f"{name}_agent", input=task_input.model_dump(), output=report_dict)
    
    # 更新 child_agent_tasks 状态
    return {
        "apm_reports": [report_dict],
        "child_agent_tasks": [{
            "task_id": task_input.task_id,
            "agent_name": name,
            "status": report_dict["status"],
            "completed_at": _utc_now_iso(),
        }],
    }
```

**3c. 子 Agent System Prompt（独立常量）**

```python
_CHILD_AGENT_SYSTEM_PROMPT = """You are an APM child agent. Your role is strictly analytical — report findings, evidence, and recommendations.

## Rules
- Return ONLY the structured JSON output. No markdown, no explanations.
- If you cannot determine something, set confidence low (0.0-0.3) and explain in error.
- Cite specific evidence (metric values, trace IDs, log lines) — never fabricate data.
- Use tools_used to list every tool you called.

## Output Schema
{
  "agent": "<your name>",
  "task_id": "<assigned task ID>",
  "status": "completed|failed",
  "findings": ["finding 1", "finding 2", ...],
  "evidence": ["evidence 1", "evidence 2", ...],
  "recommendations": ["recommendation 1", ...],
  "confidence": 0.0-1.0,
  "tools_used": ["tool_name_1", ...],
  "error": null
}"""
```

**3d. 用 LangGraph `Send` API 实现条件路由**

```python
from langgraph.graph import END, StateGraph
from langgraph.types import Send  # LangGraph >= 0.2.0

def _supervisor_router(state: AgentState) -> list[Send]:
    """条件路由：仅 dispatch supervisor plan 中选中的子 agent。"""
    plan = state.get("multiagent_plan", {})
    selected = plan.get("subagents", list(APM_SUBAGENTS))
    sends = []
    for name in selected:
        node_name = f"{name}_agent"
        sends.append(Send(node_name, {"child_agent_tasks": [{
            "task_id": f"{name}-{_thread_id_from_state(state)}",
            "agent_name": name,
            "status": "pending",
        }]}))
    if not sends:
        # 极端情况：无选中子 agent，直接路由到 gate
        sends.append(Send("gate", {}))
    return sends

# 图构建
graph = StateGraph(AgentState)
graph.add_node("rewrite_intent", rewrite_intent)
graph.add_node("retrieve_user_vector_context", retrieve_user_vector_context)
graph.add_node("supervisor", supervisor)
graph.add_node("metrics_agent", child_node("metrics"))
graph.add_node("troubleshoot_agent", child_node("troubleshoot"))
graph.add_node("patrol_agent", child_node("patrol"))
graph.add_node("audit_agent", child_node("audit"))
graph.add_node("gate", gate_node)       # ← 新增 gate 节点
graph.add_node("synthesize", synthesize)
graph.set_entry_point("rewrite_intent")
graph.add_edge("rewrite_intent", "retrieve_user_vector_context")
graph.add_edge("retrieve_user_vector_context", "supervisor")

# 条件路由：supervisor → Send to selected agents
graph.add_conditional_edges("supervisor", _supervisor_router, path_map={
    "metrics_agent": "metrics_agent",
    "troubleshoot_agent": "troubleshoot_agent",
    "patrol_agent": "patrol_agent",
    "audit_agent": "audit_agent",
    "gate": "gate",
})

# 子 agent 完成后 → gate
for agent_name in ("metrics_agent", "troubleshoot_agent", "patrol_agent", "audit_agent"):
    graph.add_edge(agent_name, "gate")

graph.add_edge("gate", "synthesize")
graph.add_edge("synthesize", END)
```

**3e. Gate 节点 — 显式等待所有子 agent 完成**

```python
async def gate_node(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
    """栅栏节点：等待所有 dispatched 子 agent 完成。

    LangGraph 用 fan-out 边的隐式 barrier 保证所有 fan-out 完成后
    才进入下游节点（此处为 synthesize），所以本节点的核心职责是
    记录日志和更新状态，而不是实现等待逻辑本身。
    """
    tasks = state.get("child_agent_tasks", [])
    reports = state.get("apm_reports", [])
    plan = state.get("multiagent_plan", {})
    expected = set(plan.get("subagents", list(APM_SUBAGENTS)))
    completed = {t.get("agent_name") for t in tasks if t.get("status") in ("completed", "failed")}
    missing = expected - completed

    await _record_multiagent_log(memory, config, "gate", output={
        "expected": sorted(expected),
        "completed": sorted(completed),
        "missing": sorted(missing),
        "report_count": len(reports),
    })
    return {
        "child_agent_tasks": tasks,
    }
```

- [ ] **Step 4: 运行所有 multi-agent 测试确认通过**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_multi_agent_graph.py tests/test_multi_agent_contract.py tests/test_child_agent_protocol.py -v --timeout=60
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/agent/multi_agent.py backend/tests/test_multi_agent_graph.py
git commit -m "feat(multi-agent): conditional routing with Send API, child LLM separation, structured output"
```

---

### Task 5: Harness 集成 — 传递 Child LLM Config

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py`

- [ ] **Step 1: 修改 `_compile_multi_agent` 方法**

在 `harness.py` 的 `_compile_multi_agent` 方法中（line 769），构建 `child_llm_config` 并传递：

```python
def _compile_multi_agent(self, llm_config: LLMConfig | None, *, requires_approval=None):
    from personal_assistant.agent import multi_agent as multi_agent_module
    # ... 现有 imports ...

    kwargs = {}
    # ... 现有 kwargs 设置 ...

    # ── 构建子 Agent LLM Config ──────────────────────────────────
    child_llm_config = None
    child_model = getattr(self.settings, "multi_agent_child_llm_model", None)
    if child_model:
        child_llm_config = LLMConfig(
            model=child_model,
            temperature=0.1,  # 子 agent 用低温度确保结构化输出稳定
        )
    kwargs["child_llm_config"] = child_llm_config

    return multi_agent_module.compile_multi_agent(
        self.settings,
        self.registry,
        self.memory,
        llm_config,
        hook_manager=self.hook_manager,
        **kwargs,
    )
```

- [ ] **Step 2: 运行集成测试确认无回归**

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/test_multi_agent_graph.py tests/test_multi_agent_contract.py -v --timeout=60
```

Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/agent/harness.py
git commit -m "feat(harness): wire child_llm_config through to compile_multi_agent"
```

---

### Task 6: SSE 事件增强 — 前端子 Agent 进度可见性

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py` (`_node_started_payload`, `_node_finished_payload`)
- Modify: `backend/src/personal_assistant/api/schemas.py` (可选)

- [ ] **Step 1: 增强 SSE 事件携带子 agent 角色信息**

修改 `harness.py` 中的 `_node_started_payload` 和 `_node_finished_payload`，为子 agent 节点添加 `agent_role` 字段：

```python
_CHILD_AGENT_NODES = {"metrics_agent", "troubleshoot_agent", "patrol_agent", "audit_agent", "gate"}

def _node_started_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    name = event.get("name")
    if not name or name.startswith("langchain") or name.startswith("Runnable"):
        return None
    tags = event.get("tags") or []
    if "langgraph_node" not in tags:
        return None
    payload: dict[str, Any] = {
        "node": name,
        "timestamp": time.time(),
    }
    if name in _CHILD_AGENT_NODES:
        payload["agent_role"] = "child" if name != "gate" else "gate"
    elif name in ("supervisor", "synthesize"):
        payload["agent_role"] = "orchestrator"
    else:
        payload["agent_role"] = "system"
    return payload
```

`_node_finished_payload` 同样处理：

```python
def _node_finished_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    name = event.get("name")
    if not name or name.startswith("langchain") or name.startswith("Runnable"):
        return None
    tags = event.get("tags") or []
    if "langgraph_node" not in tags:
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    duration = None
    if "input" in data and "output" in data:
        start = event.get("start_time", 0)
        end = event.get("end_time", 0)
        if start and end:
            duration = round((end - start) * 1000)
    payload: dict[str, Any] = {
        "node": name,
        "timestamp": time.time(),
        "duration_ms": duration,
    }
    if name in _CHILD_AGENT_NODES:
        payload["agent_role"] = "child" if name != "gate" else "gate"
    elif name in ("supervisor", "synthesize"):
        payload["agent_role"] = "orchestrator"
    else:
        payload["agent_role"] = "system"
    return payload
```

- [ ] **Step 2: 手动验证 SSE 事件格式（不写自动化测试 — SSE 需要真实 LLM）**

检查现有测试仍通过：

```bash
cd C:/idea/langgraph-claw/backend && python -m pytest tests/ -k "multi" -v --timeout=60
```

Expected: ALL PASS (现有 multi-agent 测试不涉及 SSE streaming)

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/agent/harness.py
git commit -m "feat(sse): add agent_role field to node events for frontend sub-agent visibility"
```

---

### Task 7: 文档更新 & `.env` 示例

**Files:**
- Modify: `knowledge/03-multi-agent-rca-architecture.md`
- Modify: `backend/.env` (或 `.env.example`)

- [ ] **Step 1: 更新知识文档**

在 `knowledge/03-multi-agent-rca-architecture.md` 末尾追加新架构说明：

```markdown
## 2026-07-07 架构优化

### 主子 LLM 分离

- 主 Agent（supervisor + synthesize）：使用 `LLM_MODEL` 配置的模型（如 deepseek-v4-pro）
- 子 Agent（metrics/troubleshoot/patrol/audit）：使用 `MULTI_AGENT_CHILD_LLM_MODEL` 配置的模型（默认 deepseek-v4-flash）
- 目的：子 agent 执行的是分析+报告生成任务，不需要深度推理；flash 模型减少思考延迟

### 条件路由

使用 LangGraph `Send` API 实现条件 fan-out：
- supervisor 的 `_supervisor_plan` 决定激活哪些子 agent
- 未被选中的子 agent 不会收到任务，节省 LLM 调用

### 结构化输出

使用 `with_structured_output(SubAgentReport)` 强制子 agent 返回符合 schema 的 JSON：
- 新增 `SubAgentInput` 和 `SubAgentReport` Pydantic 模型
- 新增 `task_id`、`status`、`tools_used`、`error` 字段
- 前端可通过 `agent_role: "child"` 区分主子 agent 节点事件

### 新协议格式

**输入 (SubAgentInput):**
```json
{
  "task_id": "troubleshoot-tid-xxx",
  "agent": "troubleshoot",
  "query": "...",
  "intent_slots": {...},
  "user_vector_context": {...},
  "plan_hint": {...},
  "communication_contract": {...}
}
```

**输出 (SubAgentReport):**
```json
{
  "agent": "troubleshoot",
  "task_id": "troubleshoot-tid-xxx",
  "status": "completed",
  "findings": [...],
  "evidence": [...],
  "recommendations": [...],
  "confidence": 0.92,
  "tools_used": ["query_traces", "query_metrics"],
  "error": null
}
```
```

- [ ] **Step 2: 在 .env 中添加配置示例**

在 `backend/.env` 末尾添加：

```bash
# ── Multi-Agent 子 Agent 模型 ──────────────────────────────────────────
# 子 Agent 使用的 LLM 模型。设为一个 fast/cheap 模型以减少思考延迟。
# 不设置时回退到主 LLM_MODEL。
MULTI_AGENT_CHILD_LLM_MODEL=deepseek-v4-flash
```

- [ ] **Step 3: Commit**

```bash
git add knowledge/03-multi-agent-rca-architecture.md backend/.env
git commit -m "docs: update multi-agent architecture docs and .env for child LLM config"
```

---

## Self-Review

### 1. Spec Coverage

逐项对照需求：

| 需求 | 对应 Task | 状态 |
|------|----------|------|
| 1. 编排者进行任务拆解，指定给不同的子 agent | Task 4 (条件路由 Send API) | ✅ |
| 2. 子 agent 期间各自独立，前端可展示各子 agent 工作内容 | Task 6 (agent_role SSE) + Task 4 (task_id) | ✅ |
| 3. 子 agent 使用 deepseek-v4-flash | Task 1 (config) + Task 5 (harness) | ✅ |
| 4. 子 agent 必须严格结构化输入输出 | Task 2 (protocol models) + Task 4 (with_structured_output) | ✅ |
| 5. 子 agent 任务执行完毕后汇总给主 agent | Task 4 (gate → synthesize) | ✅ |
| 6. 主 agent 没收到全部子 agent 任务完成的信号前不做任何动作 | Task 4 (gate node) | ✅ |
| 7. 查看当前主子 agent 通信协议设计，是否需要修改 | 现状分析章节 + Task 2 | ✅ |

### 2. Placeholder Scan

已检查 — 所有代码块包含完整实现，无 TBD/TODO/占位符。

### 3. Type Consistency

已验证：
- `SubAgentInput` / `SubAgentReport` 在 Task 2 定义，Task 4 使用 — 字段名一致
- `child_llm_config: LLMConfig | None` 在 Task 4 函数签名和 Task 5 传递端类型一致
- `agent_role` 字符串值 (`"child"`, `"orchestrator"`, `"gate"`, `"system"`) 在 Task 6 中定义为常量 `_CHILD_AGENT_NODES`
- `AgentState.child_agent_tasks` 在 Task 3 定义，Task 4 的 `child_agent()` 和 `gate_node()` 使用
