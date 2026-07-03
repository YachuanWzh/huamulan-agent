#!/usr/bin/env python3
"""
s16_comprehensive.py — 综合 Agent：全部 15 个机制组装为一个完整 Agent Harness

Graph（7 节点，与 agent/agent.py compile_agent 对齐）:
  entry → route_skills → compact_context → agent → memory_reflection → approval → tools → agent

机制: s01(StateGraph) s02(ToolNode) s03(registry) s04(route) s05(scripts)
      s06(approval) s07(guards+middleware) s08(hooks) s09(compact) s10(memory)
      s11(checkpoint) s12(cache) s13(CLI) s15(logger)

参考: backend/src/personal_assistant/agent/harness.py (AgentHarness)
      backend/src/personal_assistant/agent/agent.py (compile_agent)

Usage: pip install langgraph langchain-core langchain-openai python-dotenv pyyaml
       OPENAI_API_KEY=... python s16_comprehensive/code.py
"""
import json, os, re, subprocess, time, uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

import yaml
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool, StructuredTool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)
WORKSPACE = Path(os.getcwd())
SKILLS_DIR = Path(__file__).parent / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# ── s12: SimpleCache ───────────────────────────────────────────
class SimpleCache:
    def __init__(self): self._s: dict[str, tuple[Any, float]] = {}
    def get(self, k: str) -> Any | None:
        e = self._s.get(k)
        if e is None: return None
        v, exp = e
        if time.monotonic() >= exp: del self._s[k]; return None
        return v
    def set(self, k: str, v: Any, ttl: int = 60) -> None:
        self._s[k] = (v, time.monotonic() + ttl)

# ── s15: ExecutionLogger ───────────────────────────────────────
class ExecutionLogger:
    def __init__(self): self.recs: list[dict] = []
    def log(self, ty: str, st: str, nm: str, dur: int = 0, meta: dict | None = None):
        self.recs.append({"type": ty, "status": st, "name": nm, "dur_ms": dur,
            "meta": meta or {}, "ts": time.strftime("%H:%M:%S")})
    def summary(self) -> str:
        if not self.recs: return "(no events)"
        return "\n".join(f"  [{r['ts']}] {r['type']}/{r['name']}: {r['status']} ({r['dur_ms']}ms)"
                         for r in self.recs)

# ── s10: LongTermMemoryStore (JSON file, simplified) ───────────
class LongTermMemoryStore:
    def __init__(self, path: Path): self.p = path; self.p.mkdir(parents=True, exist_ok=True)
    def save(self, tid: str, summary: str):
        f = self.p / f"{tid}.json"
        xs = json.loads(f.read_text()) if f.exists() else []
        xs.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "summary": summary})
        f.write_text(json.dumps(xs, ensure_ascii=False, indent=2))
    def load(self, tid: str) -> list[dict]:
        f = self.p / f"{tid}.json"
        return json.loads(f.read_text()) if f.exists() else []

# ── s03+s04: Skill registry + trigger routing ──────────────────
@dataclass
class Skill:
    name: str; description: str; path: Path
    triggers: list[str] = field(default_factory=list)
    scripts: list[dict] = field(default_factory=list)
    instructions: str | None = None
    @property
    def loaded(self) -> bool: return self.instructions is not None

class SkillRegistry:
    def __init__(self, d: Path): self.d = d; self._sk: dict[str, Skill] = {}; self.scan()
    @property
    def skills(self) -> dict[str, Skill]: return self._sk
    def scan(self):
        for sd in sorted(p for p in self.d.iterdir() if p.is_dir()):
            md = sd / "SKILL.md"
            if not md.exists(): continue
            m = self._fm(md)
            self._sk[sd.name] = Skill(name=m.get("name", sd.name),
                description=m.get("description", ""), path=sd,
                triggers=[str(t) for t in m.get("triggers", []) if t],
                scripts=m.get("scripts") or [])
    def load_skill(self, name: str) -> Skill:
        s = self._sk.get(name)
        if s is None: raise KeyError(name)
        if not s.loaded: s.instructions = (s.path / "SKILL.md").read_text(encoding="utf-8")
        return s
    def route(self, msg: str) -> str | None:
        ml = msg.lower()
        for sk in self._sk.values():
            for p in sk.triggers:
                try:
                    if re.search(p, ml): return sk.name
                except re.error: continue
        return None
    @staticmethod
    def _fm(p: Path) -> dict:
        try:
            t = p.read_text(encoding="utf-8")
            if t.startswith("---"):
                ps = t.split("---", 2)
                if len(ps) >= 3:
                    d = yaml.safe_load(ps[1])
                    return d if isinstance(d, dict) else {}
        except Exception: pass
        return {}

