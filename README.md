# huamulan-agent

> **单 Agent 花木兰工作台：以 LangGraph ReAct 循环为骨架，配套人工审批、审计追踪、热插拔 Skill 与 superharness 工程纪律。**

`huamulan-agent` 是一个面向单人 Agent 的 LangGraph 工程：木兰不是多智能体团队，而是一个人完成侦察、整备、执行、复盘的完整链路。前端对应花木兰的人设与战备意象，后端保持原有的安全 ReAct Agent、工具调用审批、渐进式 Skill 系统、长短期记忆与上下文压缩能力。

> “东市买骏马，西市买鞍鞯，南市买辔头，北市买长鞭。” 本项目用这条战备线索组织产品隐喻：任务先整备，再出征，最后校阅。

> 详细技术方案见 [技术方案报告.md](./技术方案报告.md)

## 功能特性

### Agent 引擎
- **ReAct Agent**：LangGraph StateGraph 驱动的推理-行动循环，含路由、上下文压缩、推理、审批和工具执行节点；长期记忆反思在主回复完成后后台执行
- **流式响应**：SSE 事件流（token / reasoning / approval / tool_result / done）
- **推理展示**：DeepSeek thinking 推理过程提取并展示，支持展开/折叠
- **可配置 LLM**：通过 `LLM_CONFIG` 覆盖 `base_url`、`model`、`api_key`、`temperature`

### 记忆与上下文
- **长期记忆**：工作区 `.memory/` 维护 `USER.md`、`SYSTEM.md`、`MEMORY.md`，其中 `MEMORY.md` 按“一行一个链接”索引沉淀条目
- **用户确认沉淀**：主回复完成后由后台 LLM 静默判断是否值得保存；若需要沉淀，前端在右上角弹出非阻塞确认通知，只有用户审批 `save_conversation_memory` 后才写入 Markdown 与 PostgreSQL
- **短期记忆**：继续使用 LangGraph checkpoint 保存线程内消息、审批状态和中间状态
- **上下文压缩**：上下文阈值为 1M token，超过 90% 或对话超过 20 轮时触发，用户 Approve/Deny 审批点击也计入轮次；保留用户第一条输入、Agent 第一条和最后一条输出，中间替换为摘要；工具结果用 `[tool result can find by tool_result_id: ...]` 引用，并可从 PostgreSQL 反查
- **Redis 缓存**：可选加速层，缓存线程列表、执行日志/摘要、审计/工具错误查询和长期记忆拼接结果；PostgreSQL 仍是权威存储，Redis 不可用时自动退回直读

### 安全体系
- **Prompt Guard**：4 类注入/越狱检测（指令覆盖、系统提示泄露、DAN 越狱、身份伪造）
- **Tool Guard**：10 类危险命令检测（磁盘格式化、Fork 炸弹、反弹 Shell、提权等）
- **文件写入授权**：`write_file` 写入/追加必须经用户审批；审批通过后允许正常落盘，仍受工作区路径边界保护
- **调用中间件**：频率限制（50 次/工具/轮）/ 总量限制（20 次/轮）/ 循环检测（15 次相同参数）
- **审计日志**：所有安全事件持久化到 PostgreSQL，前端 Audit 面板可查询
- **执行日志追踪**：完整 Agent 执行链路记录（turn / LLM / tool / retry / approval / security），含 token 用量、耗时、输入输出、错误信息等结构化数据，支持按事件类型筛选与重试链可视化
- **执行摘要看板**：聚合展示 Token 消耗（Prompt/Completion）、工具调用次数、错误与重试统计、总耗时等关键指标
- **审计 SOP Skill**：内置 `audit-sop` 技能，Agent 可按标准流程分析执行日志并生成审计报告
- **Langfuse 可观测性**：opt-in 集成 Langfuse，自动追踪 LLM 调用、工具执行和图节点转移；支持云服务与自托管实例，自托管自动绕过 HTTP 代理；`thread_id` → `langfuse_session_id` 映射，可在 Langfuse UI 按会话过滤

