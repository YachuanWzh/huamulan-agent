#!/usr/bin/env python3
"""
s08_hooks.py — Agent 生命周期 Hook 系统

在 s01 的 Agent Loop 上叠加 Hook 体系：不修改核心循环，
就能在工具调用前后、Agent 启停、异常时注入自定义行为。
核心思想："挂在循环上，不写进循环里"。

    [agent_node] ──→ [with_hooks(tools_node)] ──→ [agent_node]
                          │
              PRE_TOOL ───┴─── POST_TOOL
              logging           timing

真实参考: backend/src/personal_assistant/agent/hook.py

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s08_hooks/code.py
"""
import os
import subprocess
import time
from collections import defaultdict
from enum import Enum
from typing import Annotated, Any, Callable, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

load_dotenv(override=True)

# ═══════════════════════════════════════════════════════════════
# Hook System (本章核心)
# ═══════════════════════════════════════════════════════════════


class HookStage(str, Enum):
    """Agent 生命周期中的 Hook 节点。

    真实项目 (agent/hook.py) 还有 ROUTE_SKILLS、COMPACT_CONTEXT、
    AGENT、MEMORY_REFLECTION、APPROVAL 等更多阶段。
    """
    PRE_TOOL    = "pre_tool"     # 工具调用前
    POST_TOOL   = "post_tool"    # 工具调用后
    AGENT_START = "agent_start"  # Agent 启动
    AGENT_END   = "agent_end"    # Agent 结束
    ON_ERROR    = "on_error"     # 异常发生


class AgentHookManager:
    """按阶段注册和调用 Hook 回调。

    关键设计:
    - 一个阶段可以注册多个回调，按注册顺序执行
    - Hook 抛出异常时静默吞掉 —— Hook 是观察者，不阻断主流程
    - 通过 context dict 在不同 Hook 之间传递上下文
    """
    def __init__(self):
        self._hooks: dict[HookStage, list[Callable[[dict], None]]] = (
            defaultdict(list)
        )

    def register(self, stage: HookStage, callback: Callable[[dict], None]) -> None:
        """注册一个 Hook 回调到指定阶段。"""
        self._hooks[stage].append(callback)

    def run_hooks(self, stage: HookStage, context: dict) -> None:
        """执行指定阶段的所有注册回调。单个 Hook 失败不影响后续 Hook 和主流程。"""
        for hook in self._hooks.get(stage, []):
            try:
                hook(context)
            except Exception:
                # Hook 是观察者，静默失败 —— 这是和 Middleware 的根本区别
                pass


def with_hooks(node_fn: Callable, hook_manager: AgentHookManager) -> Callable:
    """将 graph 节点包装上 before/after Hook。

    真实项目 (agent/hook.py) 版本支持异步、RunnableConfig 注入、
    以及 error phase。本章展示同步简化版以聚焦核心机制。

    Args:
        node_fn: 原始 graph 节点函数
        hook_manager: Hook 管理器

    Returns:
        包装后的节点函数，执行前后自动触发 Hook
    """
    def wrapped(state: dict, *args: Any, **kwargs: Any) -> dict:
        # 从 state 消息中提取最后一条 HumanMessage 的命令作为上下文
        ctx: dict[str, Any] = {"state": state}
        hook_manager.run_hooks(HookStage.PRE_TOOL, ctx)

        t0 = time.time()
        try:
            result = node_fn(state, *args, **kwargs)
        except Exception as exc:
            elapsed = time.time() - t0
            ctx.update({"elapsed": elapsed, "error": exc})
            hook_manager.run_hooks(HookStage.ON_ERROR, ctx)
            raise

        elapsed = time.time() - t0
        ctx.update({"result": result, "elapsed": elapsed})
        hook_manager.run_hooks(HookStage.POST_TOOL, ctx)
        return result

    return wrapped


# ═══════════════════════════════════════════════════════════════
# Example Hooks
# ═══════════════════════════════════════════════════════════════