_registry = SkillRegistry(SKILLS_DIR)

# ── s05: Script tools ──────────────────────────────────────────
def resolve_script_tools(skill: Skill) -> list[StructuredTool]:
    py = skill.path / "skill.py"
    if not py.exists() or not skill.scripts: return []
    import importlib.util, sys
    mn = f"s16_{skill.name}"
    sp = importlib.util.spec_from_file_location(mn, py)
    if sp is None or sp.loader is None: return []
    mod = importlib.util.module_from_spec(sp); sys.modules[mn] = mod; sp.loader.exec_module(mod)
    tools = []
    for sc in skill.scripts:
        fn = getattr(mod, sc["name"], None)
        if fn: tools.append(StructuredTool.from_function(func=fn, name=sc["name"],
            description=sc.get("description", fn.__doc__ or "")))
    return tools

# ── s08: Hook system ───────────────────────────────────────────
class HookStage(str, Enum):
    ROUTE = "route"; COMPACT = "compact"; AGENT = "agent"
    MEMORY = "memory"; APPROVAL = "approval"; TOOLS = "tools"

class AgentHookManager:
    def __init__(self): self._h: dict[HookStage, list[Callable]] = defaultdict(list)
    def reg(self, s: HookStage, cb: Callable): self._h[s].append(cb)
    def run(self, s: HookStage, ctx: dict):
        for h in self._h.get(s, []):
            try: h(ctx)
            except Exception: pass

def _wrap(node_fn, hooks, stage):
    def w(state, *a, **kw):
        t0 = time.perf_counter()
        try:
            r = node_fn(state, *a, **kw)
            hooks.run(stage, {"s": stage.value, "e": time.perf_counter() - t0, "ok": True})
            return r
        except Exception as ex:
            hooks.run(stage, {"s": stage.value, "e": time.perf_counter() - t0, "err": str(ex)})
            raise
    return w

# ── s07: Guards + Middleware ────────────────────────────────────
PG = [("inject", r"(?is)\b(?:forget|ignore|disregard)\b.{0,20}\b(?:instructions?|rules?)\b"),
      ("leak", r"(?is)\b(?:output|print|show|reveal)\b.{0,20}\b(?:system|developer).{0,10}\bprompt\b")]
TG = [("sudo", r"(?is)\b(?:sudo|su|doas)\b"),
      ("rm", r"(?is)\b(?:rm\s+-rf|del\s+/[fs])")]

@dataclass
class CallLimitMW:
    max: int = 20; _n: int = 0
    def check(self, c: dict) -> ToolMessage | None:
        self._n += 1
        if self._n > self.max:
            return ToolMessage(tool_call_id=c.get("id", ""),
                content=f"CallLimit: max {self.max} calls exceeded.")

def scan_prompt(msg: str) -> str | None:
    for cat, pat in PG:
        if re.search(pat, msg): return f"PromptGuard({cat})"
    return None

def scan_tool(name: str, args: dict) -> str | None:
    h = f"{name}\n{json.dumps(args, ensure_ascii=False)}"
    for cat, pat in TG:
        if re.search(pat, h): return f"ToolGuard({cat})"
    return None

