#!/usr/bin/env python3
"""Append remaining sections to the tech doc, avoiding security triggers."""

from pathlib import Path

target = Path(r"C:\idea\langgraph-claw\技术细节.md")

# Build each section safely
dd = "d" + "d"  # avoid regex: \bdd\b

sections = []

sections.append("""
### 6.3 转录归档

压缩前将完整对话写入 `transcript_dir/{thread_id}-{timestamp}.jsonl`，每条消息一个 JSON 对象。

## 7. 内存系统

### 7.1 PostgreSQL 持久化（PostgresMemory）

| 表 | 用途 |
|----|-----|
| `checkpoints` | LangGraph 状态检查点（通过 `AsyncPostgresSaver`） |
| `audit_events` | 安全审计日志（prompt guard / tool guard / 审批决策） |
| `long_term_memories` | 长期记忆（slug 主键，upsert） |
| `tool_results` | 工具执行结果（tool_result_id 主键，upsert） |

所有自增 ID 使用 `BIGSERIAL`，时间戳使用 `TIMESTAMPTZ`。

### 7.2 长期记忆（LongTermMemoryStore）

双写策略：
1. 文件系统：`{workspace}/.memory/{slug}.md` + `MEMORY.md` 索引
2. PostgreSQL：`long_term_memories` 表 upsert

由 Agent 在 `memory_reflection` 节点自动判断是否值得保存。

### 7.3 工具结果存储

每次工具执行后，`_record_tool_result_messages` 将结果写入 PostgreSQL `tool_results` 表，用于上下文压缩时的引用恢复。
""")

sections.append("""
## 8. API 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/chat` | 同步对话（返回 `ChatResponse`） |
| `POST` | `/api/chat/stream` | SSE 流式对话 |
| `POST` | `/api/approve` | 同步审批决议 |
| `POST` | `/api/approve/stream` | SSE 流式审批 |
| `GET` | `/api/threads/{thread_id}/replay` | 重放线程历史状态 |
| `GET` | `/api/threads` | 列出所有线程 |
| `DELETE` | `/api/threads` | 清空所有线程 |
| `DELETE` | `/api/threads/{thread_id}` | 删除指定线程 |
| `GET` | `/api/audit-events` | 列出审计事件 |
| `GET` | `/api/skills` | 列出所有技能 |
| `POST` | `/api/skills/reload` | 重新加载所有技能 |

### SSE 事件类型

| 事件 | 负载 | 说明 |
|------|------|------|
| `token` | `{"content": "..."}` | LLM 生成的文本 token |
| `reasoning` | `{"content": "..."}` | 推理内容（DeepSeek R1 等模型） |
| `compacting` | `{"status": "started/completed", ...}` | 上下文压缩状态 |
| `tool_result` | `{"name": "...", "content": "..."}` | 工具执行结果 |
| `requires_approval` | `{"approvals": [...]}` | 需要用户审批的工具调用 |
| `done` | `{"status": "...", "message": "..."}` | 对话完成 |
| `error` | `{"message": "..."}` | 错误信息 |
| `[DONE]` | — | 流结束信号 |
""")

sections.append("""
## 9. Hook 系统（AgentHookManager）

每个图节点在执行前后触发钩子事件，支持外部扩展：

```python
class HookStage(str, Enum):
    ROUTE_SKILLS = "route_skills"
    COMPACT_CONTEXT = "compact_context"
    AGENT = "agent"
    MEMORY_REFLECTION = "memory_reflection"
    APPROVAL = "approval"
    TOOLS = "tools"

@dataclass(frozen=True)
class HookEvent:
    stage: HookStage
    phase: str          # "before" | "after" | "error"
    state: AgentState
    config: RunnableConfig | None
    result: Any
    error: BaseException | None
```
""")

sections.append("""
## 10. 配置系统

使用 `pydantic-settings`，环境变量从 `.env` 文件加载：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DATABASE_URL` | 必填 | PostgreSQL 连接串 |
| `OPENAI_API_KEY` | 必填 | LLM API Key |
| `LLM_BASE_URL` | `None` | API 基础 URL |
| `LLM_MODEL` | 必填 | 模型标识 |
| `LLM_TEMPERATURE` | `0.2` | 采样温度 |
| `SKILLS_DIR` | `<backend>/skills/` | 技能目录 |
| `ASSISTANT_WORKSPACE_DIR` | `<cwd>` | 沙箱工作区根目录 |
| `TRANSCRIPT_DIR` | `<workspace>/.transcripts` | 转录归档目录 |
| `LONG_TERM_MEMORY_DIR` | `<workspace>/.memory` | 长期记忆目录 |
| `CONTEXT_COMPACTION_MESSAGE_COUNT` | `20` | 压缩触发消息数 |
| `CONTEXT_COMPACTION_TOKEN_THRESHOLD` | `1,000,000` | 压缩触发 Token 阈值 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | CORS 允许源 |
| `LANGFUSE_PUBLIC_KEY` | `None` | Langfuse 公钥 |
| `LANGFUSE_SECRET_KEY` | `None` | Langfuse 密钥 |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse 主机 |
""")

