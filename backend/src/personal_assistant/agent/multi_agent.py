import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from personal_assistant.agent.child_agent_protocol import SubAgentInput
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import OllamaBgeM3EmbeddingProvider
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import ExecutionLogCreate, LLMConfig
from personal_assistant.config import Settings
from personal_assistant.skills import SkillRegistry


APM_SUBAGENTS = ("metrics", "troubleshoot", "patrol", "audit")

# ── 子 Agent → Skill 映射 ──────────────────────────────────────────────
# 每个子 agent 挂载的 skill 列表，skill 提供具体的工具（查询 Jaeger、
# Prometheus、执行日志等）。未列出的 agent 默认无工具。

CHILD_AGENT_SKILLS: dict[str, list[str]] = {
    "metrics": ["apm-metrics", "otel-query"],
    "troubleshoot": ["troubleshoot", "troubleshoot-runbook", "otel-query"],
    "patrol": ["patrol", "otel-query"],
    "audit": ["audit-sop"],
}

# 子 agent ReAct 循环最大迭代次数（防止无限循环及上下文爆炸）
MAX_CHILD_AGENT_ITERATIONS = 4

# 子 agent 工具返回值最大字符数（超出截断，防止 context 超限）
MAX_TOOL_RESULT_CHARS = 2000

_CHILD_AGENT_SYSTEM_PROMPT = """\
你是一个 APM 子分析 Agent。你的职责是调用工具获取实际数据，然后基于数据输出结构化 JSON 报告。

## 工作流程
1. 先调用可用的工具获取数据（查询 Trace、Metrics、执行日志等）
2. 基于工具返回的实际数据进行分析
3. 最后输出结构化 JSON 报告

## 规则
- **先查数据再分析**：不要凭空编造，必须调用工具获取真实数据后再输出报告。
- 只返回结构化 JSON 对象。不要 Markdown、不要解释。
- findings（发现）、evidence（证据）、recommendations（建议）必须使用中文输出。
- 无法获取数据时，confidence 设为较低值 (0.0-0.3) 并在 error 中说明具体原因。
- 引用具体证据（指标数值、Trace ID、日志片段）—— 禁止编造数据。
- tools_used 列出你实际调用的每个工具名称。
- status 只能是 "completed" 或 "failed"——仅当所有工具都不可达时用 "failed"。

## 输出 Schema
{
  "agent": "<你的 agent 名称>",
  "task_id": "<分配的任务 ID>",
  "status": "completed|failed",
  "findings": ["发现 1（中文，附具体数值）", "发现 2（中文，附具体数值）", ...],
  "evidence": ["证据 1（中文，含日志/Trace/指标原文）", ...],
  "recommendations": ["建议 1（中文，可执行）", ...],
  "confidence": 0.0-1.0,
  "tools_used": ["tool_name_1", ...],
  "error": null
}"""


# ── audit agent 内置 memory 工具 ────────────────────────────────────────


