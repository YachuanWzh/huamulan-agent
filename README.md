# LangGraph Personal Assistant

个人助理 agent 原型：React 前端 + FastAPI/LangGraph 后端。

## Capabilities

- 单个 ReAct Agent
- 工具调用审批：未批准的 tool call 不会执行
- `backend/src/personal_assistant/skills` 自动扫描，支持运行时 reload
- PostgreSQL checkpoint + thread replay
- 可配置 LLM `base_url`、`model`、`api_key`、`temperature`
- agent loop + harness 分层
- skill routing：每轮先根据 skills 的 `SKILL.md` 选择相关 skill，再约束 agent 能力

## Quick Start

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
$env:OPENAI_API_KEY="..."
uvicorn personal_assistant.api.server:app --reload --host 0.0.0.0 --port 8000
```

```powershell
cd frontend
npm install
npm run dev
```

默认 PostgreSQL：

```text
postgresql://langchain_user:Deartyl0115@192.168.5.7:5432/langchain_db?sslmode=disable
```

可通过 `DATABASE_URL` 覆盖。

## Skill Layout

每个 skill 是 `skills/<skill-name>/SKILL.md`，可选 `skill.py` 暴露 `TOOLS`。

```python
from langchain_core.tools import tool

@tool
def my_tool(arg: str) -> str:
    return arg

TOOLS = [my_tool]
```

新增、删除、修改 skill 后调用 `/api/skills/reload` 即可热插拔。
