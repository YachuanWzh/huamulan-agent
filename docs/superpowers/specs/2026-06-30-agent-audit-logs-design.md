# Agent Audit Logs Design

## 背景

项目当前使用 PostgreSQL 保存 checkpoint、长期记忆、安全审计事件、工具结果和工具错误。现有右侧栏已有 `Audit` 入口和基础审计页面，但主视图只覆盖安全事件与工具错误，缺少按 thread 串联的 agent 执行流水，也没有统一展示 token usage、工具成功结果、错误重试链和执行耗时。

本次目标是在当前 LangGraph Assistant 中增加面向 thread 的日志记录与审计能力，让用户可以从顶部红框位置或现有审计入口进入审计页面，查看 agent 执行日志，并通过 skill 扩展 agent 的审计 SOP 能力。

## 目标

- 按 `thread_id` 查看 agent 执行日志。
- 日志包含时间、事件类型、工具调用、执行结果、错误、重试、耗时和 token usage。
- 前端参考 Langfuse 的 trace/observation 思路，提供直观的时间线、摘要指标和可展开详情。
- 特别突出工具错误重试过程，展示每次 attempt、错误原因、是否继续 retry、最终结果。
- 创建 `audit-sop` skill，符合 skill-creator 规范，用于让 agent 分析审计日志、定位失败链路和生成审计建议。

## 非目标

- 不接入或替换 Langfuse 服务；本功能以项目 PostgreSQL 为主存储。
- 不删除现有 `audit_events`、`tool_errors`、`tool_results` 表，避免破坏已有测试和兼容接口。
- 不做复杂成本计费，只保存模型返回的 token usage；费用计算可后续扩展。

## 推荐方案

新增统一 `agent_execution_logs` 表作为审计页面主数据源。现有 `audit_events`、`tool_errors`、`tool_results` 保留，用于兼容已有安全审计、错误记录和上下文压缩。

统一日志表把一次 thread 中的 agent 行为建模为 trace 中的 observation：

- LLM 调用记录 token usage 和模型输出摘要。
- Tool 调用记录参数、结果、耗时和状态。
- Retry 记录每次工具失败 attempt。
- Approval 记录审批请求和用户决策。
- Security 记录 prompt/tool guard 事件。
- Skill route 记录命中的 skills。

## 后端数据模型

新增 schema：

```sql
CREATE TABLE IF NOT EXISTS agent_execution_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    thread_id TEXT NOT NULL,
    run_id TEXT,
    parent_id TEXT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    name TEXT,
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    output JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB NOT NULL DEFAULT '{}'::jsonb,
    duration_ms INTEGER,
    token_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agent_execution_logs_thread_created
ON agent_execution_logs (thread_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_agent_execution_logs_thread_type
ON agent_execution_logs (thread_id, event_type, created_at ASC);
```

`event_type` 使用稳定小写枚举字符串：

- `turn`
- `skill_route`
- `llm`
- `tool`
- `tool_retry`
- `approval`
- `security`

`status` 使用：

- `started`
- `completed`
- `failed`
- `blocked`
- `retrying`
- `approved`
- `denied`

`token_usage` 保存原始模型用量并归一化常用字段：

```json
{
  "prompt_tokens": 120,
  "completion_tokens": 80,
  "total_tokens": 200,
  "raw": {}
}
```

如果 provider 返回 reasoning token、cached token 或其他细分字段，保存在 `raw` 中，同时尽量填充 `total_tokens`。

## 后端记录点

在 `PostgresMemory` 中增加：

- `record_execution_log(log)`
- `list_execution_logs(thread_id, limit=500)`
- `execution_log_summary(thread_id)`

在 agent/harness 链路中记录：

- `run_user_turn` 和 `run_user_turn_stream`：记录 turn started/completed/failed。
- `route_skills` hook after：记录 selected skills。
- `call_agent` 前后：记录 LLM started/completed/failed，并从 response metadata 中抽取 token usage。
- `execute_tools`：记录每个 tool call 的 input、output、duration 和 status。
- `_execute_tool_calls_with_retry`：每次异常记录 `tool_retry`，包含 attempt、max_attempts、will_retry、error_type、error_message。
- 审批请求/决策：同步写入统一日志，保留现有 `audit_events` 写入。
- prompt/tool guard：同步写入统一日志，保留现有 `audit_events` 写入。

记录失败不能影响主流程。所有审计写入失败只写 logger，不中断 agent 执行。

## API

新增接口：