### Skill 系统
- **渐进加载**：Phase 1 扫描元数据（无需导入），Phase 2 匹配到用时才加载
- **声明式脚本工具**：在 `SKILL.md` frontmatter 中声明命令和参数，自动生成 LangChain Tool
- **触发词路由**：根据用户输入匹配 Skill triggers，只暴露相关工具给 Agent
- **三层路由漏斗 + 可选 rerank**：先用当前 Skill 的显式正则/触发词做确定性匹配；未命中时可选启用 Ollama `bge-m3` embedding + 向量相似度召回；召回后可用本地 Ollama `qllama/bge-reranker-v2-m3` 重排；低于阈值时再交给 LLM 结构化判断
- **向量索引预热**：启用语义路由后，服务启动时会预热 Skill embedding；Qdrant 模式会按 `source_hash` 跳过未变化的 Skill，避免每次对话重复生成 embedding
- **热插拔**：`watchfiles` 监控 Skill 目录，`SKILL.md` 变化自动重载
- **示例 Skill**：`resolve-time`（中英文日期时间解析，含 3 个脚本工具）

### 基础工具
- `shell_command` — 在沙箱工作区内执行 Shell 命令
- `read_file` / `write_file` — 工作区文件读写
- `list_directory` / `search_files` — 目录浏览和内容搜索

### 审批与回放
- **工具审批门**：Agent 的所有工具调用需用户 Approve/Deny 后才执行；文件写入和更新在用户批准后继续执行
- **线程管理**：列出/删除/清空会话线程
- **Checkpoint 回放**：完整的 LangGraph 状态检查点历史，可回放到任意节点
- **Hook 扩展**：Agent 生命周期 Hook（route_skills/compact_context/agent/memory_reflection/approval/tools 的 before/after/error 阶段）

## 扩展项方案（仅方案）

以下扩展项目前只作为产品与工程方案记录，不在本次改造中新增后端 Skill、插件、API 或数据表。

| 木兰战备隐喻 | 扩展方向 | 方案说明 |
|-------------|----------|----------|
| 东市买骏马 | 任务入阵 | 为用户输入建立“目标、约束、交付物、验证命令”四段式任务整备卡，帮助单 Agent 开始前确认战场。 |
| 西市买鞍鞯 | 上下文装具 | 将仓库文件、历史会话、长期记忆、外部资料打包为可审计的上下文装备单，记录每次装配来源。 |
| 南市买辔头 | 执行缰绳 | 将审批策略、危险命令拦截、工具调用限流和人工确认组织为一套可切换的“缰绳等级”。 |
| 北市买长鞭 | 校阅追击 | 把测试结果、构建日志、审计事件和 checkpoint replay 汇总成战后校阅报告，便于复盘与交接。 |

后续若要真正实现这些扩展，建议按 superharness 流程拆成独立 spec/plan，并保持严格 TDD：每个扩展先有失败测试，再实现最小后端或前端能力。

## 技术栈

| 层 | 技术 |
|----|------|
| **前端** | React 19, TypeScript 6, Vite 8, Vitest 4 |
| **后端** | FastAPI, Uvicorn, Python 3.11 |
| **Agent** | LangGraph ≥0.2, langchain-deepseek (ChatDeepSeek) |
| **存储** | PostgreSQL (langgraph-checkpoint-postgres + 审计日志 + 长期记忆 + 工具结果) + Qdrant (可选；Skill 向量索引) |
| **缓存** | Redis (可选；读接口与长期记忆热数据加速) |
| **可观测性** | Langfuse ≥3.0 (LLM trace + 工具 span + 图节点自动追踪，支持云服务/自托管) |
| **工程** | Superharness (TDD + 系统调试 + 代码审查) |

## 架构概览

