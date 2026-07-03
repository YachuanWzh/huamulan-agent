#!/usr/bin/env python3
"""
s09_context_compaction.py -- Context Compaction

When conversation grows too long: archive full transcript, generate
LLM summary, replace middle messages, keep first 2 + last 2.

Reference: backend/src/personal_assistant/memory/compaction.py

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s09_context_compaction/code.py
"""
import json, os, subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, AnyMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

load_dotenv(override=True)

# ── Token estimation: chars / 4 ───────────────────────────────
def estimate_tokens(msgs: list[AnyMessage]) -> int:
    return sum(max(1, len(str(getattr(m, "content", ""))) // 4) for m in msgs)

# ── ContextCompactor ───────────────────────────────────────────
COMPACTED = "[Compacted]"

class ContextCompactor:
    """Archive transcript, generate summary, replace middle messages."""

    def __init__(self, *, msg_n: int = 20, tok_n: int = 100_000,
                 archive_dir: Path | None = None, llm=None):
        self.msg_n = msg_n
        self.tok_n = tok_n
        self.archive_dir = archive_dir or Path(".transcripts")
        self.llm = llm

    def should_compact(self, msgs: list[AnyMessage]) -> bool:
        return len(msgs) >= self.msg_n or estimate_tokens(msgs) >= self.tok_n

    def compact(self, msgs: list[AnyMessage], *, tid: str = "") -> list[AnyMessage]:
        if not self.should_compact(msgs):
            return list(msgs)
        archive = self._archive(msgs, tid)
        summary = self._summarize(msgs)
        # Keep first human + first AI, then summary, then last AI
        kept = []
        for m in msgs:
            if isinstance(m, HumanMessage):
                kept.append(m); break
        for m in msgs:
            if isinstance(m, AIMessage):
                kept.append(m); break
        kept.append(HumanMessage(content=f"{COMPACTED}\nTranscript: {archive}\n\n{summary}"))
        for m in reversed(msgs):
            if isinstance(m, AIMessage):
                kept.append(m); break
        return kept

    def _archive(self, msgs, tid):
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in (tid or "default"))
        p = self.archive_dir / f"thread_{safe}_{ts}.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps({"type": m.__class__.__name__,
                    "content": getattr(m, "content", "")}, ensure_ascii=False) + "\n")
        return p

    def _summarize(self, msgs):
        txt = "\n\n".join(f"[{m.__class__.__name__}] {str(getattr(m,'content',''))[:2000]}"
                          for m in msgs)
        prompt = ("Summarize this conversation (中文): goal, decisions, files, remaining work.\n\n"
                  f"=== Conversation ===\n{txt}")
        if self.llm is None:
            return "Summary unavailable. See transcript."
        try:
            resp = self.llm.invoke([HumanMessage(content=prompt[:30000])])
            return str(resp.content).strip() or "Summary empty."
        except Exception:
            return "Summary failed. See transcript."

# ── State ──────────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

# ── Tool: bash ─────────────────────────────────────────────────
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    if any(d in command for d in ["rm -rf /", "sudo", "shutdown"]):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

TOOL_NODE = ToolNode([bash])

# ── LLM ────────────────────────────────────────────────────────
LLM = ChatOpenAI(model=os.getenv("LLM_MODEL", "deepseek-chat"),
                 base_url=os.getenv("LLM_BASE_URL"),
                 api_key=os.getenv("OPENAI_API_KEY"), temperature=0.2)
LLM_TOOLS = LLM.bind_tools([bash])
SYS = "You are a coding agent. Use bash to solve tasks. Act, don't explain."

compactor = ContextCompactor(llm=LLM)

# ── Nodes ──────────────────────────────────────────────────────
def compact_node(state: AgentState) -> dict:
    msgs = state["messages"]
    if compactor.should_compact(msgs):
        c = compactor.compact(msgs)
        print(f"\n  compact: {len(msgs)} msgs -> {len(c)} msgs")
        return {"messages": c}
    return {}

def agent_node(state: AgentState) -> dict:
    resp = LLM_TOOLS.invoke([SystemMessage(content=SYS)] + state["messages"])
    return {"messages": [resp]}

def route(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if (hasattr(last, "tool_calls") and last.tool_calls) else END

# ── Graph ──────────────────────────────────────────────────────
# [entry]->[agent]->{tools?}->[tools]->[compact]->[agent]->...
#                          |                       ^
#                     {no}->END                    |
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.add_node("compact", compact_node)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
graph.add_edge("tools", "compact")
graph.add_edge("compact", "agent")
app = graph.compile()

# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"s09: Context Compaction | msg>{compactor.msg_n} tok>{compactor.tok_n:,}")
    print("Type 'q' to quit.\n")
    msgs = []
    while True:
        try:
            q = input("\033[36ms09 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if q.strip().lower() in ("q", "exit", ""):
            break
        msgs.append(HumanMessage(content=q))
        msgs = app.invoke({"messages": msgs})["messages"]
        last = msgs[-1]
        if hasattr(last, "content") and last.content:
            print(last.content)
        print()
