#!/usr/bin/env python3
"""
s04_skill_routing.py — 技能路由：regex 触发匹配

在 s01 的 StateGraph 中插入 route_skills 节点，放在 agent_node 之前。
路由节点根据 regex 触发词决定激活哪些技能，选中技能的指令被注入 system prompt。

Graph 流程：
    [START] → [route_skills] → [agent] → [tools] → [agent] → ...

与真实项目 router.py 的对应关系：
- _regex_route() → 本文件的 route_skills()
- build_system_prompt() → 本文件的 agent_node 内的 prompt 组装
- _DEFAULT_SKILL_REGEXES → 本文件的 SKILLS 中的 triggers 字段

Usage:
    pip install langgraph langchain-core langchain-openai python-dotenv
    OPENAI_API_KEY=... python s04_skill_routing/code.py
"""
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Annotated

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

# ── Skill data model ────────────────────────────────────────
# 简化版 Skill：name + description + instructions + triggers (regex 列表)


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    triggers: list[str] = field(default_factory=list)


# ── Skill registry (3 个示例技能) ───────────────────────────
# 每个技能声明一组 regex 触发词，route_skills 用它们匹配用户输入。
SKILLS: dict[str, Skill] = {
    "weather": Skill(
        name="weather",
        description="查询天气、预报、温度、降水信息",
        instructions=(
            "你可以使用 bash 工具调用天气 API 或查询在线天气信息。"
            "用户询问天气时，优先使用 curl 或 python 获取天气数据。"
        ),
        triggers=[
            r"\b(weather|forecast|temperature|rain|snow|wind|humidity)\b",
            r"(天气|气温|温度|下雨|下雪|刮风|预报|冷不冷|热不热|湿度)",
        ],
    ),
    "datetime": Skill(
        name="datetime",
        description="获取当前时间、日期、星期、时区转换",
        instructions=(
            "你可以使用 bash 工具执行系统命令获取时间。"
            "用户询问时间时，使用 date 命令获取当前时间信息。"
        ),
        triggers=[
            r"\b(today|tomorrow|yesterday|date|time|weekday|next week)\b",
            r"(今天|明天|后天|昨天|星期|周[一二三四五六日天]?|几点|日期|时间)",
        ],
    ),
    "file-search": Skill(
        name="file-search",
        description="在工作区中搜索文件：按名称、模式、内容查找",
        instructions=(
            "你可以使用 bash 工具执行 find 和 grep 搜索文件。"
            "用户要求搜索或查找文件时，先用 find 定位文件，再用 grep 搜索内容。"
        ),
        triggers=[
            r"\b(find|search|locate|grep|where is)\b",
            r"(找|搜|查找|搜索|在哪里|有没有.*文件)",
        ],
    ),
}

# ── State: 新增 selected_skills 字段 ────────────────────────
from typing import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    selected_skills: list[str]  # 本轮被 route_skills 选中的技能名


# ── Tool: bash ─────────────────────────────────────────────
@tool
def bash(command: str) -> str:
    """Run a shell command in the workspace."""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot"]
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

BASE_PROMPT = (
    "You are a personal assistant. Use bash to solve tasks. "
    "Additional capabilities come from the selected skills below. "
    "Use skill tools only when a selected skill makes them available. "
    "Act, don't explain."
)


# ── route_skills node: regex 触发匹配 ──────────────────────
# 这是本章的核心——对应 agent/router.py 的 _regex_route() 函数。
def route_skills(state: AgentState) -> dict:
    """从用户消息中提取文本，匹配所有技能的 regex 触发词。"""

    # 收集最近的 human 消息文本（最多 4000 字符）
    user_text = "\n".join(
        msg.content
        for msg in state["messages"]
        if getattr(msg, "type", "") == "human"
    )[-4000:]

    selected: list[str] = []
    for skill in SKILLS.values():
        for pattern in skill.triggers:
            try:
                if re.search(pattern, user_text, re.IGNORECASE):
                    selected.append(skill.name)
                    break  # 一个技能只需要匹配一次
            except re.error:
                continue

    if selected:
        print(f"[route_skills] matched: {', '.join(selected)}")
    else:
        print("[route_skills] no skill matched — agent uses base tools only")
    return {"selected_skills": selected}


