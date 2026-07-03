#!/usr/bin/env python3
"""
s01_agent_loop.py — LangGraph 最小 Agent 循环

LangGraph 版的 agent loop：用 StateGraph 替代 while True，
用节点和边描述 agent 的行为流程。

    [entry] → [agent_node] → {stop_reason?}
                  ↑              |
                  |   tool_use   |
                  +---[tools]----+

与 Claude Code 原始实现的区别：
- 控制流：声明式节点+边，而非手写 while+if
- 状态管理：AgentState 自动累加，而非手动管理 messages 列表
- 可扩展：后续章节在图上叠加节点和边，无需改动现有代码

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s01_agent_loop/code.py
"""
import os
import subprocess
from typing import Annotated

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

# ── State ──────────────────────────────────────────────────
# AgentState 是所有节点共享的状态容器。
# Annotated[list, add_messages] 表示消息使用"累加"语义：
# 每次节点返回 {"messages": [...]} 时，新消息追加到列表末尾。
from typing import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Tool: bash ─────────────────────────────────────────────
# 一个工具 = 一个被 @tool 装饰的 Python 函数。
# docstring 是工具的 description，LLM 据此决定何时调用。
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace. Use this to read files,
    run tests, check git status, or execute any shell operation."""
    # 基础安全检查
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=120
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


TOOLS = [bash]
TOOL_NODE = ToolNode(TOOLS)

# ── LLM ────────────────────────────────────────────────────
LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)
LLM_WITH_TOOLS = LLM.bind_tools(TOOLS)

SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. "
    "Use bash to solve tasks. Act, don't explain."
)


# ── Agent node ─────────────────────────────────────────────
def agent_node(state: AgentState) -> dict:
    """调用 LLM，可能返回文本或工具调用。"""
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}


# ── Router ─────────────────────────────────────────────────
def should_continue(state: AgentState) -> str:
    """条件边的路由逻辑：LLM 想调工具 → tools 节点；否则 → END。"""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ── Build graph ────────────────────────────────────────────
# 这就是整个 Agent。3 个组件：节点 + 边 + 状态。
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.set_entry_point("agent")
graph.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "tools", END: END},
)
graph.add_edge("tools", "agent")
app = graph.compile()


# ── Entry point ────────────────────────────────────────────
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

        # 输出模型的最终文本响应
        last = messages[-1]
        if hasattr(last, "content") and last.content:
            print(last.content)
        print()
