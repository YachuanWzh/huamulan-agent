"""
s02_tool_system.py — 从单一 bash 工具扩展到完整工具体系

ToolNode 管理 4 个工具，自动按名匹配 tool_call → 工具函数 → ToolMessage。
Graph 结构与 s01 完全相同，唯一区别：TOOLS 列表从 [bash] 变成 [bash, read_file, write_file, list_directory]。

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s02_tool_system/code.py
"""
import os, subprocess
from typing import Annotated, TypedDict
from pathlib import Path

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

WORKSPACE = Path.cwd()

# ── State ──────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Tools ──────────────────────────────────────────────────
# 每个工具 = @tool 装饰的函数。函数签名 → input_schema，docstring → description。
# ToolNode 自动: 解析 name → 匹配函数 → 校验参数 → 执行 → 返回 ToolMessage

@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        return (r.stdout + r.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

@tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace."""
    target = WORKSPACE / path
    if not target.is_file():
        return f"Error: {path} not found or is not a file"
    return target.read_text(encoding="utf-8", errors="replace")

@tool
def write_file(path: str, content: str) -> str:
    """Write UTF-8 text to a file in the workspace. Creates parent directories."""
    target = WORKSPACE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content.encode('utf-8'))} bytes to {path}"

@tool
def list_directory(path: str = ".") -> str:
    """List direct children of a directory. Directories are suffixed with /."""
    target = WORKSPACE / path
    if not target.is_dir():
        return f"Error: {path} is not a directory"
    rows = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    return "\n".join(f"{c.name}{'/' if c.is_dir() else ''}" for c in rows) or "(empty)"

# 工具注册：一个列表，ToolNode 接管全部调度
TOOLS = [bash, read_file, write_file, list_directory]
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
    f"You are a coding agent. Use tools to solve tasks. Act, don't explain. "
    "Use write_file to create files, read_file to read files, "
    "list_directory to explore directories, bash for everything else."
)

# ── Nodes ──────────────────────────────────────────────────
def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    return {"messages": [LLM_WITH_TOOLS.invoke(messages)]}

def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END

# ── Graph ──────────────────────────────────────────────────
# 与 s01 完全相同的 Graph 结构。唯一变化：TOOLS 列表从 1 个变成 4 个。
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()

# ── REPL ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("s02: Tool System — bash + read_file + write_file + list_directory")
    print("      ToolNode 管理 4 个工具，自动按名分发 tool_calls。输入 q 退出。\n")

    messages = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
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
