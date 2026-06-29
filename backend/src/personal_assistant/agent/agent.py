from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pathlib import Path

from personal_assistant.agent.approval import ApprovalGate
from personal_assistant.agent.harness import (
    _approval_route,
    _entry_route,
    _sanitize_messages_for_api,
    apply_pre_tool_guards,
    build_default_tool_middlewares,
)
from personal_assistant.agent.hook import AgentHookManager, HookStage, with_hooks
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import build_skill_router
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import LLMConfig
from personal_assistant.config import Settings
from personal_assistant.memory.compaction import ContextCompactor, TOOL_RESULT_REFERENCE_TEMPLATE
from personal_assistant.memory.long_term import LongTermMemoryStore
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry
from personal_assistant.tools import build_basic_tools


def compile_agent(
    settings: Settings,
    registry: SkillRegistry,
    memory: PostgresMemory,
    decisions: dict[str, bool],
    llm_config: LLMConfig | None = None,
    hook_manager: AgentHookManager | None = None,
):
    llm = build_llm(settings, llm_config)
    approval_gate = ApprovalGate(decisions)
    hooks = hook_manager or AgentHookManager()
    workspace = Path(getattr(settings, "assistant_workspace_dir", Path.cwd()))
    long_term_dir = getattr(settings, "long_term_memory_dir", None)
    long_term_memory = LongTermMemoryStore(
        Path(long_term_dir) if long_term_dir else workspace / ".memory"
    )
    basic_tools = build_basic_tools(
        workspace,
        long_term_memory=long_term_memory,
        postgres_memory=memory,
    )
    transcript_dir = getattr(settings, "transcript_dir", None)
    async def summarize_for_compaction(messages):
        response = await llm.ainvoke(_build_compaction_summary_messages(messages))
        return str(getattr(response, "content", "") or "")

    compactor = ContextCompactor(
        transcript_dir=Path(transcript_dir) if transcript_dir else workspace / ".transcripts",
        trigger_message_count=getattr(settings, "context_compaction_message_count", 20),
        token_threshold=getattr(settings, "context_compaction_token_threshold", 1_000_000),
        summarize=summarize_for_compaction,
    )
    tool_middlewares = build_default_tool_middlewares()

    async def call_agent(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        active_tools = _active_tools_for_state(
            registry,
            state.get("selected_skills", []),
            basic_tools,
        )
        messages = _sanitize_messages_for_api(state["messages"])
        response = await llm.bind_tools(active_tools).ainvoke(messages, config=config)
        return {"messages": [response]}

    async def reflect_memory(
        state: AgentState,
        config: RunnableConfig | None = None,
    ) -> AgentState:
        messages = state.get("messages", [])
        if _memory_save_already_requested(messages):
            return {}
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
            return {}
        save_tool = next(
            (tool for tool in basic_tools if tool.name == "save_conversation_memory"),
            None,
        )
        if save_tool is None:
            return {}
        response = await llm.bind_tools([save_tool]).ainvoke(
            _build_memory_reflection_messages(messages),
            config=config,
        )
        if getattr(response, "tool_calls", None):
            return {"messages": [response]}
        return {}

    async def compact_context(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        thread_id = _thread_id_from_config(config)
        messages = await compactor.acompact(
            state.get("messages", []),
            thread_id=thread_id,
            additional_turns=state.get("approval_turn_count", 0),
        )
        if messages == state.get("messages", []):
            return {}
        return _replace_messages_update(messages)

    async def execute_tools(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        active_tools = _active_tools_for_state(
            registry,
            state.get("selected_skills", []),
            basic_tools,
        )
        messages = state.get("messages", [])
        ai_message = _latest_ai_with_tool_calls(messages)
        if ai_message is None:
            return await ToolNode(active_tools).ainvoke(state, config=config)

        thread_id = None
        if isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                thread_id = configurable.get("thread_id")

        allowed_calls, blocked_messages = await apply_pre_tool_guards(
            ai_message.tool_calls,
            memory=memory,
            thread_id=thread_id,
            middlewares=tool_middlewares,
        )

        if not blocked_messages:
            result = await ToolNode(active_tools).ainvoke(state, config=config)
            await _record_tool_result_messages(
                memory,
                thread_id=thread_id,
                messages=result.get("messages", []),
                tool_calls=ai_message.tool_calls,
            )
            return result
        if not allowed_calls:
            await _record_tool_result_messages(
                memory,
                thread_id=thread_id,
                messages=blocked_messages,
                tool_calls=ai_message.tool_calls,
            )
            return {"messages": blocked_messages}

        guarded_state = dict(state)
        guarded_state["messages"] = [
            _replace_ai_tool_calls(message, allowed_calls) if message is ai_message else message
            for message in messages
        ]
        result = await ToolNode(active_tools).ainvoke(guarded_state, config=config)
        result_messages = [*blocked_messages, *result.get("messages", [])]
        await _record_tool_result_messages(
            memory,
            thread_id=thread_id,
            messages=result_messages,
            tool_calls=ai_message.tool_calls,
        )
        return {"messages": result_messages}

    async def inspect_approval(state: AgentState) -> AgentState:
        return approval_gate.inspect(state)

    graph = StateGraph(AgentState)
    graph.add_node(
        "route_skills",
        with_hooks(hooks, HookStage.ROUTE_SKILLS, build_skill_router(registry)),
    )
    graph.add_node(
        "compact_context",
        with_hooks(hooks, HookStage.COMPACT_CONTEXT, compact_context),
    )
    graph.add_node("agent", with_hooks(hooks, HookStage.AGENT, call_agent))
    graph.add_node(
        "memory_reflection",
        with_hooks(hooks, HookStage.MEMORY_REFLECTION, reflect_memory),
    )
    graph.add_node("approval", with_hooks(hooks, HookStage.APPROVAL, inspect_approval))
    graph.add_node("tools", with_hooks(hooks, HookStage.TOOLS, execute_tools))

    graph.set_conditional_entry_point(
        _entry_route,
        {
            "route_skills": "route_skills",
            "approval": "approval",
        },
    )
    graph.add_edge("route_skills", "compact_context")
    graph.add_edge("compact_context", "agent")
    graph.add_edge("agent", "memory_reflection")
    graph.add_edge("memory_reflection", "approval")
    graph.add_conditional_edges(
        "approval",
        _approval_route,
        {
            "wait": END,
            "tools": "tools",
            "agent": "agent",
            "end": END,
        },
    )
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=memory.checkpointer)


def _latest_ai_with_tool_calls(messages):
    for message in reversed(messages):
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            return message
    return None


def _active_tools_for_state(registry: SkillRegistry, selected_skills: list[str], basic_tools):
    tool_map = {tool.name: tool for tool in basic_tools}
    tool_map.update(registry.tool_map_for_skills(selected_skills))
    return list(tool_map.values())


def _replace_ai_tool_calls(message: AIMessage, tool_calls: list[dict]) -> AIMessage:
    return AIMessage(
        content=message.content,
        tool_calls=tool_calls,
        id=getattr(message, "id", None),
        name=getattr(message, "name", None),
    )


def _thread_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) else None


def _replace_messages_update(messages: list) -> AgentState:
    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]}


