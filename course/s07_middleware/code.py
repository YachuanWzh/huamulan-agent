#!/usr/bin/env python3
"""
s07_middleware.py — Middleware & Guards（中间件与安全守卫）

在工具 call 执行前，通过中间件链和守卫机制拦截危险/异常请求。
中间件 = 可组合、有序、独立的检查层，任一阻断则阻止执行。

    [entry] → [agent_node] → {prompt_guard?} → {tool_calls?}
                  ↑                                 |
                  |   tools                         v
                  +-------- [apply_middleware_chain + tool_guard] ---+
                           (blocked → ToolMessage)   (ok → execute)

中间件链（RateLimit → CallLimit → LoopDetection）在 pre_tool 阶段运行，
PromptGuard 拦截用户输入，ToolGuard 检查工具命令模式。

Reference: backend/src/personal_assistant/agent/harness.py

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s07_middleware/code.py
"""
import json
import os
import re
import subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

WORKSPACE = os.getcwd()

# ── State ──────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Tools ──────────────────────────────────────────────────
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
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
LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)
LLM_WITH_TOOLS = LLM.bind_tools(TOOLS)
SYSTEM = f"You are a coding agent at {WORKSPACE}. Use bash to solve tasks."


# ═════════════════════════════════════════════════════════════
#  PromptGuard — 检查用户输入中的注入/越狱模式
# ═════════════════════════════════════════════════════════════

PROMPT_DETECTION_PATTERNS: list[tuple[str, str, str]] = [
    (
        "instruction_override",
        "HIGH",
        r"(?is)(?:\b(?:forget|ignore|disregard)\b.{0,20}\b(?:instructions?|rules?|prompts?|constraints?)\b"
        r"|忽略.{0,10}(?:指令|指示|设定|规则|命令|约束|限制|要求))",
    ),
    (
        "system_prompt_leak",
        "HIGH",
        r"(?is)(?:\b(?:output|print|show|reveal|repeat)\b.{0,20}\b(?:system|developer)\b.{0,10}\b(?:prompt|instructions?)\b"
        r"|(?:输出|打印|展示|泄露|复述).{0,10}(?:系统|开发者|角色).{0,10}(?:提示词|指令|设定|规则))",
    ),
    (
        "role_play_jailbreak",
        "HIGH",
        r"(?is)(?:\byou\s+are\s+now\s+(?:dan|developer\s+mode)\b"
        r"|\benter\s+developer\s+mode\b"
        r"|你现在是.{0,10}DAN|进入.{0,10}开发者模式)",
    ),
    (
        "identity_spoof",
        "HIGH",
        r"(?is)(?:\bi\s+(?:am|have)\b.{0,12}\b(?:root|superuser|admin)\b.{0,12}\b(?:access|privileges?|override|bypass|policy)\b"
        r"|我是.{0,10}(?:管理员|root|超级用户).{0,10}(?:绕过|无视|覆盖|解除).{0,10}(?:规则|限制|权限|策略))",
    ),
]


def scan_prompt(message: str) -> str | None:
    """扫描用户输入，命中任一注入模式则返回原因；否则返回 None。"""
    for category, severity, pattern in PROMPT_DETECTION_PATTERNS:
        if re.search(pattern, message):
            return f"PromptGuard({category}, {severity}): injection/jailbreak detected"
    return None


# ═════════════════════════════════════════════════════════════
#  ToolGuard — 检查工具命令中的危险模式
# ═════════════════════════════════════════════════════════════

TOOL_DETECTION_PATTERNS: list[tuple[str, str, str]] = [
    ("disk_format", "CRITICAL", r"(?is)(?:\bmkfs(?:\.\w+)?\b|\bdd\b.{0,80}\bof=/dev/)"),
    ("download_pipe_exec", "CRITICAL", r"(?is)\b(?:curl|wget)\b.{0,160}\|.{0,30}\b(?:bash|sh|zsh|powershell|pwsh)\b"),
    ("reverse_shell", "CRITICAL", r"(?is)(?:/dev/tcp/|\bnc\b.{0,80}\s-e\b|\bncat\b.{0,80}\s-e\b)"),
    ("privilege_escalation", "CRITICAL", r"(?is)(?:^|[=;&|]\s*)\b(?:sudo|su|doas)\b"),
    ("delete_or_move_files", "HIGH", r"(?is)(?:^|[=;&|]\s*)\b(?:rm|del|Remove-Item|mv)\b"),
    ("shutdown_or_process_control", "HIGH", r"(?is)(?:\b(?:shutdown|reboot|Stop-Computer|Restart-Computer|killall|pkill|taskkill)\b|(?:^|[=;&|]\s*)\bkill\b)"),
    ("world_writable_permissions", "HIGH", r"(?is)\bchmod\b.{0,40}\b777\b"),
    ("ssh_key_modification", "HIGH", r"(?is)(?:\.ssh[/\\]|authorized_keys|id_rsa|id_ed25519)"),
]


def scan_tool(tool_name: str, args: dict) -> str | None:
    """扫描工具调用参数，命中危险模式则返回原因；否则返回 None。"""
    haystack = f"{tool_name}\n{json.dumps(args, ensure_ascii=False)}"
    for category, severity, pattern in TOOL_DETECTION_PATTERNS:
        if re.search(pattern, haystack):
            return f"ToolGuard({category}, {severity}): dangerous command blocked"
    return None


# ═════════════════════════════════════════════════════════════
#  Middleware chain — 三个中间件，pre_tool 阶段逐一检查
# ═════════════════════════════════════════════════════════════

def _tool_call_name(call: dict) -> str:
    return str(call.get("name") or "tool")


def _tool_call_signature(call: dict) -> str:
    args = json.dumps(call.get("args", {}), ensure_ascii=False, sort_keys=True)
    return f"{_tool_call_name(call)}:{args}"


