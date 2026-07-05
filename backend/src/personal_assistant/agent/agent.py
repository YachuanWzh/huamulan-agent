import asyncio
import json
import logging
import time
from langgraph.graph import END, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from pathlib import Path
from typing import Any

from personal_assistant.agent.approval import ApprovalGate, RequiresApproval, requires_tool_approval
from personal_assistant.agent.harness import (
    _approval_route,
    _entry_route,
    _sanitize_messages_for_api,
    apply_pre_tool_guards,
    build_default_tool_middlewares,
)
from personal_assistant.agent.hook import AgentHookManager, HookStage, with_hooks
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import (
    InMemorySkillVectorIndex,
    OllamaBgeM3EmbeddingProvider,
    OllamaBgeM3Reranker,
    QdrantSkillVectorIndex,
    build_skill_router,
)
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import ExecutionLogCreate, LLMConfig
from personal_assistant.config import Settings
from personal_assistant.memory.compaction import ContextCompactor, TOOL_RESULT_REFERENCE_TEMPLATE
from personal_assistant.memory.long_term import LongTermMemoryStore
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry
from personal_assistant.tools import build_basic_tools

logger = logging.getLogger(__name__)


def build_skill_vector_index(settings: Settings):
    """Build skill semantic vector index from settings."""
    embedding_provider = OllamaBgeM3EmbeddingProvider(
        base_url=settings.skill_routing_ollama_base_url,
        model=settings.skill_routing_embedding_model,
    )
    if settings.skill_routing_vector_store == "qdrant" and settings.skill_routing_qdrant_url:
        return QdrantSkillVectorIndex(
            embedding_provider,
            url=settings.skill_routing_qdrant_url,
            collection=settings.skill_routing_qdrant_collection,
            api_key=settings.skill_routing_qdrant_api_key,
        )
    return InMemorySkillVectorIndex(embedding_provider)


def build_skill_reranker(settings: Settings):
    """Build skill reranker from settings if enabled."""
    if not getattr(settings, "skill_routing_rerank_enabled", False):
        return None
    return OllamaBgeM3Reranker(
        base_url=settings.skill_routing_ollama_base_url,
        model=settings.skill_routing_rerank_model,
    )


def build_skill_routing_llm(settings: Settings, llm_config: LLMConfig | None = None):
    """Build LLM instance for skill routing judgment."""
    routing_model = getattr(settings, "skill_routing_llm_model", None)
    if not routing_model:
        return build_llm(settings, llm_config)
    base_config = llm_config or LLMConfig()
    routing_config = LLMConfig(
        base_url=base_config.base_url,
        api_key=base_config.api_key,
        model=routing_model,
        temperature=base_config.temperature,
    )
    return build_llm(settings, routing_config)


def build_skill_router_components(
    settings: Settings,
    llm_config: LLMConfig | None = None,
    long_term_memory: LongTermMemoryStore | None = None,
    cache=None,
) -> dict[str, Any]:
    """Build all components needed for the full three-layer skill routing funnel."""
    router_kwargs: dict[str, Any] = {"long_term_memory": long_term_memory}
    if getattr(settings, "skill_routing_semantic_enabled", False):
        router_kwargs.update(
            {
                "semantic_index": build_skill_vector_index(settings),
                "reranker": build_skill_reranker(settings),
                "llm": build_skill_routing_llm(settings, llm_config),
                "semantic_threshold": settings.skill_routing_similarity_threshold,
                "semantic_top_k": settings.skill_routing_top_k,
                "rerank_threshold": settings.skill_routing_rerank_threshold,
                "rerank_top_k": settings.skill_routing_rerank_top_k,
                "llm_retry_count": settings.skill_routing_llm_retry_count,
            }
        )
    if cache is not None:
        router_kwargs.update(
            {
                "cache": cache,
                "memory_cache_ttl_seconds": getattr(settings, "cache_memory_ttl_seconds", 60),
            }
        )
    return router_kwargs