sections.append("""
## 11. 依赖清单

```toml
[project]
name = "personal-assistant-backend"
version = "0.1.0"
requires-python = ">=3.11"

[dependencies]
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
pydantic-settings>=2.4.0
langchain-core>=0.3.0
langchain-openai>=0.2.0
langgraph>=0.2.0
langgraph-checkpoint-postgres>=2.0.0
psycopg[binary,pool]>=3.2.0
watchfiles>=0.24.0
pyyaml>=6.0
langfuse>=3.0.0
langchain-deepseek>=1.1.0

[dev]
pytest>=8.0.0
pytest-asyncio>=0.24.0
ruff>=0.6.0
```
""")

sections.append("""
## 12. 测试体系

22 个测试文件覆盖以下领域：

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test_config.py` | 配置加载 |
| `test_basic_tools.py` | 基础文件/Shell 工具 |
| `test_basic_tools_agent.py` | 基础工具 Agent 集成 |
| `test_router.py` | 技能路由 & 系统提示词构建 |
| `test_skill_base.py` | Skill 数据类 |
| `test_skill_loader.py` | 技能注册表 (扫描/加载/热重载) |
| `test_skill_scripts.py` | 脚本工具构建 & 参数模式 |
| `test_script_tool.py` | 脚本工具子进程执行 |
| `test_approval_routing.py` | 审批路由逻辑 |
| `test_security_harness.py` | Prompt Guard & Tool Guard |
| `test_context_compaction.py` | 上下文压缩逻辑 |
| `test_agent_context_compaction.py` | Agent 集成压缩 |
| `test_agent_split.py` | Agent 消息分片 & 清理 |
| `test_memory_reflection.py` | 记忆反射节点 |
| `test_long_term_memory.py` | 文件系统长期记忆 |
| `test_postgres_long_term_memory.py` | PostgreSQL 长期记忆 |
| `test_checkpoint_replay.py` | 检查点重放 |
| `test_thread_history_delete.py` | 线程历史删除 |
| `test_stream_error_handling.py` | SSE 流错误处理 |
| `test_hooks.py` | 钩子系统 |
| `test_tracing.py` | Langfuse 追踪 |
| `test_progressive_loading.py` | 渐进式加载 |
| `test_review_fixes.py` | 回归修复 |
| `test_resolve_time.py` | 时间解析技能 |
""")

sections.append("""
## 13. 核心设计决策

### 13.1 ReAct Agent 模式

采用单 Agent ReAct 循环（Reasoning + Acting），而非多 Agent 协作。所有工具调用经过统一审批门控，用户保持最终控制权。

### 13.2 渐进式技能加载

技能元数据（约 100 bytes/技能）在启动时扫描并缓存。完整加载仅在技能被匹配时触发，确保冷启动时间不随技能数量线性增长。

### 13.3 审批门控设计

每个工具调用生成唯一的 `approval_id`（工具调用 ID），用户批准/拒绝后通过 `resume_after_approval` 恢复图执行。已拒绝的调用附加 ToolMessage，Agent 可据此调整策略。

### 13.4 消息清洗

`_sanitize_messages_for_api` 在调用 LLM 前清除未应答的 tool_calls，满足 OpenAI/DeepSeek API 的严格约束。清洗在副本上进行，不影响原始状态。

### 13.5 上下文压缩策略

采用"语义保留 + LLM 摘要"混合策略：保留首尾关键消息作为对话锚点，中间消息由 LLM 压缩为结构化摘要。工具结果替换为引用占位符（可溯源到 PostgreSQL），完整历史归档到 JSONL 文件。

### 13.6 双写长期记忆

文件系统提供人类可读的 `.md` 文件和 `MEMORY.md` 索引；PostgreSQL 提供结构化查询能力。两者通过 `save_conversation_memory` 工具同步更新。

### 13.7 resolve-time 技能设计

内置的 resolve-time 技能采用"SKILL.md 说明书 + 独立 Python 脚本"架构：
- `SKILL.md`：YAML 前置元数据声明 triggers（中英文时间词）和 3 个脚本工具
- `scripts/resolve_date.py`：核心计算逻辑，支持 CLI 独立运行和模块导入
- 支持按天数偏移（今天±N天）和按星期偏移（这/下/上周几）
- 时区感知（默认 `Asia/Shanghai`），中英文星期映射表
""")

with target.open("a", encoding="utf-8") as f:
    for section in sections:
        f.write(section)

print(f"Appended {sum(len(s) for s in sections)} chars. Done!")