- `GET /api/threads/{thread_id}/execution-logs?limit=500`
- `GET /api/threads/{thread_id}/execution-summary`

响应模型：

```python
class ExecutionLog(BaseModel):
    id: int
    created_at: datetime
    thread_id: str
    run_id: str | None = None
    parent_id: str | None = None
    event_type: str
    status: str
    name: str | None = None
    input: dict[str, Any] = {}
    output: dict[str, Any] = {}
    error: dict[str, Any] = {}
    duration_ms: int | None = None
    token_usage: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

class ExecutionSummary(BaseModel):
    thread_id: str
    total_events: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    tool_calls: int
    tool_errors: int
    tool_retries: int
    security_events: int
    total_duration_ms: int
```

## 前端设计

红框位置增加主入口按钮，文案为 `Audit` 或图标按钮加 tooltip，点击后进入全宽审计页面。当前右侧 `Audit` tab 保留，仍可切换到同一审计 workspace。

审计页面采用 Langfuse 风格但保持项目现有朴素控制台视觉：

- 顶部 summary strip：Total Tokens、Tool Calls、Errors、Retries、Duration。
- 筛选 tabs：All、LLM、Tools、Retries、Errors、Security、Approvals。
- 主时间线按时间升序展示。
- 每个事件行显示时间、事件类型、名称、状态、耗时、token 或 attempt 摘要。
- LLM 行突出 token usage。
- Tool 行展示工具名、调用参数摘要、结果摘要。
- Retry 链使用同一工具调用 ID 聚合，直观显示 `Attempt 1 failed -> Attempt 2 failed -> Attempt 3 succeeded/failed`。
- 行可展开查看 input/output/error/metadata JSON。
- 空状态清晰提示当前 thread 尚无审计日志。

前端新增 API 类型与客户端方法：

- `ExecutionLog`
- `ExecutionSummary`
- `api.listExecutionLogs(threadId)`
- `api.getExecutionSummary(threadId)`

测试覆盖：

- 点击顶部 Audit 入口进入审计 workspace。
- 审计页面加载 summary 和 logs。
- token usage 正确展示。
- 工具 retry chain 正确聚合和展示。
- 空状态和加载失败不破坏页面。

## Audit SOP Skill

新增 skill 目录：

`backend/src/personal_assistant/skills/audit-sop`

结构：

```text
audit-sop/
  SKILL.md
  agents/
    openai.yaml
```

`SKILL.md` frontmatter 只包含 `name` 和 `description`。描述明确触发场景：分析 agent 审计日志、排查工具失败重试、总结 token 使用异常、生成 thread 审计报告。

正文提供精简 SOP：

1. 确认 thread 范围。
2. 汇总 token usage、工具调用数、错误数和重试数。
3. 找出失败或高重试工具调用。
4. 检查 approval/security 事件。
5. 评估工具输入输出是否符合用户目标。
6. 输出原因、影响、证据和改进建议。

`agents/openai.yaml` 按 skill-creator 规范提供 display name、short description 和 default prompt。

## 测试策略

严格 TDD：

1. 后端 schema/model 测试：先断言 `record_execution_log` 和 `list_execution_logs` 行为。
2. 后端 agent 测试：先断言工具成功、工具错误重试、LLM token usage 会写统一日志。
3. API 测试：先断言新增 execution logs 与 summary endpoint。
4. 前端 API 测试：先断言请求路径和响应解析。
5. 前端组件测试：先断言 summary、timeline、retry chain 和顶部 Audit 入口。
6. Skill 测试：先断言 `audit-sop` 被 loader 发现，frontmatter 合规。

## 风险与处理

- 不同 LLM provider 的 token usage 字段不一致：使用归一化函数，原始字段保存到 `raw`。
- 审计写入失败影响主流程：所有写入包装为 best-effort，失败只记录 logger。
- 日志量增长：接口 limit 默认为 500，数据库按 thread/time 建索引。归档和分页后续再做。
- 前端过度复杂：第一版只做 summary、筛选、时间线、展开详情和 retry chain。

## 成功标准

- 用户能从红框位置或右侧 Audit 入口进入审计页面。
- 当前 thread 的 agent 执行日志按时间线展示。
- 日志包含时间、工具调用、执行结果、错误重试、耗时和 token usage。
- 工具错误重试链能一眼看出每次失败原因和最终状态。
- `audit-sop` skill 可被 skills 列表发现，并符合 skill-creator 的目录与 metadata 规范。
- 后端与前端相关测试通过。