def compile_agent(
    settings: Settings,
    registry: SkillRegistry,
    memory: PostgresMemory,
    decisions: dict[str, bool],
    llm_config: LLMConfig | None = None,
    hook_manager: AgentHookManager | None = None,
    enable_memory_reflection: bool = True,
    cache=None,
    requires_approval: RequiresApproval | None = None,
):
    llm = build_llm(settings, llm_config)
    approval_callback: RequiresApproval = requires_approval or requires_tool_approval
    approval_gate = ApprovalGate(decisions, requires_approval=approval_callback)
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
        thread_id = _thread_id_from_config(config)
        started = time.perf_counter()
        response = await llm.bind_tools(active_tools).ainvoke(messages, config=config)
        await _record_execution_log(
            memory,
            ExecutionLogCreate(
                thread_id=thread_id or "",
                event_type="llm",
                status="completed",
                name="agent",
                input={"message_count": len(messages), "tool_count": len(active_tools)},
                output={"content": str(getattr(response, "content", ""))[:1000]},
                duration_ms=int((time.perf_counter() - started) * 1000),
                token_usage=_extract_token_usage(response),
                metadata={
                    "selected_skills": state.get("selected_skills", []),
                    "routing_trace": state.get("routing_trace", []),
                },
            ),
        )
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
            approval_decisions=decisions,
        )

        if not blocked_messages:
            result = {
                "messages": await _execute_tool_calls_with_retry(
                    active_tools,
                    ai_message.tool_calls,
                    config=config,
                    memory=memory,
                    thread_id=thread_id,
                )
            }
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

        result = {
            "messages": await _execute_tool_calls_with_retry(
                active_tools,
                allowed_calls,
                config=config,
                memory=memory,
                thread_id=thread_id,
            )
        }
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
    router_kwargs = build_skill_router_components(
        settings,
        llm_config=llm_config,
        long_term_memory=long_term_memory,
        cache=cache,
    )
    graph.add_node(
        "route_skills",
        with_hooks(
            hooks,
            HookStage.ROUTE_SKILLS,
            build_skill_router(registry, **router_kwargs),
        ),
    )
    graph.add_node(
        "compact_context",
        with_hooks(hooks, HookStage.COMPACT_CONTEXT, compact_context),
    )
    graph.add_node("agent", with_hooks(hooks, HookStage.AGENT, call_agent))
    if enable_memory_reflection:
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
    if enable_memory_reflection:
        graph.add_edge("agent", "memory_reflection")
        graph.add_edge("memory_reflection", "approval")
    else:
        graph.add_conditional_edges(
            "agent",
            _agent_route_without_memory_reflection,
            {
                "approval": "approval",
                "end": END,
            },
        )
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


async def warmup_skill_routing(settings: Settings, registry: SkillRegistry, semantic_index=None) -> None:
    """Warmup skill routing semantic index embeddings."""
    if not getattr(settings, "skill_routing_semantic_enabled", False):
        return
    if semantic_index is None:
        semantic_index = build_skill_vector_index(settings)
    try:
        await semantic_index.warmup(registry)
    except Exception:
        logger.exception("Skill routing vector warmup failed")


async def build_memory_reflection_update(
    settings: Settings,
    registry: SkillRegistry,
    memory: PostgresMemory,
    decisions: dict[str, bool],
    messages: list,
    llm_config: LLMConfig | None = None,
    config: RunnableConfig | None = None,
) -> AgentState:
    llm = build_llm(settings, llm_config)
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
    save_tool = next(
        (tool for tool in basic_tools if tool.name == "save_conversation_memory"),
        None,
    )
    if save_tool is None or _memory_save_already_requested(messages):
        return {}
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage) or getattr(last, "tool_calls", None):
        return {}
    response = await llm.bind_tools([save_tool]).ainvoke(
        _build_memory_reflection_messages(messages),
        config=config,
    )
    if not getattr(response, "tool_calls", None):
        return {}
    approval_update = ApprovalGate(
        decisions,
        requires_approval=requires_tool_approval,
    ).inspect({"messages": [response]})
    return {"messages": [response], **approval_update}


def _latest_ai_with_tool_calls(messages):
    for message in reversed(messages):
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            return message
    return None


def _active_tools_for_state(registry: SkillRegistry, selected_skills: list[str], basic_tools):
    tool_map = {tool.name: tool for tool in basic_tools}
    tool_map.update(registry.tool_map_for_skills(selected_skills))
    return list(tool_map.values())