def _tool_call_id(call: dict) -> str:
    return str(call.get("id") or "")


def _blocked_tool_message(call: dict, content: str) -> ToolMessage:
    return ToolMessage(tool_call_id=_tool_call_id(call), content=content)


@dataclass
class RateLimitMiddleware:
    """每个工具最多调用 50 次。"""
    max_calls_per_tool: int = 50
    _counts: dict[str, int] = field(default_factory=dict)

    def pre_tool(self, call: dict) -> ToolMessage | None:
        name = _tool_call_name(call)
        self._counts[name] = self._counts.get(name, 0) + 1
        if self._counts[name] <= self.max_calls_per_tool:
            return None
        return _blocked_tool_message(
            call,
            f"RateLimitMiddleware blocked tool '{name}': "
            f"per-request limit is {self.max_calls_per_tool} calls.",
        )


@dataclass
class CallLimitMiddleware:
    """总工具调用次数最多 20 次。"""
    max_total_calls: int = 20
    _count: int = 0

    def pre_tool(self, call: dict) -> ToolMessage | None:
        self._count += 1
        if self._count <= self.max_total_calls:
            return None
        return _blocked_tool_message(
            call,
            f"CallLimitMiddleware blocked: total tool call limit "
            f"is {self.max_total_calls}.",
        )


@dataclass
class LoopDetectionMiddleware:
    """滑动窗口（size=20）内同一签名最多重复 15 次。"""
    window_size: int = 20
    max_repeats: int = 15
    _window: deque[str] = field(default_factory=deque)

    def pre_tool(self, call: dict) -> ToolMessage | None:
        sig = _tool_call_signature(call)
        self._window.append(sig)
        while len(self._window) > self.window_size:
            self._window.popleft()
        if sum(1 for s in self._window if s == sig) < self.max_repeats:
            return None
        return _blocked_tool_message(
            call,
            f"LoopDetectionMiddleware blocked repeated tool call: "
            f"'{_tool_call_name(call)}' used the same arguments "
            f"{self.max_repeats} times within the last {self.window_size} calls.",
        )


def apply_middleware_chain(
    call: dict, middlewares: list
) -> ToolMessage | None:
    """依次运行中间件链，第一个阻断即返回。"""
    for mw in middlewares:
        msg = mw.pre_tool(call)
        if msg is not None:
            return msg
    return None


# ═════════════════════════════════════════════════════════════
#  Graph integration — 在工具执行前插入 prompt_guard + middleware + tool_guard
# ═════════════════════════════════════════════════════════════

middlewares = [
    RateLimitMiddleware(),
    CallLimitMiddleware(),
    LoopDetectionMiddleware(),
]


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}


def pre_tools_node(state: AgentState) -> dict:
    """在 ToolNode 之前运行：检查最后一条用户消息（PromptGuard），
    然后对每个 tool_call 依次运行 ToolGuard + 中间件链。"""
    last_ai = state["messages"][-1]
    if not hasattr(last_ai, "tool_calls") or not last_ai.tool_calls:
        return {}

    # PromptGuard: 扫描最近的 HumanMessage
    blocked_messages: list[ToolMessage] = []
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            reason = scan_prompt(str(msg.content))
            if reason:
                for tc in last_ai.tool_calls:
                    blocked_messages.append(
                        ToolMessage(tool_call_id=tc["id"], content=reason)
                    )
                return {"messages": blocked_messages}
            break

    # ToolGuard + Middleware chain per tool_call
    allowed: list[dict] = []
    for tc in last_ai.tool_calls:
        call = {"id": tc["id"], "name": tc["name"], "args": tc["args"]}

        # ToolGuard first (security check on command content)
        reason = scan_tool(tc["name"], tc["args"])
        if reason:
            blocked_messages.append(
                ToolMessage(tool_call_id=tc["id"], content=reason)
            )
            continue

        # Middleware chain
        mw_msg = apply_middleware_chain(call, middlewares)
        if mw_msg:
            blocked_messages.append(mw_msg)
            continue

        allowed.append(tc)

    # If all calls were blocked, return ToolMessages to LLM (no tool execution)
    if blocked_messages:
        return {"messages": blocked_messages}

    return {}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    # If the last message is a blocking ToolMessage, send back to agent (skip tools)
    if isinstance(last, ToolMessage) and "blocked" in str(last.content).lower():
        return "agent"
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "pre_tools"
    return END


def pre_tools_router(state: AgentState) -> str:
    """pre_tools 之后：如果全都放行 → tools 节点；如果产生了阻断消息 → agent。"""
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and "blocked" in str(last.content).lower():
        return "agent"
    return "tools"


# ── Build graph with middleware insertion ──────────────────
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("pre_tools", pre_tools_node)
graph.add_node("tools", ToolNode(TOOLS))
graph.set_entry_point("agent")

graph.add_conditional_edges(
    "agent",
    should_continue,
    {"tools": "pre_tools", "pre_tools": "pre_tools", "agent": "agent", END: END},
)
graph.add_conditional_edges(
    "pre_tools",
    pre_tools_router,
    {"tools": "tools", "agent": "agent"},
)
graph.add_edge("tools", "agent")

app = graph.compile()


# ── Entry point ────────────────────────────────────────────
if __name__ == "__main__":
    print("s07: Middleware & Guards")
    print("   中间件链: RateLimit(50) → CallLimit(20) → LoopDetection(15/20)")
    print("   守卫: PromptGuard (4 patterns) + ToolGuard (8 patterns)")
    print("   输入问题后回车发送。输入 q 退出。\n")

    messages = []
    while True:
        try:
            query = input("\033[36ms07 >> \033[0m")
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
