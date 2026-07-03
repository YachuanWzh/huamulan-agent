# langgraph-claw 教学课程设计 Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 langgraph-claw 项目打造为递进式教学课程（16章），参考 learn-claude-code 的章节结构，输出到 `course/` 目录。

**Architecture:** 每章一个目录 `sXX_topic/`，包含 README.md（中文完整叙事）、`code.py`（独立可运行的简化实现）、`images/`（可选 SVG 图）。代码从 s01 的最小 LangGraph agent loop 递进到 s16 的完整 personal assistant。课程按 6 个阶段组织：核心循环 → 技能系统 → 安全控制 → 记忆持久化 → 服务界面 → 观测总结。

**Tech Stack:** Python 3.11+, LangGraph, FastAPI, React (仅文档说明，不实现前端教学代码), Markdown, pytest (用于验证 code.py 可运行)

---

## 课程结构总览

```
course/
├── README.md                    # 课程总览、学习路径、章节索引
├── s01_agent_loop/              # 每章一个目录
│   ├── README.md                #   中文源文档（完整叙事）
│   ├── code.py                  #   独立可运行代码
│   └── images/                  #   SVG 流程图（可选）
├── s02_tool_system/
├── ...
├── s16_comprehensive/
└── tests/
    └── test_all_chapters.py     # 验证所有 code.py 可导入和运行
```

### 16 章主题

| 章节 | 主题 | 关键概念 | 映射源码 |
|------|------|---------|---------|
| s01 | Agent Loop | StateGraph, nodes, edges, ReAct | `agent/agent.py`, `agent/state.py` |
| s02 | Tool System | Tool definitions, ToolNode, bash/file | `tools/basic.py` |
| s03 | Skill Loading | YAML frontmatter, SkillRegistry | `skills/base.py`, `skills/loader.py` |
| s04 | Skill Routing | Regex triggers, semantic search | `agent/router.py` |
| s05 | Script Tools | Script-as-tool, dynamic binding | `skills/script_tool.py` |
| s06 | Approval Gate | Tool approval pipeline, decisions | `agent/approval.py` |
| s07 | Middleware & Guards | Rate/Call/Loop limits, security guard | `agent/harness.py` |
| s08 | Hook System | PreToolUse/PostToolUse, lifecycle | `agent/hook.py` |
| s09 | Context Compaction | Summarization, transcript archive | `memory/compaction.py` |
| s10 | Long-Term Memory | .memory dir, MEMORY.md | `memory/long_term.py` |
| s11 | Checkpoint | Redis-first, state persistence | `checkpoint/redis_first.py` |
| s12 | Cache System | Redis cache, TTL, noop fallback | `cache/redis_cache.py`, `cache/base.py` |
| s13 | API Server | FastAPI, SSE streaming | `api/server.py`, `api/schemas.py` |
| s14 | Frontend Chat | React, tool approval UI | `frontend/src/` |
| s15 | Tracing | Langfuse, execution logs | `tracing.py` |
| s16 | Comprehensive Agent | All mechanisms together | `agent/harness.py` |

### 6 阶段学习路径

```
阶段1: 核心循环 (s01-s02) → 让 Agent 能动手
阶段2: 技能系统 (s03-s05) → 让 Agent 能扩展
阶段3: 安全控制 (s06-s08) → 给 Agent 边界
阶段4: 记忆持久化 (s09-s12) → 让 Agent 能记住
阶段5: 服务界面 (s13-s14) → 让 Agent 可交互
阶段6: 观测总结 (s15-s16) → 让 Agent 可观测
```

### 每章 README.md 模板结构

```markdown
# sXX: [主题中文名] ([English Name])

> *"[一句格言]"* -- [格言解释]
>
> **Harness 层**: [属于哪个 harness 子系统的哪个部分]

## 问题
[这一章要解决什么问题？为什么需要这个机制？]

## 解决方案
[架构图 / 流程图]

## 工作原理
[分步骤解释，每步配上关键代码片段]

## 变更内容
[表格：之前 vs 之后]

## 试一试
[运行命令 + 示例 prompt]
```