```mermaid
flowchart LR
    subgraph Frontend["Frontend — React 19"]
        CP[ChatPanel<br/>MessageList + MessageInput<br/>ToolApprovalCard]
        WP[WorkspacePanel<br/>Thread Replay<br/>Operational Audit]
        SB[Sidebar<br/>Skills / History<br/>Checkpoint / Audit]
    end

    subgraph Backend["Backend — FastAPI"]
        AH[AgentHarness<br/>Prompt Guard + Tool Guard<br/>+ Middlewares + Execution Log]
        SG[LangGraph StateGraph<br/>route → compact → LLM<br/>→ approve → tools<br/>background memory reflection]
        SK[SkillRegistry<br/>渐进加载 + 热插拔<br/>含 audit-sop Skill]
        LT[LongTermMemoryStore<br/>.memory/USER.md + SYSTEM.md + MEMORY.md]
        CT[ContextCompactor<br/>.transcripts/*.jsonl + LLM summary]
        CA[Cache<br/>RedisCache / NoopCache]
    end

    subgraph DB["PostgreSQL"]
        CK[checkpoints]
        CW[checkpoint_writes]
        CB[checkpoint_blobs]
        AE[audit_events]
        EL[agent_execution_logs]
        TE[tool_errors]
        LM[long_term_memories]
        TR[tool_results]
    end

    subgraph Redis["Redis (Optional Cache)"]
        RL[thread list / execution logs / summaries]
        RM[long-term memory prompt cache]
    end

    subgraph Qdrant["Qdrant (Optional Vector Store)"]
        QV[Skill Embedding Index]
    end

    Frontend -->|"HTTP REST + SSE"| Backend
    Backend --> DB
    Backend -.-> Redis
    Backend -.-> Qdrant
    Backend -->|"CallbackHandler<br/>自动 trace"| LF["Langfuse<br/>(Cloud / Self-Hosted)"]
    AH --> CA
    CA -.-> Redis
    SG --> LT
    SG --> CT
    SK -.-> Qdrant
```

## 快速开始

### 前置条件

- Python ≥3.11
- Node.js ≥18
- PostgreSQL（默认连接见下方）
- Redis（可选；默认不配置时禁用缓存）

### 后端

```powershell
cd backend
cp .env.example .env                # 复制并编辑 .env，填入实际配置
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
uvicorn personal_assistant.api.server:app --reload --host 0.0.0.0 --port 8000
```

### 前端

```powershell
cd frontend
npm install
npm run dev                          # http://localhost:5173，API 代理 → localhost:8000
```

### 数据库

数据库连接通过 `DATABASE_URL` 环境变量配置，格式如下：
```
postgresql://user:password@host:5432/dbname?sslmode=disable
```

### Redis 缓存（可选）

Redis 通过 `REDIS_URL` 启用，只作为可丢失的加速层：

```ini
CACHE_ENABLED=true
REDIS_URL=redis://redis.example.local:6379/0
```

缓存内容包括 `/api/threads`、执行日志/摘要、审计/工具错误查询结果，以及 `.memory/*.md` 拼接后的长期记忆提示片段。所有写入仍先落 PostgreSQL 或文件系统，写入后主动失效相关 Redis key；Redis 连接失败时自动使用 `NoopCache`，业务功能不受影响。

### 执行日志 Schema

Agent 运行期间自动记录结构化执行日志到 `agent_execution_logs` 表：

| 列 | 类型 | 说明 |
|----|------|------|
| `thread_id` | `TEXT` | 会话线程 ID |
| `run_id` | `TEXT` | 运行 ID（可空） |
| `parent_id` | `TEXT` | 父事件 ID（可空） |
| `event_type` | `TEXT` | 事件类型（见下表） |
| `status` | `TEXT` | 事件状态（见下表） |
| `name` | `TEXT` | 事件名称（工具名 / 安全类别 / 审批 ID） |
| `input` | `JSONB` | 输入数据（消息、工具参数等） |
| `output` | `JSONB` | 输出数据（LLM 文本、工具结果等） |
| `error` | `JSONB` | 错误信息（`type` + `message`） |
| `duration_ms` | `INT` | 耗时（毫秒） |
| `token_usage` | `JSONB` | Token 用量（`prompt_tokens` / `completion_tokens` / `total_tokens`） |
| `metadata` | `JSONB` | 扩展元数据（`tool_call_id` / `attempt` / `severity` 等） |

