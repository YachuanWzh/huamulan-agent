#!/usr/bin/env python3
"""
s05_script_tools.py — Script Tools: 从 SKILL.md 声明到 LangChain Tool

模式："声明在 YAML，实现在 Python，绑定在运行时"。

每个 skill 的 SKILL.md frontmatter 可以声明 `scripts` 字段——描述工具的名称、
描述和参数。对应实现在同目录的 skill.py 中。resolve 时，harness 动态导入
skill.py，找到声明的函数，包装为 LangChain Tool，绑定到 LLM。

对比 s03（静态加载）和 s04（路由选择）：s03/s04 的 skill 只提供文本注入 system
prompt。s05 让 skill 可以带自己的工具——skill 不再只是"一段指令"，而是一个
完整的能力包（文档 + 触发器 + 工具代码）。

真实源码参考: backend/src/personal_assistant/skills/script_tool.py

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv pyyaml
    OPENAI_API_KEY=... python s05_script_tools/code.py
"""
import os
import sys
import importlib.util
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated

load_dotenv(override=True)

# Ensure skills/ is importable from the course root
SKILLS_ROOT = Path(__file__).parent / "skills"


# ── 1. Parse YAML frontmatter ────────────────────────────────

def parse_skill_md(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns all top-level keys declared between the ``---`` fences.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    return {}


# ── 2. create_script_tool 工厂 ────────────────────────────────

def create_script_tool(
    name: str,
    description: str,
    func: callable,
    parameters: dict[str, Any] | None = None,
) -> StructuredTool:
    """Wrap a Python function as a LangChain StructuredTool.

    ``parameters`` dict maps param_name -> {type, description, required?, default?}.
    When omitted, the tool's args_schema is inferred from ``func``'s type hints by
    Pydantic, then enriched with the frontmatter metadata via the tool's description.
    """
    return StructuredTool.from_function(
        func=func,
        name=name,
        description=description,
    )


# ── 3. Resolve a skill directory ─────────────────────────────

def resolve_skill(skill_dir: Path) -> list[StructuredTool]:
    """Resolve one skill: parse SKILL.md, import skill.py, build tools.

    Steps:
    1. Read SKILL.md frontmatter to get skill metadata and script declarations.
    2. Dynamically import ``skill.py`` from the skill directory.
    3. For each ``scripts`` entry, find the matching function in the module.
    4. Wrap each matched function via ``create_script_tool``.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return []

    meta = parse_skill_md(skill_md)
    scripts = meta.get("scripts") or []
    if not scripts:
        return []

    # Dynamically load skill.py
    skill_py = skill_dir / "skill.py"
    if not skill_py.exists():
        return []

    module_name = f"skills.{skill_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, skill_py)
    if spec is None or spec.loader is None:
        return []

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # Match script declarations to Python functions
    tools: list[StructuredTool] = []
    for script in scripts:
        func_name = script["name"]
        func = getattr(module, func_name, None)
        if func is None:
            print(f"  [WARN] Function '{func_name}' not found in {skill_py}")
            continue
        tool = create_script_tool(
            name=script["name"],
            description=script.get("description", func.__doc__ or ""),
            func=func,
            parameters=script.get("parameters"),
        )
        tools.append(tool)

    return tools


# ── 4. Resolve all skills ────────────────────────────────────

def resolve_all_skills() -> list[StructuredTool]:
    """Discover and resolve all skill directories under skills/."""
    all_tools: list[StructuredTool] = []
    for item in sorted(SKILLS_ROOT.iterdir()):
        if item.is_dir() and (item / "SKILL.md").exists():
            print(f"Resolving skill: {item.name}")
            tools = resolve_skill(item)
            all_tools.extend(tools)
            for t in tools:
                print(f"  + {t.name}")
    return all_tools


# ── 5. Assemble tools and agent ───────────────────────────────

# Script tools resolved from skills/
SCRIPT_TOOLS = resolve_all_skills()

# LangChain prebuilt ToolNode handles tool execution
TOOL_NODE = ToolNode(SCRIPT_TOOLS)

LLM = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0.2,
)
LLM_WITH_TOOLS = LLM.bind_tools(SCRIPT_TOOLS)

SYSTEM = (
    "You are a helpful assistant. "
    "Use available tools to answer user questions. "
    "Respond in Chinese when the user asks in Chinese."
)


# ── 6. StateGraph ───────────────────────────────────────────—

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def agent_node(state: AgentState) -> dict:
    messages = [SystemMessage(content=SYSTEM)] + state["messages"]
    response = LLM_WITH_TOOLS.invoke(messages)
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("tools", TOOL_NODE)
graph.set_entry_point("agent")
graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
graph.add_edge("tools", "agent")
app = graph.compile()


# ── 7. CLI ──────────────────────────────────────────────────

if __name__ == "__main__":
    print("s05: Script Tools — YAML 声明 → Python 实现 → LangChain Tool")
    print(f"Loaded {len(SCRIPT_TOOLS)} script tools.\n")
    print("输入问题，回车发送。输入 q 退出。\n")

    messages = []
    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
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