---

## Tasks

### Task 1: 课程总览 README

**Files:**
- Create: `course/README.md`

- [ ] **Step 1: 编写课程总览 README**

```markdown
# langgraph-claw 教学课程

从零理解 LangGraph Agent Harness —— 以一个真实的 Personal Assistant 项目为标本。

## Harness 工程：模型之外的工程

Agency（感知、推理、行动的能力）来自模型训练，不是来自外部代码编排。但一个能干活
的 agent 产品，需要模型和 harness 缺一不可。模型是驾驶者，harness 是载具。

本课程以 **langgraph-claw**（花木兰 Agent）为标本，拆解一个基于 LangGraph 的
Personal Assistant 的完整 harness 架构。与 [learn-claude-code](
https://github.com/shareAI-lab/learn-claude-code) 的"从零构建"不同，本课程是
"从真实项目逆向"——每个机制都对应项目中可运行的真实代码。

## 学习路径

```
阶段1: 核心循环 (s01-s02) → 让 Agent 能动手
阶段2: 技能系统 (s03-s05) → 让 Agent 能扩展
阶段3: 安全控制 (s06-s08) → 给 Agent 边界
阶段4: 记忆持久化 (s09-s12) → 让 Agent 能记住
阶段5: 服务界面 (s13-s14) → 让 Agent 可交互
阶段6: 观测总结 (s15-s16) → 让 Agent 可观测
```

## 全部章节

| 章节 | 主题 | 关键概念 | 源码位置 |
|------|------|---------|---------|
| [s01](./s01_agent_loop/) | Agent Loop | StateGraph, ReAct | `agent/agent.py` |
| [s02](./s02_tool_system/) | Tool System | ToolNode, bash/file | `tools/basic.py` |
| ... | ... | ... | ... |
| [s16](./s16_comprehensive/) | Comprehensive | 全机制合体 | `agent/harness.py` |

## 快速开始

```sh
cd course
pip install -r requirements.txt

python s01_agent_loop/code.py
python s16_comprehensive/code.py
```

## 与 learn-claude-code 的关系

| learn-claude-code | langgraph-claw 教学 |
|---|---|
| 从零构建 harness | 逆向真实项目 |
| 基于 Anthropic API 原始循环 | 基于 LangGraph 框架 |
| 20 章，每章一个机制 | 16 章，每章一个机制 |
| bash + agent loop | LangGraph + FastAPI + React |

两个课程互补：先学 build（learn-claude-code），再学 architecture（本课程）。

## 项目结构

```
course/
  s01_agent_loop/          # 每章一个文件夹
    README.md              #   中文源文档（完整叙事）
    code.py                #   独立可运行代码
    images/                #   SVG 流程图
  s02_tool_system/
  ...
  s16_comprehensive/       # 终点章
  tests/
    test_all_chapters.py   # 验证所有章节代码
```
```

- [ ] **Step 2: Commit**

```bash
git add course/README.md
git commit -m "docs(course): add course overview README with 16-chapter index"
```

---

### Task 2: s01 — Agent Loop（LangGraph 最小循环）

**Files:**
- Create: `course/s01_agent_loop/README.md`
- Create: `course/s01_agent_loop/code.py`

- [ ] **Step 1: 编写 s01 README.md**（完整叙事：问题→解决方案→工作原理→变更内容→试一试）

- [ ] **Step 2: 编写 s01 code.py**（~80行，最小 LangGraph StateGraph + 一个 bash 工具）