def _build_audit_memory_tools(memory) -> list:
    """为审计子 agent 构建基于 memory 的内置查询工具。

    当 ``audit-sop`` skill 未提供工具实现时（仅有 SOP 文档），
    这些工具让 audit agent 能够查询 Postgres 中的执行日志、
    安全事件、工具错误和执行摘要。

    所有工具返回 JSON 字符串以适配 LLM tool calling 协议。
    """

    @tool
    async def query_execution_log_summary(thread_id: str) -> str:
        """查询指定线程的执行摘要统计。

        返回：总事件数、token 消耗（prompt/completion）、工具调用数、
        工具错误/重试数、安全事件数、总耗时(ms)。

        Args:
            thread_id: 要查询的线程 ID（可从 task_id 中提取，格式为 agent_name-thread_id）
        """
        try:
            result = await memory.execution_log_summary(thread_id)
            return json.dumps(result.model_dump(), ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool
    async def query_execution_logs(thread_id: str, limit: int = 100) -> str:
        """查询指定线程的详细执行日志列表。

        返回按时间排序的事件列表，每个事件包含 event_type、status、
        duration_ms、input/output 等信息。

        Args:
            thread_id: 要查询的线程 ID
            limit: 返回条数上限（默认 100，最大 500）
        """
        try:
            limit = max(1, min(limit, 500))
            result = await memory.list_execution_logs(thread_id, limit=limit)
            return json.dumps(
                [item.model_dump() for item in result],
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool
    async def query_audit_events(thread_id: str, limit: int = 100) -> str:
        """查询指定线程的安全审计事件列表。

        返回 prompt guard 拦截、tool guard 拦截等安全事件，
        每个事件包含 category、severity、reason、subject 等字段。

        Args:
            thread_id: 要查询的线程 ID
            limit: 返回条数上限（默认 100，最大 500）
        """
        try:
            limit = max(1, min(limit, 500))
            result = await memory.list_audit_events(thread_id=thread_id, limit=limit)
            return json.dumps(
                [item.model_dump() for item in result],
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool
    async def query_tool_errors(thread_id: str, limit: int = 100) -> str:
        """查询指定线程的工具错误/重试记录列表。

        返回工具调用失败的详细信息，包括 tool_name、error_type、
        error_message、attempt（第几次尝试）、will_retry 等字段。

        Args:
            thread_id: 要查询的线程 ID
            limit: 返回条数上限（默认 100，最大 500）
        """
        try:
            limit = max(1, min(limit, 500))
            result = await memory.list_tool_errors(thread_id=thread_id, limit=limit)
            return json.dumps(
                [item.model_dump() for item in result],
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return [
        query_execution_log_summary,
        query_execution_logs,
        query_audit_events,
        query_tool_errors,
    ]


def rewrite_query_and_slots(query: str) -> dict[str, Any]:
    """Extract metrics/entities and classify intent from a user query.

    Intent classification uses signal-counting heuristics (via
    _regex_intent_with_confidence from intent_router) instead of a
    simple if-elif chain, so that keyword-rich knowledge queries
    (e.g. "解释 LCP/CLS 和告警") are correctly classified as
    ``metrics`` rather than being short-circuited by a stray
    ``patrol`` or ``troubleshoot`` keyword.
    """
    from personal_assistant.agent.intent_router import _regex_intent_with_confidence

    normalized = " ".join(query.split())
    lowered = normalized.lower()
    metrics = _unique(
        match.group(0).lower()
        for match in re.finditer(r"\b(?:p50|p75|p90|p95|p99|lcp|cls|inp|ttfb|fid|tbt|apdex|slo)\b", lowered)
    )
    intent, _confidence = _regex_intent_with_confidence(normalized)

    entities = _unique(
        token
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", normalized)
        if token.lower() not in {"api", "apm", "rca", *metrics}
    )
    return {
        "original_query": query,
        "rewritten_query": normalized,
        "slots": {
            "domain": "apm" if _looks_like_apm(normalized) else "general",
            "intent": intent,
            "metrics": metrics,
            "entities": entities,
            "requires_user_vector_context": True,
        },
    }


def compile_multi_agent(
    settings: Settings,
    registry: SkillRegistry,
    memory,
    llm_config: LLMConfig | None = None,
    hook_manager=None,
    cache=None,
    # ── Hybrid intent routing (3-tier funnel) ──────────────────────────
    intent_index=None,  # IntentEmbeddingIndex | None
    intent_llm=None,    # LLM for Tier 2 intent classification
    # ── Child agent LLM ────────────────────────────────────────────────
    child_llm_config: LLMConfig | None = None,
    # ── Hybrid RAG retrieval ───────────────────────────────────────────
    hybrid_retriever=None,  # HybridRetriever | None
    # ── Enhanced query rewriting ────────────────────────────────────────
    query_rewriter=None,  # QueryRewriter | None
):
    llm = build_llm(settings, llm_config)

    # ── 构建子 Agent 专用 LLM ──────────────────────────────────────────
    if child_llm_config is not None:
        child_llm = build_llm(settings, child_llm_config)
    elif getattr(settings, "multi_agent_child_llm_model", None):
        child_llm = build_llm(
            settings,
            LLMConfig(
                model=settings.multi_agent_child_llm_model,
                temperature=0.1,
            ),
        )
    else:
        child_llm = llm  # 回退到主 LLM（保持向后兼容）

    # ── 为每个子 Agent 预加载 Skill 并构建工具 ─────────────────────────
    child_agent_tools: dict[str, list] = {}
    _registry_ok = hasattr(registry, "tool_map_for_skills") and hasattr(registry, "load_skill")
    for agent_name in APM_SUBAGENTS:
        skill_names = CHILD_AGENT_SKILLS.get(agent_name, [])
        tools: list = []
        if skill_names and _registry_ok:
            for skill_name in skill_names:
                try:
                    if skill_name not in registry._skills:  # type: ignore[union-attr]
                        continue
                    registry.load_skill(skill_name)  # type: ignore[union-attr]
                except Exception:
                    continue
            try:
                tools = list(registry.tool_map_for_skills(skill_names).values())  # type: ignore[union-attr]
            except Exception:
                pass
        child_agent_tools[agent_name] = tools

    # ── 为 audit agent 注入内置 memory 查询工具 ─────────────────────
    # audit-sop skill 目前仅有 SOP 文档无工具实现，但 audit agent 需要
    # 能查询执行日志、安全事件、工具错误等数据。这些数据通过 memory 对象
    # （PostgresMemory）暴露，此处将其包装为 LangChain tool 注入。
    # 仅当 memory 对象具备查询能力（hasattr 检查）且 registry 未提供
    # 工具时才注入，避免对测试 stub 对象注入无效工具。
    if not child_agent_tools.get("audit") and hasattr(memory, "execution_log_summary"):
        child_agent_tools["audit"] = _build_audit_memory_tools(memory)

    # Read multi-agent intent routing config (use getattr for test compatibility)
    regex_threshold = float(getattr(settings, "multi_agent_intent_regex_threshold", 0.80) or 0.80)
    semantic_enabled = bool(getattr(settings, "multi_agent_intent_semantic_enabled", True))
    semantic_threshold = float(getattr(settings, "multi_agent_intent_semantic_threshold", 0.75) or 0.75)
    llm_enabled = bool(getattr(settings, "multi_agent_intent_llm_enabled", True))
    llm_threshold = float(getattr(settings, "multi_agent_intent_llm_threshold", 0.60) or 0.60)

    async def rewrite_intent(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        query = _last_human_text(state)

        # ── Enhanced query rewriting (LLM-based, config-gated) ──
        rewritten_query = query
        rewrite_result = None
        if query_rewriter is not None and query_rewriter.enabled:
            from personal_assistant.agent.query_rewriter import extract_conversation_context

            history_text = extract_conversation_context(
                state.get("messages", []),
                max_turns=query_rewriter.history_max_turns,
            )
            history_entries: list[dict[str, str]] = []
            if history_text:
                for line in history_text.split("\n"):
                    if line.startswith("user: "):
                        history_entries.append({"role": "user", "content": line[6:]})
                    elif line.startswith("assistant: "):
                        history_entries.append({"role": "assistant", "content": line[11:]})

            rewrite_result = await query_rewriter.rewrite(query, history=history_entries)
            rewritten_query = rewrite_result.rewritten

        # Always run legacy regex for metrics/entities extraction
        legacy = rewrite_query_and_slots(rewritten_query)

        # Use 3-tier funnel when intent_index or intent_llm is available
        if intent_index is not None or intent_llm is not None:
            from personal_assistant.agent.intent_router import route_intent_with_trace

            routing = await route_intent_with_trace(
                rewritten_query,
                intent_index=intent_index if semantic_enabled else None,
                llm=intent_llm if llm_enabled else None,
                regex_threshold=regex_threshold,
                semantic_threshold=semantic_threshold,
                llm_threshold=llm_threshold,
                existing_slots=legacy.get("slots"),
            )
            slots_dict = routing.intent_slots.to_dict()

            # Merge rewrite result into intent slots
            if rewrite_result is not None:
                slots_dict["original_query"] = rewrite_result.original
                slots_dict["rewrite_confidence"] = rewrite_result.confidence
                slots_dict["needs_clarification"] = rewrite_result.needs_clarification
                slots_dict["missing_slots"] = rewrite_result.missing_slots
                slots_dict["sub_queries"] = rewrite_result.sub_queries
                slots_dict["rewrite_reason"] = rewrite_result.reason

            await _record_multiagent_log(memory, config, "rewrite_intent", output={
                "slots": slots_dict,
                "trace": routing.trace,
                "rewrite": {
                    "original": rewrite_result.original if rewrite_result else query,
                    "rewritten": rewritten_query,
                    "confidence": rewrite_result.confidence if rewrite_result else None,
                } if rewrite_result else None,
            })
            return {
                "rewritten_query": rewritten_query,
                "intent_slots": slots_dict,
            }

        # Fallback: pure regex (with rewritten query if available)
        payload = legacy
        if rewrite_result is not None:
            payload["rewritten_query"] = rewritten_query
            payload["slots"]["original_query"] = rewrite_result.original
            payload["slots"]["rewrite_confidence"] = rewrite_result.confidence
        await _record_multiagent_log(memory, config, "rewrite_intent", output=payload)
        return {
            "rewritten_query": rewritten_query,
            "intent_slots": payload["slots"],
        }

    async def supervisor(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        plan = _supervisor_plan(state.get("rewritten_query", ""), state.get("intent_slots", {}))
        await _record_multiagent_log(memory, config, "supervisor", output=plan)
        return {"multiagent_plan": plan}

    async def retrieve_user_vector_context(
        state: AgentState,
        config: RunnableConfig | None = None,
    ) -> AgentState:
        context = await _retrieve_user_vector_context(
            settings,
            state.get("rewritten_query", ""),
            hybrid_retriever=hybrid_retriever,
        )
        await _record_multiagent_log(memory, config, "user_vector_retrieval", output=context)
        return {"user_vector_context": context}

    async def child_agent(state: AgentState, config: RunnableConfig | None = None, *, name: str) -> AgentState:
        # 构造结构化输入
        task_input = SubAgentInput(
            task_id=f"{name}-{_tid(config)}",
            agent=name,
            query=state.get("rewritten_query", ""),
            intent_slots=state.get("intent_slots", {}),
            user_vector_context=state.get("user_vector_context", {}),
            plan_hint=state.get("multiagent_plan", {}),
        )

        tools = child_agent_tools.get(name, [])
        if not tools:
            # 无工具：单次调用，token 流式输出到 SSE
            response = await child_llm.ainvoke(
                [
                    SystemMessage(content=_CHILD_AGENT_SYSTEM_PROMPT),
                    HumanMessage(content=task_input.model_dump_json()),
                ],
                config=config,
            )
            report_dict = _coerce_report(name, getattr(response, "content", response))
        else:
            # 有工具：mini ReAct 循环（最多 MAX_CHILD_AGENT_ITERATIONS 轮）
            tool_map = {t.name: t for t in tools}
            messages: list = [
                SystemMessage(content=_CHILD_AGENT_SYSTEM_PROMPT),
                HumanMessage(content=task_input.model_dump_json()),
            ]
            tools_used: list[str] = []

            for _ in range(MAX_CHILD_AGENT_ITERATIONS):
                response = await child_llm.bind_tools(tools).ainvoke(messages, config=config)
                messages.append(response)

                tool_calls = getattr(response, "tool_calls", None) or []
                if not tool_calls:
                    # LLM 输出了最终 JSON 报告（无进一步 tool call）
                    break

                # 执行工具调用
                for tc in tool_calls:
                    tool_name = str(tc.get("name") or "")
                    tool_args = tc.get("args", {})
                    if not isinstance(tool_args, dict):
                        tool_args = {"input": tool_args}
                    tools_used.append(tool_name)

                    tool = tool_map.get(tool_name)
                    if tool is None:
                        messages.append(ToolMessage(
                            tool_call_id=str(tc.get("id") or ""),
                            content=f"Error: unknown tool '{tool_name}'",
                        ))
                        continue
                    try:
                        result = await tool.ainvoke(tool_args, config=config)
                        content = str(result)
                        original_len = len(content)
                        # 截断过长工具返回值防止 context 爆炸
                        if original_len > MAX_TOOL_RESULT_CHARS:
                            content = (
                                content[:MAX_TOOL_RESULT_CHARS]
                                + f"\n...(已截断，原始长度 {original_len} 字符)"
                            )
                        messages.append(ToolMessage(
                            tool_call_id=str(tc.get("id") or ""),
                            content=content,
                        ))
                    except Exception as exc:
                        messages.append(ToolMessage(
                            tool_call_id=str(tc.get("id") or ""),
                            content=f"Error: {exc}",
                        ))

            report_dict = _coerce_report(name, getattr(response, "content", response))
            report_dict.setdefault("tools_used", tools_used)

        # 注入新版协议字段
        report_dict.setdefault("task_id", task_input.task_id)
        report_dict.setdefault("status", "completed")
        report_dict.setdefault("error", None)

        await _record_multiagent_log(memory, config, f"{name}_agent", input=task_input.model_dump(), output=report_dict)
        return {
            "apm_reports": [report_dict],
            "child_agent_tasks": [{
                "task_id": task_input.task_id,
                "agent_name": name,
                "status": report_dict.get("status", "completed"),
                "completed_at": _utc_now_iso(),
                "error": report_dict.get("error"),
            }],
        }

    def child_node(name: str):
        async def run_child(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
            return await child_agent(state, config, name=name)

        return run_child

    async def gate_node(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        """栅栏节点：确认所有 dispatched 子 agent 已完成。

        LangGraph Send API 的 fan-out 边提供隐式 barrier — 所有 fan-out
        节点完成后才进入下游。本节点记录完成状态日志，不做实际等待。
        """
        tasks = state.get("child_agent_tasks", [])
        reports = state.get("apm_reports", [])
        plan = state.get("multiagent_plan", {})
        expected = set(plan.get("subagents", list(APM_SUBAGENTS)))
        completed = {t.get("agent_name") for t in tasks if t.get("status") in ("completed", "failed")}
        missing = expected - completed

        await _record_multiagent_log(memory, config, "gate", output={
            "expected": sorted(expected),
            "completed": sorted(completed),
            "missing": sorted(missing),
            "report_count": len(reports),
        })
        return {}

    def _supervisor_router(state: AgentState) -> list[Send]:
        """条件路由：仅 dispatch supervisor plan 中选中的子 agent。"""
        plan = state.get("multiagent_plan", {})
        selected = plan.get("subagents", list(APM_SUBAGENTS))
        return [
            Send(
                f"{name}_agent",
                {
                    "child_agent_tasks": [{
                        "task_id": f"{name}-{_tid_from_state(state)}",
                        "agent_name": name,
                        "status": "pending",
                    }],
                },
            )
            for name in selected
        ]

    async def synthesize(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        payload = {
            "query": state.get("rewritten_query", ""),
            "intent_slots": state.get("intent_slots", {}),
            "user_vector_context": state.get("user_vector_context", {}),
            "reports": state.get("apm_reports", []),
        }
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是 APM 主监督 Agent。请将子 Agent 的 JSON 报告综合成简洁的面向用户的回答。"
                        "保留具体证据和数值，使用中文输出。"
                    )
                ),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
            config=config,
        )
        content = str(getattr(response, "content", response) or "")
        await _record_multiagent_log(memory, config, "synthesize", input=payload, output={"content": content})
        return {"messages": [AIMessage(content=content)]}

    graph = StateGraph(AgentState)
    graph.add_node("rewrite_intent", rewrite_intent)
    graph.add_node("retrieve_user_vector_context", retrieve_user_vector_context)
    graph.add_node("supervisor", supervisor)
    graph.add_node("metrics_agent", child_node("metrics"))
    graph.add_node("troubleshoot_agent", child_node("troubleshoot"))
    graph.add_node("patrol_agent", child_node("patrol"))
    graph.add_node("audit_agent", child_node("audit"))
    graph.add_node("gate", gate_node)
    graph.add_node("synthesize", synthesize)

    graph.set_entry_point("rewrite_intent")
    graph.add_edge("rewrite_intent", "retrieve_user_vector_context")
    graph.add_edge("retrieve_user_vector_context", "supervisor")

    # 条件路由：supervisor → Send API 仅 dispatch 选中的子 agent
    graph.add_conditional_edges("supervisor", _supervisor_router)

    # 所有子 agent 完成后 → gate → synthesize
    for agent_name in ("metrics_agent", "troubleshoot_agent", "patrol_agent", "audit_agent"):
        graph.add_edge(agent_name, "gate")
    graph.add_edge("gate", "synthesize")
    graph.add_edge("synthesize", END)

    checkpointer = getattr(memory, "checkpointer", None)
    return graph.compile(checkpointer=checkpointer)


def _supervisor_plan(query: str, slots: dict[str, Any]) -> dict[str, Any]:
    intent = str(slots.get("intent") or "general")
    preferred = {
        "metrics": ["metrics", "audit"],
        "troubleshoot": ["troubleshoot", "metrics", "audit"],
        "patrol": ["patrol", "metrics", "audit"],
        "audit": ["audit", "metrics"],
    }.get(intent, list(APM_SUBAGENTS))
    return {
        "query": query,
        "intent": intent,
        "subagents": preferred,
        "message_format": "json",
        "harness": {
            "enabled": True,
            "approval_gated_tools": True,
            "storage": ["postgresql", "redis", "qdrant"],
        },
    }


def _coerce_confidence(raw: Any) -> float:
    """Convert a confidence value (which may be a qualitative string) to a float."""
    if raw is None:
        return 0.5
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        qualitative: dict[str, float] = {
            "very_high": 0.95,
            "very high": 0.95,
            "high": 0.85,
            "medium_high": 0.7,
            "medium high": 0.7,
            "medium": 0.5,
            "medium_low": 0.3,
            "medium low": 0.3,
            "low": 0.15,
            "very_low": 0.05,
            "very low": 0.05,
        }
        if cleaned in qualitative:
            return qualitative[cleaned]
        try:
            return float(cleaned)
        except ValueError:
            return 0.5
    return 0.5


def _coerce_report(agent_name: str, raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        parsed = raw
    else:
        text = str(raw or "").strip()
        try:
            parsed = json.loads(_extract_json_object(text))
        except Exception:
            parsed = {"summary": text}
    return {
        "agent": str(parsed.get("agent") or agent_name),
        "findings": _as_list(parsed.get("findings") or parsed.get("summary")),
        "evidence": _as_list(parsed.get("evidence")),
        "recommendations": _as_list(parsed.get("recommendations")),
        "confidence": _coerce_confidence(parsed.get("confidence")),
    }


async def _record_multiagent_log(
    memory,
    config: RunnableConfig | None,
    name: str,
    *,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
) -> None:
    record = getattr(memory, "record_execution_log", None)
    if not callable(record):
        return
    try:
        await record(
            ExecutionLogCreate(
                thread_id=_thread_id_from_config(config) or "",
                event_type="multiagent",
                status="completed",
                name=name,
                input=input or {},
                output=output or {},
                duration_ms=0,
                metadata={"agent_mode": "multi"},
            )
        )
    except Exception:
        return


def _last_human_text(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            return str(getattr(message, "content", "") or "")
    return ""


async def _retrieve_user_vector_context(
    settings: Settings, query: str, *, hybrid_retriever=None,
) -> dict[str, Any]:
    # ── Hybrid path (vector + BM25 + relevance filter) ─────────────────
    if hybrid_retriever is not None:
        try:
            result = await hybrid_retriever.retrieve(query)
            formatted = hybrid_retriever.format_for_llm(result)
            return {
                "status": result.status,
                "documents": [d.model_dump() for d in result.documents],
                "formatted": formatted,
                "trust_signal": getattr(result, "trust_signal", ""),
            }
        except Exception as exc:
            logger.warning(
                "Hybrid retrieval failed, falling back to legacy: %s", exc,
                exc_info=True,
            )

    # ── Legacy path: raw Qdrant vector search ──────────────────────────
    if not getattr(settings, "user_vector_retrieval_enabled", False):
        return {
            "status": "skipped",
            "reason": "USER_VECTOR_RETRIEVAL_ENABLED is false",
            "documents": [],
        }
    qdrant_url = getattr(settings, "user_vector_qdrant_url", None)
    if not qdrant_url:
        return {
            "status": "skipped",
            "reason": "USER_VECTOR_QDRANT_URL is not configured",
            "documents": [],
        }
    try:
        embedding = await OllamaBgeM3EmbeddingProvider(
            base_url=getattr(settings, "skill_routing_ollama_base_url", "http://localhost:11434"),
            model=getattr(settings, "skill_routing_embedding_model", "bge-m3"),
        ).embed(query)
        documents = await _qdrant_search_user_documents(
            url=qdrant_url,
            collection=getattr(settings, "user_vector_qdrant_collection", "user_memory"),
            api_key=getattr(settings, "user_vector_qdrant_api_key", None),
            vector=embedding,
            top_k=int(getattr(settings, "user_vector_top_k", 5) or 5),
        )
        return {"status": "completed", "documents": documents}
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "documents": []}


async def _qdrant_search_user_documents(
    *,
    url: str,
    collection: str,
    api_key: str | None,
    vector: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    return await __import__("asyncio").to_thread(
        _qdrant_search_user_documents_sync,
        url.rstrip("/"),
        collection,
        api_key,
        vector,
        top_k,
    )


def _qdrant_search_user_documents_sync(
    url: str,
    collection: str,
    api_key: str | None,
    vector: list[float],
    top_k: int,
) -> list[dict[str, Any]]:
    body = json.dumps(
        {
            "vector": vector,
            "limit": max(1, top_k),
            "with_payload": True,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    request = urllib.request.Request(
        f"{url}/collections/{collection}/points/search",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Qdrant user vector search failed: {exc}") from exc
    result = payload.get("result", []) if isinstance(payload, dict) else []
    if not isinstance(result, list):
        return []
    documents = []
    for item in result:
        if not isinstance(item, dict):
            continue
        point_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        documents.append(
            {
                "score": float(item.get("score") or 0.0),
                "content": str(point_payload.get("content") or point_payload.get("text") or ""),
                "metadata": point_payload.get("metadata") if isinstance(point_payload, dict) else {},
            }
        )
    return documents


def _thread_id_from_config(config: RunnableConfig | None) -> str | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) else None


def _tid(config: RunnableConfig | None) -> str:
    """Extract thread_id from config, returning 'unknown' on failure."""
    return _thread_id_from_config(config) or "unknown"


def _tid_from_state(state: AgentState) -> str:
    """Extract thread_id from state messages for use when config is unavailable."""
    for message in reversed(state.get("messages", [])):
        if hasattr(message, "id") and message.id:
            return str(message.id)[:8]
    return "unknown"


def _utc_now_iso() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _looks_like_apm(text: str) -> bool:
    return bool(re.search(r"\b(?:apm|api|p95|p99|lcp|cls|inp|apdex|slo)\b|排查|根因|巡检|告警|指标", text, re.I))


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _unique(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        normalized = str(value)
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result