**事件类型** (`event_type`)：

| 类型 | 说明 |
|------|------|
| `turn` | 用户会话轮次（开始/完成/失败） |
| `skill_route` | Skill 触发词路由 |
| `llm` | LLM 调用（含 token 用量） |
| `tool` | 工具执行（含输入参数和输出结果） |
| `tool_retry` | 工具重试（含失败原因和重试次数） |
| `approval` | 工具审批操作（请求/同意/拒绝） |
| `security` | 安全事件（Prompt Guard / Tool Guard 拦截） |

**事件状态** (`status`)：

| 状态 | 适用场景 |
|------|----------|
| `started` | turn / approval 请求 |
| `completed` | 成功完成（turn / llm / tool） |
| `failed` | 执行失败（turn / tool 耗尽重试） |
| `blocked` | 安全拦截（security） |
| `retrying` | 工具重试中（tool_retry） |
| `approved` / `denied` | 审批结果（approval） |

### 执行日志 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/threads/{thread_id}/execution-logs?limit=500` | 按线程查询执行日志（时间升序） |
| `GET` | `/api/threads/{thread_id}/execution-summary` | 查询执行摘要（聚合统计） |

执行摘要 (`ExecutionSummary`) 包含 `total_events`、`total_tokens`、`prompt_tokens`、`completion_tokens`、`tool_calls`、`tool_errors`、`tool_retries`、`security_events`、`total_duration_ms`。

前端 Operational Audit 面板支持：
- 摘要指标卡片（Token、工具调用、错误、重试、耗时）
- 按事件类型筛选（All / LLM / Tool / Tool Retry / Security / Approval）
- 重试链可视化（按 `tool_call_id` 聚合，展示每次尝试结果）
- 事件时间线（可展开查看 Input / Output / Error / Metadata）

### Langfuse 可观测性（可选）

Langfuse 集成是 **opt-in** 的——仅在配置 `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` 时启用。

**启用方式**——在 `backend/.env` 中设置：

```ini
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com    # 默认值，自托管时替换为你的实例地址
```

**自动追踪内容**（通过 LangChain CallbackHandler 自动 hook，无需手动插桩）：

| 追踪对象 | 追踪内容 |
|----------|----------|
| LLM 调用 | 每次 LLM 推理，包含 prompt/completion tokens、模型名、耗时、输入输出 |
| 工具执行 | 每次工具调用，包含工具名、参数、返回值、耗时、错误信息 |
| 图节点转移 | LangGraph StateGraph 的节点进入/退出，含 metadata（langfuse_session_id） |
| Session | `thread_id` → `langfuse_session_id`，在 Langfuse UI 可按会话过滤所有 trace |

**自托管注意事项**：
- 自托管 Langfuse 实例通常在局域网内，本地 HTTP 代理（Clash / V2Ray 等）无法访问
- `tracing.py` 自动将自托管主机名加入 `NO_PROXY` 环境变量，避免 OTEL span 被代理拦截
- 仅对非 `cloud.langfuse.com` 的主机名生效

### 审计 SOP Skill

内置 `audit-sop` Skill，定义 Agent 分析执行日志的标准操作流程：

1. 确认 `thread_id`
2. 查阅执行摘要（事件总数、Token、工具调用、错误、重试、安全事件、耗时）
3. 按时间线检查事件序列
4. 分析 Token 用量（识别异常大的 LLM 调用）
5. 诊断工具重试链（按 `tool_call_id` 分组，逐次分析失败原因）
6. 检查审批与安全事件（被请求/同意/拒绝/拦截的内容及原因）
7. 生成结构化审计报告（Summary → Evidence → Token Usage → Tool Retry Analysis → Security And Approval Events → Recommendations）