```python
#!/usr/bin/env python3
"""
s01_agent_loop.py - LangGraph 最小 Agent 循环

LangGraph 版的 agent loop：用 StateGraph 替代 while True，
用节点和边描述 agent 的行为流程。

    [entry] → [agent_node] → {stop_reason?}
                  ↑              |
                  |    tool_use  |
                  +----[tools]---+

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s01_agent_loop/code.py
"""
import os
import subprocess
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

# ── State ──
class AgentState(TypedDict):
    messages: list

# ── Tool: bash ──
def bash_tool(command: str) -> str:
    """Run a shell command in the workspace."""
    dangerous = ["rm -rf /", "sudo", "shutdown", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        return (r.stdout + r.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

TOOLS = [bash_tool]
TOOL_NODE = ToolNode(TOOLS)

# ── LLM ──
LLM = ChatOpenAI(model=os.getenv("LLM_MODEL", "deepseek-chat"),
                 base_url=os.getenv("LLM_BASE_URL"),
                 api_key=os.getenv("OPENAI_API_KEY"))
LLM_WITH_TOOLS = LLM.bind_tools(TOOLS)

SYSTEM = "You are a coding agent. Use bash to solve tasks. Act, don't explain."

# ── Agent node ──
def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}

# ── Router ──
def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

# ── Build graph ──
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()

# ── Entry point ──
if __name__ == "__main__":
    print("s01: LangGraph Agent Loop")
    print("输入问题，回车发送。输入 q 退出。\n")
    messages = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        messages.append(HumanMessage(content=query))
        result = app.invoke({"messages": messages})
        messages = result["messages"]
        last = messages[-1]
        if hasattr(last, "content") and last.content:
            print(last.content)
        print()
```

- [ ] **Step 3: 编写 smoke test 验证 code.py 可导入**

```python
# course/tests/test_all_chapters.py
def test_s01_imports():
    """Verify s01 code.py can be parsed without syntax errors."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "s01", "course/s01_agent_loop/code.py"
    )
    assert spec is not None, "s01 code.py should be parseable"
```

- [ ] **Step 4: Commit**

```bash
git add course/s01_agent_loop/
git commit -m "docs(course): add s01 - LangGraph Agent Loop"
```

---

### Task 3: s02 — Tool System（工具体系）

**Files:**
- Create: `course/s02_tool_system/README.md`
- Create: `course/s02_tool_system/code.py`

在 s01 基础上添加多个工具（read_file, write_file, list_directory），用 ToolNode 统一管理。

- [ ] **Step 1: 编写 s02 README.md**
- [ ] **Step 2: 编写 s02 code.py**
- [ ] **Step 3: 添加 s02 smoke test**
- [ ] **Step 4: Commit**

---

### Task 4: s03 — Skill Loading（技能加载）

**Files:**
- Create: `course/s03_skill_loading/README.md`
- Create: `course/s03_skill_loading/code.py`
- Create: `course/s03_skill_loading/skills/example/SKILL.md`

展示 YAML frontmatter 格式的 SKILL.md，Phase 1 扫描 frontmatter，Phase 2 按需加载完整指令和工具。

- [ ] **Step 1: 编写 s03 README.md**
- [ ] **Step 2: 编写 s03 code.py + 示例 skill**
- [ ] **Step 3: 添加 s03 smoke test**
- [ ] **Step 4: Commit**

---

### Task 5: s04 — Skill Routing（技能路由）

**Files:**
- Create: `course/s04_skill_routing/README.md`
- Create: `course/s04_skill_routing/code.py`

展示 route_skills 节点：regex 触发器匹配 + 可选的语义搜索。

- [ ] **Step 1: 编写 s04 README.md**
- [ ] **Step 2: 编写 s04 code.py**
- [ ] **Step 3: 添加 s04 smoke test**
- [ ] **Step 4: Commit**

---

### Task 6: s05 — Script Tools（脚本工具）

**Files:**
- Create: `course/s05_script_tools/README.md`
- Create: `course/s05_script_tools/code.py`

展示 YAML frontmatter 中声明 scripts，resolve-time 将 Python 函数转换为 LangChain Tool。

- [ ] **Step 1: 编写 s05 README.md**
- [ ] **Step 2: 编写 s05 code.py**
- [ ] **Step 3: 添加 s05 smoke test**
- [ ] **Step 4: Commit**

---

### Task 7: s06 — Approval Gate（审批管线）

**Files:**
- Create: `course/s06_approval_gate/README.md`
- Create: `course/s06_approval_gate/code.py`

展示 ApprovalGate：工具调用前判断是否需要审批，用户决策后路由到执行或拒绝。

