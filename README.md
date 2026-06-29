# LangGraph Personal Assistant

> **Secure LangGraph agent with ReAct loop, human-in-the-loop tool approval, auditable execution, and a hot-pluggable skill system.**

基于 **LangGraph** 的个人助理 Agent 原型系统：React 19 前端 + FastAPI/LangGraph 后端，
单 ReAct Agent + 工具调用审批 + 渐进式 Skill 系统 + 长短期记忆与上下文压缩。

> 详细技术方案见 [技术方案报告.md](./技术方案报告.md)

## 功能特性

### Agent 引擎
- **ReAct Agent**：LangGraph StateGraph 驱动的推理-行动循环，含路由、上下文压缩、推理、记忆反思、审批和工具执行节点
- **流式响应**：SSE 事件流（token / reasoning / approval / tool_result / done）
- **推理展示**：DeepSeek thinking 推理过程提取并展示，支持展开/折叠
- **可配置 LLM**：通过 `LLM_CONFIG` 覆盖 `base_url`、`model`、`api_key`、`temperature`

### 记忆与上下文
- **长期记忆**：工作区 `.memory/` 维护 `USER.md`、`SYSTEM.md`、`MEMORY.md`，其中 `MEMORY.md` 按“一行一个链接”索引沉淀条目
- **用户确认沉淀**：会话末尾由 LLM 反思节点判断是否值得保存，只有用户审批 `save_conversation_memory` 后才写入 Markdown 与 PostgreSQL
- **短期记忆**：继续使用 LangGraph checkpoint 保存线程内消息、审批状态和中间状态
- **上下文压缩**：上下文阈值为 1M token，超过 90% 或对话超过 20 轮时触发，用户 Approve/Deny 审批点击也计入轮次；保留用户第一条输入、Agent 第一条和最后一条输出，中间替换为摘要；工具结果用 `[tool result can find by tool_result_id: ...]` 引用，并可从 PostgreSQL 反查

### 安全体系
- **Prompt Guard**：4 类注入/越狱检测（指令覆盖、系统提示泄露、DAN 越狱、身份伪造）
- **Tool Guard**：10 类危险命令检测（磁盘格式化、Fork 炸弹、反弹 Shell、提权等）
- **调用中间件**：频率限制（50 次/工具/轮）/ 总量限制（20 次/轮）/ 循环检测（15 次相同参数）
- **审计日志**：所有安全事件持久化到 PostgreSQL，前端 Audit 面板可查询

### Skill 系统
- **渐进加载**：Phase 1 扫描元数据（无需导入），Phase 2 匹配到用时才加载
- **声明式脚本工具**：在 `SKILL.md` frontmatter 中声明命令和参数，自动生成 LangChain Tool
- **触发词路由**：根据用户输入匹配 Skill triggers，只暴露相关工具给 Agent
- **热插拔**：`watchfiles` 监控 Skill 目录，`SKILL.md` 变化自动重载
- **示例 Skill**：`resolve-time`（中英文日期时间解析，含 3 个脚本工具）

### 基础工具
- `shell_command` — 在沙箱工作区内执行 Shell 命令
- `read_file` / `write_file` — 工作区文件读写
- `list_directory` / `search_files` — 目录浏览和内容搜索

### 审批与回放
- **工具审批门**：Agent 的所有工具调用需用户 Approve/Deny 后才执行
- **线程管理**：列出/删除/清空会话线程
- **Checkpoint 回放**：完整的 LangGraph 状态检查点历史，可回放到任意节点
- **Hook 扩展**：Agent 生命周期 Hook（route_skills/compact_context/agent/memory_reflection/approval/tools 的 before/after/error 阶段）

## 技术栈

| 层 | 技术 |
|----|------|
| **前端** | React 19, TypeScript 6, Vite 8, Vitest 4 |
| **后端** | FastAPI, Uvicorn, Python 3.11 |
| **Agent** | LangGraph ≥0.2, langchain-deepseek (ChatDeepSeek) |
| **存储** | PostgreSQL (langgraph-checkpoint-postgres + 审计日志 + 长期记忆 + 工具结果) |
| **工程** | Superharness (TDD + 系统调试 + 代码审查) |

## 架构概览

```mermaid
flowchart LR
    subgraph Frontend["Frontend — React 19"]
        CP[ChatPanel<br/>MessageList + MessageInput<br/>ToolApprovalCard]
        SB[Sidebar<br/>Skills / History<br/>Checkpoint / Audit]
    end

    subgraph Backend["Backend — FastAPI"]
        AH[AgentHarness<br/>Prompt Guard + Tool Guard<br/>+ Middlewares]
        SG[LangGraph StateGraph<br/>route → compact → LLM → memory reflection<br/>→ approve → tools]
        SK[SkillRegistry<br/>渐进加载 + 热插拔]
        LT[LongTermMemoryStore<br/>.memory/USER.md + SYSTEM.md + MEMORY.md]
        CT[ContextCompactor<br/>.transcripts/*.jsonl + LLM summary]
    end

    subgraph DB["PostgreSQL"]
        CK[checkpoints]
        CW[checkpoint_writes]
        CB[checkpoint_blobs]
        AE[audit_events]
        LM[long_term_memories]
        TR[tool_results]
    end

    Frontend -->|"HTTP REST + SSE"| Backend
    Backend --> DB
    SG --> LT
    SG --> CT
```

## 快速开始

### 前置条件

- Python ≥3.11
- Node.js ≥18
- PostgreSQL（默认连接见下方）

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
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 允许跨域的浏览器来源（JSON 数组） |

### 前端（Vite）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VITE_API_TARGET` | `http://localhost:8000` | 开发服务器 API 代理目标 |

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
├── agent/        # Agent 引擎（图编译、安全、Hook、LLM、路由、审批）
├── api/          # FastAPI 服务器 + 数据模型
├── memory/       # PostgreSQL Checkpoint + 审计日志 + 长期记忆 + 上下文压缩
├── skills/       # Skill 系统（渐进加载、脚本工具、热插拔）
│   └── resolve-time/  # 内置日期时间解析 Skill
└── tools/        # 基础工具（Shell/文件操作/长期记忆保存）

frontend/src/
├── components/   # React 组件
├── hooks/        # useChat 状态机
├── lib/          # 类型化 API 客户端 + SSE 流解析
└── test/         # 测试配置
```

## 开发规范

项目使用 **superharness** 工程纪律框架：严格 TDD（先测试后代码）、系统调试、代码审查。
详见 `CLAUDE.md` 和 `.claude/superharness/`。

---

🤖 技术方案详见 [技术方案报告.md](./技术方案报告.md)