async def _execute_tool_calls_with_retry(
    tools,
    tool_calls: list[dict],
    *,
    config: RunnableConfig | None = None,
    memory=None,
    thread_id: str | None = None,
    max_retries: int = 3,
    base_delay: float = 0.25,
    sleep=asyncio.sleep,
) -> list[ToolMessage]:
    tool_map = {tool.name: tool for tool in tools}
    messages: list[ToolMessage] = []
    for call in tool_calls:
        tool_started = time.perf_counter()
        tool_name = str(call.get("name") or "tool")
        tool_call_id = str(call.get("id") or "")
        tool_args = call.get("args", {})
        if not isinstance(tool_args, dict):
            tool_args = {"input": tool_args}
        tool = tool_map.get(tool_name)
        if tool is None:
            messages.append(
                ToolMessage(
                    tool_call_id=tool_call_id,
                    content=_tool_failure_content(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        attempts=1,
                        error_type="KeyError",
                        error_message=f"Unknown tool: {tool_name}",
                    ),
                )
            )
            continue

        last_error: Exception | None = None
        max_attempts = max_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                output = await tool.ainvoke(tool_args, config=config)
                content = _tool_output_content(output)
                messages.append(
                    ToolMessage(
                        tool_call_id=tool_call_id,
                        content=content,
                    )
                )
                await _record_execution_log(
                    memory,
                    ExecutionLogCreate(
                        thread_id=thread_id or "",
                        event_type="tool",
                        status="completed",
                        name=tool_name,
                        input=tool_args,
                        output={"content": content},
                        duration_ms=int((time.perf_counter() - tool_started) * 1000),
                        metadata={"tool_call_id": tool_call_id, "attempt": attempt},
                    ),
                )
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                will_retry = attempt < max_attempts
                await _record_tool_error(
                    memory,
                    thread_id=thread_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc) or exc.__class__.__name__,
                    will_retry=will_retry,
                )
                await _record_execution_log(
                    memory,
                    ExecutionLogCreate(
                        thread_id=thread_id or "",
                        event_type="tool_retry",
                        status="retrying" if will_retry else "failed",
                        name=tool_name,
                        input=tool_args,
                        error={
                            "type": exc.__class__.__name__,
                            "message": str(exc) or exc.__class__.__name__,
                        },
                        duration_ms=int((time.perf_counter() - tool_started) * 1000),
                        metadata={
                            "tool_call_id": tool_call_id,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "will_retry": will_retry,
                        },
                    ),
                )
                if will_retry:
                    await sleep(base_delay * (2 ** (attempt - 1)))
        if last_error is not None:
            await _record_execution_log(
                memory,
                ExecutionLogCreate(
                    thread_id=thread_id or "",
                    event_type="tool",
                    status="failed",
                    name=tool_name,
                    input=tool_args,
                    error={
                        "type": last_error.__class__.__name__,
                        "message": str(last_error) or last_error.__class__.__name__,
                    },
                    duration_ms=int((time.perf_counter() - tool_started) * 1000),
                    metadata={"tool_call_id": tool_call_id, "attempt": max_attempts},
                ),
            )
            messages.append(
                ToolMessage(
                    tool_call_id=tool_call_id,
                    content=_tool_failure_content(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        attempts=max_attempts,
                        error_type=last_error.__class__.__name__,
                        error_message=str(last_error) or last_error.__class__.__name__,
                    ),
                )
            )
    return messages


async def _record_tool_error(memory, **kwargs) -> None:
    record = getattr(memory, "record_tool_error", None)
    if not callable(record):
        return
    try:
        await record(**kwargs)
    except Exception:
        return


async def _record_execution_log(memory, log: ExecutionLogCreate) -> None:
    record = getattr(memory, "record_execution_log", None)
    if not callable(record):
        return
    try:
        await record(log)
    except Exception:
        return


def _extract_token_usage(response) -> dict[str, Any]:
    raw = _token_usage_raw(response)
    prompt = _int_from_keys(raw, "prompt_tokens", "input_tokens")
    completion = _int_from_keys(raw, "completion_tokens", "output_tokens")
    total = _int_from_keys(raw, "total_tokens")
    if total == 0:
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "raw": raw,
    }


def _token_usage_raw(response) -> dict[str, Any]:
    for attr in ("usage_metadata", "response_metadata"):
        value = getattr(response, attr, None)
        if not isinstance(value, dict):
            continue
        token_usage = value.get("token_usage")
        if isinstance(token_usage, dict):
            return token_usage
        if any(str(key).endswith("_tokens") for key in value):
            return value
    return {}


def _int_from_keys(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _tool_output_content(output) -> str:
    if isinstance(output, ToolMessage):
        return str(output.content)
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False)
    except TypeError:
        return str(output)


def _tool_failure_content(
    *,
    tool_name: str,
    tool_args: dict,
    attempts: int,
    error_type: str,
    error_message: str,
) -> str:
    args_json = json.dumps(tool_args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        f"Tool call failed after {attempts} attempts.\n"
        f"Tool: {tool_name}\n"
        f"Arguments: {args_json}\n"
        f"Error: {error_type}: {error_message}\n"
        "Use the error and arguments above to decide whether to retry with corrected parameters."
    )


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


def _agent_route_without_memory_reflection(state: AgentState) -> str:
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "approval"
    return "end"


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
