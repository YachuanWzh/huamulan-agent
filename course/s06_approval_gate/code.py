#!/usr/bin/env python3
"""
s06_approval_gate.py — Tool Call Approval Pipeline

在 agent 和 tools 之间插入 approval 节点。读操作自动放行，
写/删除操作需要用户批准才能执行。

Graph: [entry] → [agent] → [approval] → [tools] → [agent]
                           ↓ (pending)    ↑ (approved)
                        END (wait)

真实项目参考: backend/src/personal_assistant/agent/approval.py
"""
import os
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

# ── State ──────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    pending_approvals: list[dict]       # 等待用户决策的 tool calls
    approval_decisions: dict[str, bool] # approval_id → True/False

# 全局决策存储（真实项目中是 AgentHarness.decisions）
_decisions: dict[str, bool] = {}

# ── Tools: read / write 分离 ────────────────────────────────
WORKSPACE = Path(os.getcwd())

@tool
def read_file(path: str) -> str:
    """Read the contents of a file."""
    try:
        return (WORKSPACE / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except OSError as e:
        return f"Error: {e}"

@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates or overwrites)."""
    full = WORKSPACE / path
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return f"OK: Wrote {full.stat().st_size} bytes to {path}"
    except OSError as e:
        return f"Error: {e}"

TOOLS = [read_file, write_file]
TOOL_NODE = ToolNode(TOOLS)

# ── Approval rules ─────────────────────────────────────────
_READ_ONLY = {"read_file"}

def requires_approval(tool_call: dict) -> bool:
    """写/删除操作需要审批。"""
    return tool_call.get("name", "") not in _READ_ONLY

# ── LLM ────────────────────────────────────────────────────
LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
).bind_tools(TOOLS)

SYSTEM = (
    f"You are a coding agent at {WORKSPACE}. "
    "Use read_file and write_file. Act, don't explain."
)

# ── Agent node ─────────────────────────────────────────────
def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    return {"messages": [LLM.invoke(messages)]}

# ── Approval node ──────────────────────────────────────────
def approval_node(state: AgentState) -> dict:
    """检查 tool_calls 审批状态：已批准→放行，已拒绝→返回 denied 消息，
    待决→加入 pending_approvals。"""
    msgs = state.get("messages", [])
    if not msgs:
        return {"pending_approvals": []}

    last = msgs[-1]
    calls = getattr(last, "tool_calls", None)
    if not isinstance(last, AIMessage) or not calls:
        return {"pending_approvals": []}

    answered = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
    pending: list[dict] = []
    denials: list[ToolMessage] = []

    for c in calls:
        if not requires_approval(c):
            continue
        cid = c.get("id", "")
        d = _decisions.get(cid)
        if d is None:
            pending.append({
                "approval_id": cid, "tool_call_id": c["id"],
                "name": c["name"], "args": c.get("args", {}),
            })
        elif d is False and c["id"] not in answered:
            denials.append(ToolMessage(
                tool_call_id=c["id"],
                content="Tool call denied by user approval policy.",
            ))

    result: dict = {"pending_approvals": pending}
    if denials:
        result["messages"] = denials
    return result

# ── Approval router ────────────────────────────────────────
def approval_route(state: AgentState) -> str:
    pending = state.get("pending_approvals") or []
    if pending:
        return "wait"

    msgs = state.get("messages", [])
    answered = {m.tool_call_id for m in msgs if isinstance(m, ToolMessage)}
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if tc["id"] not in answered:
                    return "tools"
            break
    return END

# ── Build graph ────────────────────────────────────────────
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("approval", approval_node)
graph.add_node("tools", TOOL_NODE)
graph.set_entry_point("agent")
graph.add_edge("agent", "approval")
graph.add_conditional_edges(
    "approval", approval_route,
    {"tools": "tools", "wait": END, END: END},
)
graph.add_edge("tools", "agent")
app = graph.compile()

# ── Entry point ────────────────────────────────────────────
if __name__ == "__main__":
    print("s06: Approval Gate — Tool Call Approval Pipeline")
    print("读操作自动放行；写操作需要你批准。输入 q 退出。\n")

    msgs = []
    while True:
        try:
            q = input("\033[36ms06 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if q.strip().lower() in ("q", "exit", ""):
            break
        msgs.append(HumanMessage(content=q))

        while True:
            result = app.invoke({
                "messages": msgs,
                "pending_approvals": [],
                "approval_decisions": _decisions,
            })
            msgs = result["messages"]
            pending = result.get("pending_approvals") or []
            if not pending:
                break

            print("\n" + "=" * 60)
            print("APPROVAL REQUIRED — 以下操作需要批准:")
            for i, item in enumerate(pending):
                print(f"  [{i}] {item['name']}({item['args']})")
            print("=" * 60)
            while True:
                choice = input("Approve all? [y]es / [n]o: ").strip().lower()
                if choice in ("y", "yes"):
                    _decisions.update({p["approval_id"]: True for p in pending})
                    break
                if choice in ("n", "no"):
                    _decisions.update({p["approval_id"]: False for p in pending})
                    break
                print("Type 'y' or 'n'.")

        last = msgs[-1] if msgs else None
        if last and getattr(last, "content", None):
            print(last.content)
        print()
