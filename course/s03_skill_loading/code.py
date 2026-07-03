#!/usr/bin/env python3
"""
s03_skill_loading.py — Skill Loading: YAML frontmatter + two-phase registry

  Phase 1 (cheap): 扫描 skills/ 目录，只解析 YAML frontmatter，构建技能索引。
  Phase 2 (on-demand): 技能被选中时，加载完整 SKILL.md 指令内容。

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv pyyaml
    OPENAI_API_KEY=... python s03_skill_loading/code.py

Reference source: backend/src/personal_assistant/skills/loader.py, skills/base.py
"""
from __future__ import annotations

import os, subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, TypedDict

import yaml
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)


# ── State ──────────────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


# ── Tool: bash ─────────────────────────────────────────────────────
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


# ── Skill dataclass ────────────────────────────────────────────────
@dataclass
class Skill:
    name: str
    description: str
    path: Path
    instructions_path: Path
    instructions: str | None = None
    triggers: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return self.instructions is not None


# ── SkillRegistry: two-phase loading ───────────────────────────────
class SkillRegistry:
    """两阶段技能加载器。
    Phase 1 — scan_metadata(): 只解析 YAML frontmatter，构建索引。
    Phase 2 — load_skill():    按需读取 SKILL.md 全文。
    """

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).resolve()
        self._skills: dict[str, Skill] = {}
        self.scan_metadata()

    @property
    def skills(self) -> dict[str, Skill]: return self._skills
    @property
    def skill_names(self) -> list[str]: return list(self._skills.keys())

    def scan_metadata(self) -> list[Skill]:
        """Phase 1: scan skills/ directories, parse YAML frontmatter only."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        loaded: dict[str, Skill] = {}
        for skill_dir in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            md = skill_dir / "SKILL.md"
            if not md.exists():
                continue
            meta = _parse_frontmatter(md)
            loaded[skill_dir.name] = Skill(
                name=meta.get("name", skill_dir.name),
                description=meta.get("description") or _first_heading(md),
                path=skill_dir,
                instructions_path=md,
                triggers=[str(t) for t in meta.get("triggers", []) if t],
            )
        self._skills = loaded
        return list(loaded.values())

    def load_skill(self, name: str) -> Skill:
        """Phase 2: load full SKILL.md instructions on demand."""
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"Unknown skill: {name}")
        if not skill.loaded:
            skill.instructions = skill.instructions_path.read_text(encoding="utf-8")
        return skill


# ── Frontmatter + heading helpers ──────────────────────────────────
def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter between two '---' delimiters."""
    try:
        with path.open(encoding="utf-8") as fh:
            if fh.readline().strip() != "---":
                return {}
            lines = []
            for line in fh:
                if line.strip() == "---":
                    break
                lines.append(line)
    except (OSError, UnicodeDecodeError):
        return {}
    data = yaml.safe_load("".join(lines))
    return data if isinstance(data, dict) else {}


def _first_heading(path: Path) -> str:
    """Extract first Markdown heading (after frontmatter) as fallback description."""
    try:
        with path.open(encoding="utf-8") as fh:
            in_fm = False
            for line in fh:
                s = line.strip()
                if s == "---":
                    in_fm = not in_fm; continue
                if in_fm: continue
                if s.startswith("#"):
                    return s.lstrip("#").strip()
    except (OSError, UnicodeDecodeError):
        pass
    return "No description"


# ── Init registry ──────────────────────────────────────────────────
SKILLS_DIR = Path(__file__).parent / "skills"
REGISTRY = SkillRegistry(SKILLS_DIR)


# ── Skill tool: expose load_skill to LLM ───────────────────────────
@tool
def load_skill(skill_name: str) -> str:
    """Load a skill's full instructions by name."""
    try:
        skill = REGISTRY.load_skill(skill_name)
        return (f"\n## 已激活技能: {skill.name}\n{skill.instructions}\n"
                if skill.instructions else f"Skill '{skill_name}' has no instructions.")
    except KeyError:
        return f"Unknown skill: {skill_name}. Available: {', '.join(REGISTRY.skill_names)}"


# ── LLM + Graph ────────────────────────────────────────────────────
LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)
ALL_TOOLS = [bash, load_skill]
LLM_WITH_TOOLS = LLM.bind_tools(ALL_TOOLS)


def _build_system() -> str:
    parts = [f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks."]
    if REGISTRY.skills:
        parts.append("\n## 可用技能")
        for s in REGISTRY.skills.values():
            trig = ", ".join(s.triggers[:5]) if s.triggers else "(无)"
            parts.append(f"- {s.name}: {s.description}  [{trig}]")
        parts.append("\n当用户问题匹配技能触发词时，先调 load_skill 加载该技能。")
    return "\n".join(parts)


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=_build_system())] + state["messages"]
    return {"messages": [LLM_WITH_TOOLS.invoke(messages)]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if (hasattr(last, "tool_calls") and last.tool_calls) else END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", ToolNode(ALL_TOOLS))
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()


# ── Entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("s03: Skill Loading — YAML frontmatter + two-phase registry")
    print(f"Skills dir: {SKILLS_DIR}")
    n = len(REGISTRY.skills)
    names = ', '.join(REGISTRY.skill_names) or '(none)'
    print(f"Found {n} skill(s): {names}")
    print("输入问题，回车发送。输入 q 退出。\n")

    messages = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
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
