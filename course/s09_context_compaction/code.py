#!/usr/bin/env python3
"""
s09_context_compaction.py -- Context Compaction

When conversation grows too long, compress it:
  1. Archive full transcript to .transcripts/thread_xxx.jsonl
  2. Generate an LLM summary of the entire conversation
  3. Replace middle messages with the summary, keeping first 2 + last 2

Reference: backend/src/personal_assistant/memory/compaction.py

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s09_context_compaction/code.py
"""
import json
import os
import subprocess
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

# ── Token estimation (simple: chars / 4) ──────────────────────
def estimate_tokens(messages: list[AnyMessage]) -> int:
    return sum(max(1, len(str(getattr(m, "content", ""))) // 4) for m in messages)


# ── ContextCompactor ───────────────────────────────────────────
COMPACTED_PREFIX = "[Compacted]"


class ContextCompactor:
    """Check thresholds, archive transcript, generate summary, replace middle."""

    def __init__(self, *, msg_threshold: int = 20, tok_threshold: int = 100_000,
                 transcript_dir: Path | None = None, llm: ChatOpenAI | None = None):
        self.msg_threshold = msg_threshold
        self.tok_threshold = tok_threshold
        self.transcript_dir = transcript_dir or Path(".transcripts")
        self.llm = llm

    def should_compact(self, messages: list[AnyMessage]) -> bool:
        return len(messages) >= self.msg_threshold or estimate_tokens(messages) >= self.tok_threshold

    def compact(self, messages: list[AnyMessage], *, thread_id: str = "") -> list[AnyMessage]:
        if not self.should_compact(messages):
            return list(messages)

        # 1. Archive full transcript
        archive = self._archive(messages, thread_id)

        # 2. Generate LLM summary
        summary = self._summarize(messages)

        # 3. Build compacted list: first human + first AI + summary + last AI
        preserved: list[AnyMessage] = []
        for m in messages:
            if isinstance(m, HumanMessage):
                preserved.append(m)
                break
        for m in messages:
            if isinstance(m, AIMessage):
                preserved.append(m)
                break
        preserved.append(HumanMessage(content=(
            f"{COMPACTED_PREFIX}\nTranscript: {archive}\n\n{summary}"
        )))
        last_ai = None
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        if last_ai:
            preserved.append(last_ai)
        return preserved

    def _archive(self, messages: list[AnyMessage], thread_id: str) -> Path:
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in (thread_id or "default"))
        path = self.transcript_dir / f"thread_{safe}_{ts}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps({
                    "type": m.__class__.__name__,
                    "content": getattr(m, "content", ""),
                }, ensure_ascii=False) + "\n")
        return path

    def _summarize(self, messages: list[AnyMessage]) -> str:
        prompt = (
            "Summarize this conversation (中文), covering:\n"
            "1) user goal  2) decisions  3) files touched\n"
            "4) remaining work  5) constraints\n\n"
            "=== Conversation ===\n"
        )
        lines = []
        for m in messages:
            c = str(getattr(m, "content", "") or "")
            lines.append(f"[{m.__class__.__name__}] {c[:3000]}")
        prompt += "\n\n".join(lines)

        if self.llm is None:
            return "Summary unavailable (no LLM). See transcript for full history."

        try:
            resp = self.llm.invoke([HumanMessage(content=prompt[:40000])])
            return str(resp.content).strip() or "Summary empty."
        except Exception:
            return "Summary failed. See transcript for details."


# ── State ──────────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Tool: bash ─────────────────────────────────────────────────
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


TOOLS = [bash]
TOOL_NODE = ToolNode(TOOLS)

# ── LLM ────────────────────────────────────────────────────────
LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)
LLM_WITH_TOOLS = LLM.bind_tools(TOOLS)
SYSTEM = "You are a coding agent. Use bash to solve tasks. Act, don't explain."

compactor = ContextCompactor(llm=LLM)


# ── Nodes ──────────────────────────────────────────────────────
def compact_context_node(state: AgentState) -> dict:
    """Check thresholds; if over, archive + summarize + replace middle."""
    msgs = state["messages"]
    if compactor.should_compact(msgs):
        compacted = compactor.compact(msgs)
        print(f"\n  compact_context: {len(msgs)} msgs -> {len(compacted)} msgs (summary)")
        return {"messages": compacted}
    return {}


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    resp = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [resp]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ── Build graph ────────────────────────────────────────────────
# [entry] -> [agent] -> {tool_calls?} -> [tools] -> [compact_context] -> [agent] ...
#                            |                                          ^
#                        {no calls} -> END                             |
#                            (compaction check before next LLM call)
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.add_node("compact_context", compact_context_node)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "compact_context")
graph.add_edge("compact_context", "agent")
app = graph.compile()


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    print("s09: Context Compaction")
    print(f"  msg threshold: {compactor.msg_threshold} | "
          f"tok threshold: {compactor.tok_threshold:,}")
    print("Type 'q' to quit.\n")

    messages = []
    while True:
        try:
            query = input("\033[36ms09 >> \033[0m")
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