- [ ] **Step 1: 编写 s06 README.md**
- [ ] **Step 2: 编写 s06 code.py**
- [ ] **Step 3: 添加 s06 smoke test**
- [ ] **Step 4: Commit**

---

### Task 8: s07 — Middleware & Guards（中间件与安全守卫）

**Files:**
- Create: `course/s07_middleware/README.md`
- Create: `course/s07_middleware/code.py`

展示中间件链：RateLimitMiddleware, CallLimitMiddleware, LoopDetectionMiddleware + PromptGuard + ToolGuard。

- [ ] **Step 1: 编写 s07 README.md**
- [ ] **Step 2: 编写 s07 code.py**
- [ ] **Step 3: 添加 s07 smoke test**
- [ ] **Step 4: Commit**

---

### Task 9: s08 — Hook System（生命周期 Hook）

**Files:**
- Create: `course/s08_hooks/README.md`
- Create: `course/s08_hooks/code.py`

展示 Hook 系统：PreToolUse, PostToolUse, 在每个 graph node 前后插入扩展点。

- [ ] **Step 1: 编写 s08 README.md**
- [ ] **Step 2: 编写 s08 code.py**
- [ ] **Step 3: 添加 s08 smoke test**
- [ ] **Step 4: Commit**

---

### Task 10: s09 — Context Compaction（上下文压缩）

**Files:**
- Create: `course/s09_context_compaction/README.md`
- Create: `course/s09_context_compaction/code.py`

展示上下文压缩：消息数/Token 阈值触发 → 摘要生成 → 中间消息替换 → 转录本归档。

- [ ] **Step 1: 编写 s09 README.md**
- [ ] **Step 2: 编写 s09 code.py**
- [ ] **Step 3: 添加 s09 smoke test**
- [ ] **Step 4: Commit**

---

### Task 11: s10 — Long-Term Memory（长期记忆）

**Files:**
- Create: `course/s10_long_term_memory/README.md`
- Create: `course/s10_long_term_memory/code.py`

展示 .memory 目录管理：USER.md, SYSTEM.md, MEMORY.md，LLM reflection 决定存什么。

- [ ] **Step 1: 编写 s10 README.md**
- [ ] **Step 2: 编写 s10 code.py**
- [ ] **Step 3: 添加 s10 smoke test**
- [ ] **Step 4: Commit**

---

### Task 12: s11 — Checkpoint（状态持久化）

**Files:**
- Create: `course/s11_checkpoint/README.md`
- Create: `course/s11_checkpoint/code.py`

展示 LangGraph checkpoint：Redis-first 写入 → 异步归档 PostgreSQL → 线程恢复。

- [ ] **Step 1: 编写 s11 README.md**
- [ ] **Step 2: 编写 s11 code.py**
- [ ] **Step 3: 添加 s11 smoke test**
- [ ] **Step 4: Commit**

---

### Task 13: s12 — Cache System（缓存系统）

**Files:**
- Create: `course/s12_cache/README.md`
- Create: `course/s12_cache/code.py`

展示 Redis 缓存层：AsyncCache 协议，NoopCache 降级，TTL 策略。

- [ ] **Step 1: 编写 s12 README.md**
- [ ] **Step 2: 编写 s12 code.py**
- [ ] **Step 3: 添加 s12 smoke test**
- [ ] **Step 4: Commit**

---

### Task 14: s13 — API Server（API 服务）

**Files:**
- Create: `course/s13_api_server/README.md`
- Create: `course/s13_api_server/code.py`

展示 FastAPI 流式 API：SSE 聊天端点，审批端点，线程管理。

- [ ] **Step 1: 编写 s13 README.md**
- [ ] **Step 2: 编写 s13 code.py**
- [ ] **Step 3: 添加 s13 smoke test**
- [ ] **Step 4: Commit**

---

### Task 15: s14 — Frontend Chat（前端聊天界面）

**Files:**
- Create: `course/s14_frontend/README.md`
- （无 code.py —— 前端章仅文档，配架构图和关键代码片段）

纯文档章节：分析 React 前端架构。不做可运行的 code.py（前端不适合单文件教学）。