## 环境变量

项目根目录下有 `.env.example` 文件（[backend](backend/.env.example) / [frontend](frontend/.env.example)），
复制为 `.env` 后按需修改即可使用。

### 后端

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | 必填，无默认值 | PostgreSQL 连接串（checkpoint + 审计日志） |
| `OPENAI_API_KEY` | 必填，无默认值 | API 密钥（兼容 OpenAI/DeepSeek） |
| `LLM_BASE_URL` | 必填，无默认值 | LLM API 地址（如 `https://api.deepseek.com`） |
| `LLM_MODEL` | 必填，无默认值 | 模型名称（如 `deepseek-v4-pro`） |
| `LLM_TEMPERATURE` | `0.2` | 生成温度（0.0–2.0） |
| `SKILLS_DIR` | `<backend>/skills/` | Skill 定义目录 |
| `ASSISTANT_WORKSPACE_DIR` | 当前工作目录 | 工具沙箱根目录 |
| `LONG_TERM_MEMORY_DIR` | `<workspace>/.memory` | 长期记忆 Markdown 文件目录 |
| `TRANSCRIPT_DIR` | `<workspace>/.transcripts` | 上下文压缩前完整 transcript JSONL 存储目录 |
| `CONTEXT_COMPACTION_MESSAGE_COUNT` | `20` | 触发上下文压缩的用户对话轮数，含 Approve/Deny 审批点击 |
| `CONTEXT_COMPACTION_TOKEN_THRESHOLD` | `1000000` | 上下文 token 阈值；超过 90% 时触发压缩 |
| `CACHE_ENABLED` | `true` | 是否启用可选缓存层；无 `REDIS_URL` 时自动退回 NoopCache |
| `REDIS_URL` | 可选，无默认值 | Redis 连接串，必须使用 `redis://` 或 `rediss://`，如 `redis://redis.example.local:6379/0` |
| `CACHE_DEFAULT_TTL_SECONDS` | `10` | 线程列表、执行摘要、审计/工具错误等普通缓存 TTL |
| `CACHE_LOG_TTL_SECONDS` | `5` | 执行日志列表缓存 TTL |
| `CACHE_MEMORY_TTL_SECONDS` | `60` | 长期记忆拼接提示缓存 TTL |
| `SKILL_ROUTING_SEMANTIC_ENABLED` | `false` | 是否启用 regex → embedding 召回 → 可选 rerank → LLM 判断的 Skill 路由漏斗；关闭时仅使用确定性正则/触发词路由 |
| `SKILL_ROUTING_EMBEDDING_MODEL` | `bge-m3` | Ollama embedding 模型名 |
| `SKILL_ROUTING_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址；局域网部署时填写提供 `bge-m3` / reranker 的机器地址 |
| `SKILL_ROUTING_VECTOR_STORE` | `memory` | Skill embedding 存储后端：`memory` 或 `qdrant` |
| `SKILL_ROUTING_QDRANT_URL` | 可选，无默认值 | Qdrant HTTP 地址，例如 `http://<qdrant-host>:6333` |
| `SKILL_ROUTING_QDRANT_API_KEY` | 可选，无默认值 | Qdrant API key；后端通过 `api-key` header 发送 |
| `SKILL_ROUTING_QDRANT_COLLECTION` | `skill_routes` | Qdrant collection 名称，需提前创建并匹配 embedding 维度 |
| `SKILL_ROUTING_SIMILARITY_THRESHOLD` | `0.72` | 语义召回直接命中的相似度阈值 |
| `SKILL_ROUTING_TOP_K` | `3` | 语义召回候选数量，未启用 rerank 或 rerank 失败时低于阈值会作为 LLM judge 的 `relatedFind` |
| `SKILL_ROUTING_RERANK_ENABLED` | `false` | 是否在语义召回后启用本地 reranker 重排 |
| `SKILL_ROUTING_RERANK_MODEL` | `qllama/bge-reranker-v2-m3` | Ollama reranker 模型名；当前适配器要求该模型在 `/api/tags` 中声明 `embedding` capability，并通过 `/api/embed` 对 query/passage pair 打分 |
| `SKILL_ROUTING_RERANK_THRESHOLD` | `0.72` | rerank 后 top 候选直接命中的阈值 |
| `SKILL_ROUTING_RERANK_TOP_K` | `3` | 送入 reranker 的候选数量；应小于等于 `SKILL_ROUTING_TOP_K` |
| `SKILL_ROUTING_LLM_RETRY_COUNT` | `1` | LLM 路由结构化输出校验失败后的重试次数 |
| `SKILL_ROUTING_LLM_MODEL` | 可选，无默认值 | 第三层 LLM judge 专用模型；不填则沿用 `LLM_MODEL`，例如可填 `deepseek-v4-flash` |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 允许跨域的浏览器来源（JSON 数组） |
| `LANGFUSE_PUBLIC_KEY` | 可选，无默认值 | Langfuse 公钥（不填则禁用追踪） |
| `LANGFUSE_SECRET_KEY` | 可选，无默认值 | Langfuse 密钥 |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Langfuse 实例地址（支持自托管） |