async def _record_tool_result_messages(
    memory,
    *,
    thread_id: str | None,
    messages: list,
    tool_calls: list[dict],
) -> None:
    record = getattr(memory, "record_tool_result", None)
    if not callable(record):
        return
    tool_names = {
        str(call.get("id")): str(call.get("name") or "tool")
        for call in tool_calls
    }
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_result_id = str(message.tool_call_id)
        try:
            await record(
                thread_id=thread_id,
                tool_result_id=tool_result_id,
                tool_name=tool_names.get(tool_result_id),
                content=str(message.content),
                metadata={"tool_call_id": tool_result_id},
            )
        except Exception:
            continue


def _memory_save_already_requested(messages: list) -> bool:
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == "save_conversation_memory":
                return True
    return False


def _build_memory_reflection_messages(messages: list) -> list:
    transcript = "\n".join(
        f"{getattr(message, 'type', message.__class__.__name__)}: {getattr(message, 'content', '')}"
        for message in messages[-20:]
    )
    return [
        SystemMessage(
            content=(
                "You decide whether the conversation contains durable memory worth saving. "
                "Call save_conversation_memory only for stable user preferences, system facts, "
                "project decisions, or reusable context. If not worth saving, respond with no tool call. "
                "When saving, write the body with these sections exactly:\n"
                "==当前目标==\n"
                "==重要发现 / 决策==\n"
                "==已读 / 已改的文件==\n"
                "==剩余工作==\n"
                "==用户约束=="
            )
        ),
        HumanMessage(content=f"Review this transcript for long-term memory:\n\n{transcript}"),
    ]


def _build_compaction_summary_messages(messages: list) -> list:
    transcript = "\n".join(
        _compaction_transcript_line(message)
        for message in messages
    )
    return [
        SystemMessage(
            content=(
                "Summarize this conversation for context compaction. Preserve the exact "
                "five headings and keep operational details concrete. Include file paths, "
                "decisions, constraints, and remaining work when present."
            )
        ),
        HumanMessage(
            content=(
                "Return a Chinese summary with these headings exactly:\n"
                "==当前目标==\n"
                "==重要发现 / 决策==\n"
                "==已读 / 已改的文件==\n"
                "==剩余工作==\n"
                "==用户约束==\n\n"
                f"Transcript:\n{transcript}"
            )
        ),
    ]


def _compaction_transcript_line(message) -> str:
    if isinstance(message, ToolMessage):
        content = TOOL_RESULT_REFERENCE_TEMPLATE.format(tool_result_id=message.tool_call_id)
    else:
        content = getattr(message, "content", "")
    return f"{getattr(message, 'type', message.__class__.__name__)}: {content}"