# ── LLM factory (lazy init — avoids crash in demo mode) ─────
_LLM = None
_LLM_WITH_TOOLS = None


def _get_llm():
    global _LLM, _LLM_WITH_TOOLS
    if _LLM is None:
        _LLM = ChatOpenAI(
            model=os.getenv("LLM_MODEL", "deepseek-chat"),
            base_url=os.getenv("LLM_BASE_URL"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
        )
        _LLM_WITH_TOOLS = _LLM.bind_tools(TOOLS)
    return _LLM, _LLM_WITH_TOOLS


# ── agent_node: 注入选中技能的指令 ──────────────────────────
def agent_node(state: AgentState) -> dict:
    """构建系统提示，注入 route_skills 选中的技能指令。"""

    sections = [BASE_PROMPT]

    # 注入选中技能的元信息（简短摘要）
    selected = state.get("selected_skills", [])
    if selected:
        meta_lines = []
        for name in selected:
            skill = SKILLS.get(name)
            if skill:
                meta_lines.append(f"- **{skill.name}**: {skill.description}")
        sections.append("## Available Skills\n" + "\n".join(meta_lines))

    # 注入选中技能的完整指令
    for name in selected:
        skill = SKILLS.get(name)
        if skill and skill.instructions:
            sections.append(f"## Skill: {skill.name}\n{skill.instructions}")

    system = SystemMessage(content="\n\n".join(sections))
    messages = [system] + state["messages"]
    _, llm_with_tools = _get_llm()
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


# ── Router (条件边) ────────────────────────────────────────
def should_continue(state: AgentState) -> str:
    """LLM 想调工具 → tools 节点；否则 → END。"""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ── Build graph: route_skills → agent → tools → agent ──────
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("route_skills", route_skills)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.set_entry_point("route_skills")
    g.add_edge("route_skills", "agent")
    g.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    g.add_edge("tools", "agent")
    return g.compile()


# ── Demo mode ───────────────────────────────────────────────
if __name__ == "__main__":
    print("s04: Skill Routing — regex trigger matching")
    print("=" * 55)
    print("Demonstrating route_skills with sample prompts:\n")

    # 演示路由行为：每个样例只打印 route_skills 的匹配结果，不调用 LLM
    demos = [
        "今天天气怎么样？",
        "现在几点了？",
        "帮我找一下 README.md 在哪里",
        "告诉我时间和天气",
        "hello world",
        "What is the weather forecast for tomorrow?",
        "search for python files in the project",
        "帮我创建一个 hello.py",
    ]

    for prompt in demos:
        # 只用 route_skills 节点——不运行完整 graph，聚焦路由行为
        initial = {"messages": [HumanMessage(content=prompt)], "selected_skills": []}
        result = route_skills(initial)
        selected = result["selected_skills"]
        names = ", ".join(selected) if selected else "(none)"
        print(f"  Prompt: {prompt}")
        print(f"  Skills: {names}\n")

    print("─" * 55)
    print("To chat with the full agent, pass --chat:")
    print("  python s04_skill_routing/code.py --chat")

    import sys as _sys

    if "--chat" in _sys.argv:
        print("\nStarting chat mode...\n")
        app = build_graph()
        messages = []
        while True:
            try:
                query = input("s04 >> ")
            except (EOFError, KeyboardInterrupt):
                break
            if query.strip().lower() in ("q", "exit", ""):
                break
            messages.append(HumanMessage(content=query))
            result = app.invoke({"messages": messages, "selected_skills": []})
            messages = result["messages"]
            for msg in messages:
                if hasattr(msg, "content") and msg.content and msg.type == "ai":
                    print(msg.content)
                    break
            print()