### 前端（Vite）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_API_TARGET` | `http://localhost:8000` | 开发服务器 API 代理目标 |

## Skill Routing Funnel

`route_skills` 使用三层漏斗选择本轮需要加载的 Skill：

1. **Regex / triggers**：先匹配当前内置 Skill 的显式正则、`SKILL.md` frontmatter 中的 `triggers`，以及无 triggers Skill 的保守 name/description token。命中后立即短路，不调用 embedding 或 LLM。
2. **Semantic retrieval**：当第一层未命中且 `SKILL_ROUTING_SEMANTIC_ENABLED=true` 时，使用 Ollama `bge-m3` 为用户 query 生成 embedding，并从内存索引或 Qdrant 中召回 top-K Skill。
3. **Optional rerank**：当 `SKILL_ROUTING_RERANK_ENABLED=true` 时，把召回候选按 query/passage pair 交给本地 Ollama reranker 重排，并用 `SKILL_ROUTING_RERANK_THRESHOLD` 判断是否直接命中；当前适配器会先检查模型是否声明 `embedding` capability，避免把不支持 `/api/embed` 的模型打到 500；rerank 请求失败时保留原 semantic 候选继续降级。
4. **LLM judge**：当召回或 rerank 分数低于对应阈值但存在候选时，将 `{"userInput": "...", "relatedFind": [...]}` 交给 LLM，并使用本地 JSON schema 校验；结构不合法会带错误信息重试。该层可用 `SKILL_ROUTING_LLM_MODEL` 单独指定模型，例如 `deepseek-v4-flash`，不填则沿用主 `LLM_MODEL`。

漏斗最终选中的 Skill 才会进入本轮 System Prompt 与工具过滤范围；未选中任何 Skill 时不会注入 Skill 元数据，避免 Skill 数量变多或描述相近时让模型再次自行猜测。

Skill embedding 不会在每次对话重复生成。服务启动时会尝试预热索引；Qdrant 模式会先读取已有 point payload 的 `skill_name/source_hash`，只有新增或 `SKILL.md` 变化的 Skill 才重新生成 embedding 并 upsert。预热和写入过程会打印 INFO 日志，包含 collection、生成 embedding 的 skill、upsert 数量和跳过数量。用户请求时仍保留懒同步兜底，避免启动预热失败或热插拔后漏同步。

Qdrant 写入成功后可以用以下命令验证：

```bash
curl -X POST "http://<qdrant-host>:6333/collections/skill_routes/points/scroll" \
  -H "api-key: <your-qdrant-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"limit": 10, "with_payload": true, "with_vector": false}'
```

返回 payload 中应包含 `skill_name`、`description`、`source_hash`。

## Skill 开发

每个 Skill 是一个目录，包含 `SKILL.md` 和可选的脚本文件：