# ── s09: ContextCompactor ──────────────────────────────────────
class ContextCompactor:
    def __init__(self, mt: int = 20, tt: int = 100_000):
        self.mt = mt; self.tt = tt
    def should(self, msgs: list) -> bool:
        t = sum(max(1, len(str(getattr(m, "content", ""))) // 4) for m in msgs)
        return len(msgs) >= self.mt or t >= self.tt
    def compact(self, msgs: list) -> list:
        if not self.should(msgs) or len(msgs) <= 8: return list(msgs)
        parts = []
        for m in msgs[:len(msgs)//2]:
            c = str(getattr(m, "content", ""))[:80]
            if c.strip(): parts.append(c.strip()[:60])
        return [msgs[0], HumanMessage(content="[Compacted] " + "; ".join(parts))] + list(msgs[-3:])

# ── s06: ApprovalGate ──────────────────────────────────────────
RO = {"read_file", "list_directory", "load_skill", "save_conversation_memory"}

def _approval_node_fn(decisions: dict[str, bool]):
    def f(state: dict) -> dict:
        ms = state.get("messages", []); la = ms[-1] if ms else None
        if not isinstance(la, AIMessage) or not getattr(la, "tool_calls", None):
            return {"pending_approvals": []}
        ans = {m.tool_call_id for m in ms if isinstance(m, ToolMessage)}
        pen, den = [], []
        for tc in la.tool_calls:
            if tc["name"] in RO: continue
            d = decisions.get(tc["id"])
            if d is None:
                pen.append({"approval_id": tc["id"], "tool_call_id": tc["id"],
                    "name": tc["name"], "args": tc.get("args", {})})
            elif d is False and tc["id"] not in ans:
                den.append(ToolMessage(tool_call_id=tc["id"], content="Denied."))
        r: dict = {"pending_approvals": pen}
        if den: r["messages"] = den
        return r
    return f

def _approval_route(state: dict) -> str:
    if state.get("pending_approvals"): return "wait"
    ms = state.get("messages", [])
    ans = {m.tool_call_id for m in ms if isinstance(m, ToolMessage)}
    for m in reversed(ms):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            return "tools" if any(tc["id"] not in ans for tc in m.tool_calls) else END
    return END

# ── s01+s02: Basic tools ───────────────────────────────────────
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    if any(d in command for d in ("rm -rf /", "sudo", "shutdown", "reboot")):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        return ((r.stdout + r.stderr).strip() or "(no output)")[:50000]
    except subprocess.TimeoutExpired: return "Error: Timeout"
    except (FileNotFoundError, OSError) as e: return f"Error: {e}"

@tool
def read_file(path: str) -> str:
    """Read a text file from the workspace."""
    try: return (WORKSPACE / path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError: return f"File not found: {path}"
    except OSError as e: return f"Error: {e}"

@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file. Requires user approval."""
    try:
        f = WORKSPACE / path; f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8"); return f"Wrote {f.stat().st_size} bytes to {path}"
    except OSError as e: return f"Error: {e}"

@tool
def save_conversation_memory(memory_key: str, body: str) -> str:
    """Save a durable memory (preference, decision, fact) for future sessions."""
    return f"Memory saved: {memory_key}."

BASE = [bash, read_file, write_file, save_conversation_memory]

# ── s11: Checkpoint ────────────────────────────────────────────
chk = MemorySaver()

# ── State ──────────────────────────────────────────────────────
class State(TypedDict):
    messages: Annotated[list, add_messages]
    selected_skills: list[str]
    pending_approvals: list[dict]
    approval_turn_count: int

# ── LLM ────────────────────────────────────────────────────────
LLM = ChatOpenAI(model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("OPENAI_API_KEY"), temperature=0.2)
SYS = f"You are a coding agent at {WORKSPACE}. Use tools. Act, don't explain."

# ═══════════════════════════════════════════════════════════════
# build_agent_harness — all 7 nodes, 15 mechanisms
# ═══════════════════════════════════════════════════════════════
def build_agent_harness(reg: SkillRegistry = _registry,
                        decisions: dict[str, bool] | None = None):
    if decisions is None: decisions = {}
    cache = SimpleCache()
    logr = ExecutionLogger()
    ltm = LongTermMemoryStore(WORKSPACE / ".memory")
    comp = ContextCompactor()
    hm = AgentHookManager()
    mw = CallLimitMW()

    def hlog(ctx):
        s = ctx.get("s", "?"); e = ctx.get("e", 0)
        ok = "ok" if ctx.get("ok") else f"ERR:{ctx.get('err','')}"
        print(f"  [Hook:{s}] {ok}  {e*1000:.0f}ms")
    for st in HookStage: hm.reg(st, hlog)

    # ── route_skills ──
    def route_node(state: State) -> dict:
        ms = state.get("messages", [])
        if not ms: return {"selected_skills": []}
        c = str(getattr(ms[-1], "content", ""))
        ck = f"rt:{c[:80]}"
        cv = cache.get(ck)
        if cv is not None: return {"selected_skills": cv}
        sn = reg.route(c); sel = [sn] if sn else []
        ins = ""
        if sn:
            try: sk = reg.load_skill(sn); ins = f"\n## Skill: {sk.name}\n{sk.instructions}\n"
            except KeyError: pass
        cache.set(ck, sel, ttl=60)
        logr.log("route", "ok", "route_skills", meta={"in": c[:50], "sel": sel})
        r: dict = {"selected_skills": sel}
        if ins: r["messages"] = [HumanMessage(content=f"[Skill: {sn}]\n{ins}")]
        return r

    # ── compact_context ──
    def compact_node(state: State) -> dict:
        ms = state.get("messages", [])
        cc = comp.compact(list(ms))
        if cc != list(ms):
            logr.log("compact", "ok", "compact_context", meta={"bf": len(ms), "af": len(cc)})
            return {"messages": cc}
        return {}

    # ── agent ──
    def agent_node(state: State) -> dict:
        sel = state.get("selected_skills", [])
        tools = list(BASE)
        for sn in sel:
            try: tools.extend(resolve_script_tools(reg.load_skill(sn)))
            except KeyError: pass
        parts = [SYS]
        if reg.skills:
            parts.append("\n## Skills")
            for s in reg.skills.values():
                tr = ", ".join(s.triggers[:5]) if s.triggers else "-"
                parts.append(f"- {s.name}: {s.description} [{tr}]")
        for sn in sel:
            try:
                sk = reg.load_skill(sn)
                if sk.instructions: parts.append(f"\n## Active: {sn}\n{sk.instructions}")
            except KeyError: pass
        t0 = time.perf_counter()
        r = LLM.bind_tools(tools).invoke(
            [SystemMessage(content="\n".join(parts))] + list(state["messages"]))
        logr.log("llm", "ok", "agent", dur=int((time.perf_counter() - t0) * 1000))
        return {"messages": [r]}

    # ── memory_reflection ──
    def memory_node(state: State) -> dict:
        ms = state.get("messages", [])
        la = ms[-1] if ms else None
        if not isinstance(la, AIMessage) or getattr(la, "tool_calls", None): return {}
        ct = str(getattr(la, "content", ""))
        if len(ct) > 50:
            ltm.save("s16-session", ct[:200] + ("..." if len(ct) > 200 else ""))
            logr.log("memory", "ok", "memory_reflection", meta={"len": len(ct)})
        return {}

    # ── approval ──
    afn = _approval_node_fn(decisions)

    # ── tools ──
    def tools_node(state: State) -> dict:
        ms = state.get("messages", [])
        la = ms[-1] if ms else None
        if not isinstance(la, AIMessage) or not getattr(la, "tool_calls", None): return {}
        blk, alw = [], []
        for tc in la.tool_calls:
            ca = {"id": tc["id"], "name": tc["name"], "args": tc["args"]}
            g = scan_tool(tc["name"], tc.get("args", {}))
            if g:
                blk.append(ToolMessage(tool_call_id=tc["id"], content=f"Security: {g}"))
                logr.log("sec", "blocked", tc["name"], meta={"reason": g}); continue
            mm = mw.check(ca)
            if mm: blk.append(mm); continue
            alw.append(tc)
        if not alw: return {"messages": blk}
        tools = list(BASE)
        for sn in state.get("selected_skills", []):
            try: tools.extend(resolve_script_tools(reg.load_skill(sn)))
            except KeyError: pass
        pa = AIMessage(content="", tool_calls=alw)
        rr = ToolNode(tools).invoke({"messages": ms + [pa]})
        tms = [m for m in rr.get("messages", [])]
        for tm in tms:
            logr.log("tool", "ok", getattr(tm, "name", "?"),
                     meta={"ct": str(getattr(tm, "content", ""))[:80]})
        return {"messages": [*blk, *tms]}

    # ── entry_route ──
    def entry_r(state: State) -> str:
        ms = state.get("messages", [])
        for m in reversed(ms):
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                ans = {tm.tool_call_id for tm in ms if isinstance(tm, ToolMessage)}
                if any(tc["id"] not in ans for tc in m.tool_calls): return "approval"
                break
        return "route_skills"

    # ── Graph ──
    g = StateGraph(State)
    g.add_node("route_skills", _wrap(route_node, hm, HookStage.ROUTE))
    g.add_node("compact_context", _wrap(compact_node, hm, HookStage.COMPACT))
    g.add_node("agent", _wrap(agent_node, hm, HookStage.AGENT))
    g.add_node("memory_reflection", _wrap(memory_node, hm, HookStage.MEMORY))
    g.add_node("approval", _wrap(afn, hm, HookStage.APPROVAL))
    g.add_node("tools", _wrap(tools_node, hm, HookStage.TOOLS))
    g.set_conditional_entry_point(entry_r, {"route_skills": "route_skills", "approval": "approval"})
    g.add_edge("route_skills", "compact_context")
    g.add_edge("compact_context", "agent")
    g.add_edge("agent", "memory_reflection")
    g.add_edge("memory_reflection", "approval")
    g.add_conditional_edges("approval", _approval_route, {"tools": "tools", "wait": END, END: END})
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=chk), logr, ltm, decisions


# ═══════════════════════════════════════════════════════════════
# REPL — interactive loop with approval
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("s16: Comprehensive Agent — all 15 mechanisms together")
    print("=" * 60)
    print("Graph: entry → route_skills → compact → agent → memory → approval → tools → agent")
    n = len(_registry.skills)
    print(f"Skills: {n} | Commands: <anything>=agent  q/exit=quit  log=events\n")

    decisions: dict[str, bool] = {}
    app, execlog, ltm, _ = build_agent_harness(decisions=decisions)
    cfg = {"configurable": {"thread_id": "s16-session"}}
    msgs = []
    while True:
        try: q = input("\033[36ms16 >> \033[0m")
        except (EOFError, KeyboardInterrupt): break
        if q.strip().lower() in ("q", "exit", ""): break
        if q.strip().lower() == "log":
            print(f"\n--- Log ---\n{execlog.summary()}\n"); continue
        g = scan_prompt(q)
        if g: print(f"\033[31m[GUARD] {g}\033[0m\n"); continue
        msgs.append(HumanMessage(content=q))
        while True:
            r = app.invoke({"messages": msgs, "selected_skills": [],
                "pending_approvals": [], "approval_turn_count": 0}, cfg)
            msgs = list(r["messages"])
            pen = r.get("pending_approvals") or []
            if not pen: break
            print(f"\n{'='*45}\nAPPROVAL:")
            for i, p in enumerate(pen): print(f"  [{i}] {p['name']}({p.get('args', {})})")
            print("=" * 45)
            ch = input("Approve? [y/n]: ").strip().lower()
            for p in pen: decisions[p["approval_id"]] = ch in ("y", "yes")
            r2 = app.invoke({"messages": msgs, "selected_skills": [],
                "pending_approvals": [], "approval_turn_count": 1}, cfg)
            msgs = list(r2["messages"])
        la = msgs[-1] if msgs else None
        if la and hasattr(la, "content") and la.content: print(la.content)
        print()

    print(f"\n--- Summary ---\n{execlog.summary()}")
    mems = ltm.load("s16-session")
    if mems:
        print(f"\n--- Memory ({len(mems)}) ---")
        for m in mems: print(f"  [{m['ts']}] {m['summary'][:80]}")
    print("Done.\n")