def logging_hook(ctx: dict) -> None:
    """PRE_TOOL Hook: 打印即将执行的工具调用详情。"""
    state = ctx.get("state", {})
    msgs = state.get("messages", [])
    if not msgs:
        return
    last_msg = msgs[-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        for tc in last_msg.tool_calls:
            name = tc.get("name", "?")
            args = tc.get("args", {})
            # 截断过长参数以提高可读性
            args_str = str(args)
            if len(args_str) > 80:
                args_str = args_str[:77] + "..."
            print(f"  [Hook: PRE_TOOL]  tool={name}  args={args_str}")


def timing_hook(ctx: dict) -> None:
    """POST_TOOL Hook: 打印工具耗时和结果摘要。"""
    elapsed = ctx.get("elapsed", 0)
    result = ctx.get("result", {})
    msgs = result.get("messages", [])
    summary = ""
    if msgs:
        last = msgs[-1]
        content = getattr(last, "content", str(last))
        summary = str(content)[:60].replace("\n", " ")
    print(f"  [Hook: POST_TOOL]  elapsed={elapsed:.2f}s  result={summary}...")


def agent_lifecycle_hook(ctx: dict) -> None:
    """AGENT_START / AGENT_END Hook: 记录 Agent 生命周期边界。"""
    stage = ctx.get("stage", "?")
    ts = time.strftime("%H:%M:%S", time.localtime(ctx.get("ts", time.time())))
    print(f"  [Hook: {stage}]  time={ts}")


# ═══════════════════════════════════════════════════════════════
# Agent (s01 核心，不变)
# ═══════════════════════════════════════════════════════════════


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


@tool
def bash(command: str) -> str:
    """Run a shell command. Use to list files, read content, check git status."""
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=120
        )
        out = (r.stdout + r.stderr).strip()
        return out[:5000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"
    except (FileNotFoundError, OSError) as exc:
        return f"Error: {exc}"


TOOLS = [bash]
TOOL_NODE = ToolNode(TOOLS)

LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.1,
)
LLM_WITH_TOOLS = LLM.bind_tools(TOOLS)

SYSTEM = (
    f"You are a coding agent in {os.getcwd()}. "
    "Use bash to solve tasks. Act, don't explain."
)


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ═══════════════════════════════════════════════════════════════
# Build Graph (核心循环不变，只包一层 with_hooks)
# ═══════════════════════════════════════════════════════════════

hook_manager = AgentHookManager()
hook_manager.register(HookStage.PRE_TOOL, logging_hook)
hook_manager.register(HookStage.POST_TOOL, timing_hook)

# 把 tools_node 包上 Hook —— agent_node 和 graph 结构一行不变
hooked_tools = with_hooks(TOOL_NODE, hook_manager)

graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", hooked_tools)  # 用包装后的节点
graph.set_entry_point("agent")
graph.add_conditional_edges(
    "agent", should_continue,
    {"tools": "tools", END: END},
)
graph.add_edge("tools", "agent")
app = graph.compile()


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("s08: Hook System — 挂在循环上，不写进循环里")
    print("=" * 60 + "\n")

    # 触发 AGENT_START
    hook_manager.run_hooks(
        HookStage.AGENT_START,
        {"stage": "AGENT_START", "ts": time.time()},
    )

    messages: list = []
    while True:
        try:
            query = input("\033[36ms08 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit"):
            break
        if not query.strip():
            continue

        messages.append(HumanMessage(content=query))
        try:
            result = app.invoke({"messages": messages})
        except Exception as exc:
            hook_manager.run_hooks(
                HookStage.ON_ERROR,
                {"error": exc, "ts": time.time()},
            )
            print(f"  Error: {exc}")
            continue

        messages = result["messages"]
        last = messages[-1]
        if hasattr(last, "content") and last.content:
            print(last.content)
        print()

    # 触发 AGENT_END
    hook_manager.run_hooks(
        HookStage.AGENT_END,
        {"stage": "AGENT_END", "ts": time.time()},
    )
    print("Goodbye.\n")