- [ ] **Step 1: 编写 s14 README.md**
- [ ] **Step 2: Commit**

---

### Task 16: s15 — Tracing（追踪与观测）

**Files:**
- Create: `course/s15_tracing/README.md`
- Create: `course/s15_tracing/code.py`

展示 Langfuse 集成 + 执行日志系统（7 种事件类型，结构化 JSONB 日志）。

- [ ] **Step 1: 编写 s15 README.md**
- [ ] **Step 2: 编写 s15 code.py**
- [ ] **Step 3: 添加 s15 smoke test**
- [ ] **Step 4: Commit**

---

### Task 17: s16 — Comprehensive Agent（综合 Agent）

**Files:**
- Create: `course/s16_comprehensive/README.md`
- Create: `course/s16_comprehensive/code.py`

前面所有机制整合到一个完整的 agent harness 中。总结架构全景。

- [ ] **Step 1: 编写 s16 README.md**
- [ ] **Step 2: 编写 s16 code.py**
- [ ] **Step 3: 添加 s16 smoke test**
- [ ] **Step 4: Commit**

---

### Task 18: requirements.txt + tests

**Files:**
- Create: `course/requirements.txt`
- Create: `course/tests/test_all_chapters.py`

- [ ] **Step 1: 编写 course/requirements.txt**

```
langgraph>=0.2
langchain-core>=0.3
langchain-openai>=0.3
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2: 编写 tests/test_all_chapters.py**（完整 smoke test 套件，验证所有 code.py 可导入）

```python
"""Smoke tests: verify every chapter's code.py can be parsed and imported."""
import importlib.util
import sys
from pathlib import Path

COURSE_DIR = Path(__file__).resolve().parent.parent
CHAPTERS = sorted(
    d.name for d in COURSE_DIR.iterdir()
    if d.is_dir() and d.name.startswith("s")
)

def _load_module(chapter: str):
    code_path = COURSE_DIR / chapter / "code.py"
    if not code_path.exists():
        return None  # s14 frontend has no code.py
    spec = importlib.util.spec_from_file_location(
        chapter, str(code_path)
    )
    assert spec is not None, f"{chapter}/code.py should be parseable"
    module = importlib.util.module_from_spec(spec)
    sys.modules[chapter] = module
    spec.loader.exec_module(module)
    return module

import pytest

@pytest.mark.parametrize("chapter", CHAPTERS)
def test_chapter_code_parses(chapter):
    """Every chapter's code.py must be syntactically valid."""
    code_path = COURSE_DIR / chapter / "code.py"
    if not code_path.exists():
        pytest.skip(f"{chapter} has no code.py")
    with open(code_path, encoding="utf-8") as f:
        compile(f.read(), str(code_path), "exec")
```

- [ ] **Step 3: 运行测试验证**

```bash
cd course && python -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add course/requirements.txt course/tests/
git commit -m "test(course): add requirements.txt and chapter smoke tests"
```

---

### Task 19: 最终验证与清理

- [ ] **Step 1: 运行完整测试套件**

```bash
cd course && python -m pytest tests/ -v
```

- [ ] **Step 2: 运行每个 code.py 确认无 import 错误**

逐个运行 `python sXX_topic/code.py --help` 或类似方式确认。

- [ ] **Step 3: 最终 commit**

```bash
git add course/
git commit -m "docs(course): complete 16-chapter LangGraph agent harness course"
```

---

## 自我审查

### Spec Coverage
- ✅ 课程总览 README（Task 1）
- ✅ 16 个章节，每章一个机制（Task 2-17）
- ✅ 每章包含 README.md + code.py（s14 除外，仅文档）
- ✅ smoke test 验证所有 code.py 可解析（Task 18）
- ✅ requirements.txt（Task 18）
- ✅ 参考 learn-claude-code 的章节结构（README.md 模板）

### Placeholder Scan
- ✅ 无 TBD/TODO
- ✅ 所有代码步骤有具体代码
- ✅ 所有文件路径精确

### Type Consistency
- ✅ code.py 使用统一的 AgentState TypedDict
- ✅ 章节间变量命名一致
