# s15: Tracing（追踪与观测）

`[ s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 ] s15 > s16`

> *"如果你不能观测它，你就不能改进它"* —— 双轨观测体系：
> Langfuse 分布式追踪（AI 层） + 执行日志审计（Harness 层）。
>
> **Harness 层**: 观测 —— Agent 的"黑匣子"。

## 问题

前面 14 章构建的 Agent 是一个黑盒子：用户输入进去，回复出来。但如果回答质量差、
工具调用失败、审批流程卡住——你完全不知道内部发生了什么。

生产环境的 Agent 需要可观测性：
- **每个请求走了多少 token？** → 成本控制
- **哪个工具调用失败了？为什么？** → 故障诊断
- **技能路由选了什么？为什么？** → 路由调试
- **有没有安全事件？** → 安全审计
- **从用户提问到最终回复花了多久？** → 性能优化

## 解决方案

双轨观测体系：

```
用户请求
    │
    ├──→ Langfuse (AI 层追踪)
    │    • 记录每次 LLM 调用（token 用量、延迟）
    │    • 记录 Agent 决策（选了什么工具、为什么）
    │    • 可视化 Trace 树
    │    • 评分和反馈
    │
    └──→ Execution Logs (Harness 层审计)
         • 7 种事件类型
         • 7 种状态
         • 结构化 JSONB 存储到 PostgreSQL
         • 前端审计面板实时查询
```

## 工作原理

### 1. Execution Logs（执行日志）

7 种事件类型，覆盖 Agent 全生命周期：

| event_type | 记录什么 | status 取值 |
|---|---|---|
| `turn` | 每轮对话的开始/完成/失败 | started, completed, failed |
| `skill_route` | 技能路由决策 | started, completed |
| `llm` | LLM 调用（token 用量、延迟） | started, completed, failed |
| `tool` | 工具调用（参数、结果、错误） | started, completed, failed, blocked, retrying |
| `tool_retry` | 工具重试 | retrying, completed, failed |
| `approval` | 审批请求和用户决定 | requested, approved, denied |
| `security` | 安全检查事件 | blocked, passed |

每条日志的 JSONB 结构：

```json
{
  "thread_id": "abc123",
  "event_type": "tool",
  "status": "completed",
  "name": "bash",
  "duration_ms": 320,
  "input": {"command": "ls -la"},
  "output": "total 48\ndrwxr-xr-x ...",
  "token_usage": null,
  "error": null,
  "metadata": {"tool_call_id": "toolu_xxx"}
}
```

### 2. Audit Events（审计事件）

专门记录安全相关事件：

```python
class AuditEventCreate:
    thread_id: str          # 哪个会话
    source: str             # "prompt" | "tool"
    category: str           # instruction_override / fork_bomb / ...
    severity: str           # CRITICAL / HIGH / LOW
    reason: str             # 人类可读的原因
    subject: str            # 被拦截的内容摘要（截断 500 字符）
    metadata: dict          # 额外上下文
```

### 3. Langfuse 集成

Langfuse 是开源的 LLM 可观测性平台。langgraph-claw 通过 LangChain 的 CallbackHandler 自动集成：

```python
# tracing.py
from langfuse.callback import CallbackHandler

def build_langfuse_handler(settings):
    if not settings.langfuse_public_key:
        return None  # 未配置则跳过
    return CallbackHandler(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
```

Langfuse 自动捕获：
- 每次 LLM 调用的 token 用量和延迟
- Agent 决策链（Trace → Span → Generation）
- 工具调用和结果
- 可以手动添加 score 和 feedback

### 4. 前端审计面板

WorkspacePanel 提供三个审计视图：

- **执行日志列表**：按 event_type 过滤，查看每条日志的详情
- **执行摘要**：聚合统计（总轮数、工具调用数、失败数、审批数）
- **工具错误视图**：只显示失败的工具调用，带重试链
- **时间线视图**：按时间排列所有事件，一目了然

## 变更内容

| 组件 | 之前 | 之后 |
|------|------|------|
| 可观测性 | 无 | Langfuse 追踪 + 执行日志 + 审计事件 |
| 故障排查 | 只能看终端输出 | 结构化日志，前端面板实时查询 |
| 安全审计 | 无从追溯 | audit_events 表完整记录每次拦截 |
| 性能分析 | 无数据 | 每次调用的 token 用量和延迟 |

## 试一试

```sh
cd course
python s15_tracing/code.py
```

演示模式会：
1. 创建 ExecutionLogger
2. 通过 Hook 系统自动记录事件
3. 模拟一次完整的 Agent 交互
4. 打印事件摘要

## 源码参考

| 教学代码 | 项目源码 |
|---------|---------|
| `ExecutionLogger` | `memory/postgres.py` — `agent_execution_logs` 表 |
| `AuditEvent` | `api/schemas.py` — `AuditEventCreate` |
| Hook 集成 | `agent/harness.py` — `_record_*` 系列函数 |
| Langfuse | `tracing.py` — `build_langfuse_handler()` |

## 下一步

[s16: Comprehensive Agent](../s16_comprehensive/) —— 终点章：所有 15 个机制组装到一个完整的 AgentHarness。