```markdown
---
name: my-skill
description: 技能描述
triggers:
  - 关键词1
  - keyword2
scripts:
  - name: my_tool
    description: 工具描述
    command: ["python", "scripts/my_script.py", "{param}"]
    params:
      param:
        type: string
        description: 参数说明
        required: true
---

# Skill 标题

Agent 行为指令...
```

也支持通过 `skill.py` 暴露 LangChain 工具：

```python
from langchain_core.tools import tool

@tool
def my_tool(arg: str) -> str:
    return arg

TOOLS = [my_tool]
```

新增、删除、修改 Skill 后：
- **自动**：`watchfiles` 后台监控，`SKILL.md` 变化自动重新扫描
- **手动**：调用 `POST /api/skills/reload` 或点击前端 Sidebar → Skills → Reload

## 运行测试

```powershell
# Backend
cd backend
uv run pytest -v

# Frontend
cd frontend
npm test
```

## 项目结构

```
backend/src/personal_assistant/
├── agent/        # Agent 引擎（图编译、安全、Hook、LLM、路由、审批、执行日志记录）
├── api/          # FastAPI 服务器 + 数据模型 + 执行日志/摘要 API
├── cache/        # RedisCache / NoopCache 可选缓存层
├── memory/       # PostgreSQL Checkpoint + 缓存包装 + 审计日志 + 执行日志 + 工具错误 + 长期记忆 + 上下文压缩
├── skills/       # Skill 系统（渐进加载、脚本工具、热插拔）
│   ├── resolve-time/  # 内置日期时间解析 Skill
│   └── audit-sop/     # 内置审计 SOP Skill（执行日志分析）
├── tools/        # 基础工具（Shell/文件操作/长期记忆保存）
└── tracing.py    # Langfuse 可观测性集成（opt-in CallbackHandler 注入）

frontend/src/
├── components/   # React 组件（含 WorkspacePanel 审计面板 + 重试链可视化）
├── hooks/        # useChat 状态机
├── lib/          # 类型化 API 客户端（含执行日志/摘要 API）+ SSE 流解析
└── test/         # 测试配置
```

## 开发规范

项目使用 **superharness** 工程纪律框架：严格 TDD（先测试后代码）、系统调试、代码审查。
详见 `CLAUDE.md` 和 `.claude/superharness/`。

---

🤖 技术方案详见 [技术方案报告.md](./技术方案报告.md)
## Redis-first Checkpoint 存储补充

配置 `REDIS_URL` 后，LangGraph checkpoint 的热路径改为先同步写入 Redis，再异步归档到 PostgreSQL。Redis 作为近期 checkpoint 的优先读源；如果 Redis miss、过期或被 LRU 淘汰，回放会回退到 PostgreSQL。Redis 写入失败时会同步写 PostgreSQL，避免丢失线程状态。

checkpoint payload 使用 MessagePack-oriented `JsonPlusSerializer` 并对较大字节流做 zlib 压缩。`CHECKPOINT_TTL_SECONDS` 同时作用于 Redis key TTL 和 PostgreSQL checkpoint 清理；启动时会 best-effort 配置 Redis `maxmemory-policy`，默认 `allkeys-lru`。默认跳过确定性写入节点 `route_skills,compact_context`，保留 `agent`、`tools`、`approval`、`memory_reflection` 等会产生外部交互或关键状态变化的节点；日志中的 `source=input/loop` 是 LangGraph checkpoint 来源，不参与 `CHECKPOINT_SKIP_NODES` 判断，真实图写入节点会以 `write_node` 打印。

```ini
CHECKPOINT_TTL_SECONDS=604800
CHECKPOINT_PG_CLEANUP_ENABLED=true
CHECKPOINT_PG_CLEANUP_INTERVAL_SECONDS=3600
CHECKPOINT_REDIS_LRU_ENABLED=true
CHECKPOINT_REDIS_MAXMEMORY_POLICY=allkeys-lru
CHECKPOINT_SKIP_NODES=route_skills,compact_context
```
