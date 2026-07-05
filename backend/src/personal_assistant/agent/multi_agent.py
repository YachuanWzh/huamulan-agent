import json
import re
import urllib.error
import urllib.request
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import OllamaBgeM3EmbeddingProvider
from personal_assistant.agent.state import AgentState
from personal_assistant.api.schemas import ExecutionLogCreate, LLMConfig
from personal_assistant.config import Settings
from personal_assistant.skills import SkillRegistry


APM_SUBAGENTS = ("metrics", "troubleshoot", "patrol", "audit")


def rewrite_query_and_slots(query: str) -> dict[str, Any]:
    normalized = " ".join(query.split())
    lowered = normalized.lower()
    metrics = _unique(
        match.group(0).lower()
        for match in re.finditer(r"\b(?:p50|p75|p90|p95|p99|lcp|cls|inp|ttfb|apdex|slo)\b", lowered)
    )
    intent = "general"
    if re.search(r"\b(?:rca|root cause|troubleshoot|timeout|slow|error)\b|排查|根因|超时|异常|故障", normalized, re.I):
        intent = "troubleshoot"
    elif re.search(r"\b(?:patrol|health check|alert)\b|巡检|告警|健康检查", normalized, re.I):
        intent = "patrol"
    elif re.search(r"\b(?:audit|approval|compliance|log)\b|审计|合规|审批|日志", normalized, re.I):
        intent = "audit"
    elif metrics or re.search(r"\b(?:metric|web vitals|conversion)\b|指标|转化率", normalized, re.I):
        intent = "metrics"

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
):
    llm = build_llm(settings, llm_config)

    # Read multi-agent intent routing config (use getattr for test compatibility)
    regex_threshold = float(getattr(settings, "multi_agent_intent_regex_threshold", 0.80) or 0.80)
    semantic_enabled = bool(getattr(settings, "multi_agent_intent_semantic_enabled", True))
    semantic_threshold = float(getattr(settings, "multi_agent_intent_semantic_threshold", 0.75) or 0.75)
    llm_enabled = bool(getattr(settings, "multi_agent_intent_llm_enabled", True))
    llm_threshold = float(getattr(settings, "multi_agent_intent_llm_threshold", 0.60) or 0.60)

    async def rewrite_intent(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        query = _last_human_text(state)

        # Always run legacy regex for metrics/entities extraction
        legacy = rewrite_query_and_slots(query)

        # Use 3-tier funnel when intent_index or intent_llm is available
        if intent_index is not None or intent_llm is not None:
            from personal_assistant.agent.intent_router import route_intent_with_trace

            routing = await route_intent_with_trace(
                query,
                intent_index=intent_index if semantic_enabled else None,
                llm=intent_llm if llm_enabled else None,
                regex_threshold=regex_threshold,
                semantic_threshold=semantic_threshold,
                llm_threshold=llm_threshold,
                existing_slots=legacy.get("slots"),
            )
            slots_dict = routing.intent_slots.to_dict()
            await _record_multiagent_log(memory, config, "rewrite_intent", output={
                "slots": slots_dict,
                "trace": routing.trace,
            })
            return {
                "rewritten_query": legacy["rewritten_query"],
                "intent_slots": slots_dict,
            }

        # Fallback: pure regex (original behavior, unchanged)
        payload = legacy
        await _record_multiagent_log(memory, config, "rewrite_intent", output=payload)
        return {
            "rewritten_query": payload["rewritten_query"],
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
        context = await _retrieve_user_vector_context(settings, state.get("rewritten_query", ""))
        await _record_multiagent_log(memory, config, "user_vector_retrieval", output=context)
        return {"user_vector_context": context}

    async def child_agent(state: AgentState, config: RunnableConfig | None = None, *, name: str) -> AgentState:
        payload = {
            "agent": name,
            "query": state.get("rewritten_query", ""),
            "intent_slots": state.get("intent_slots", {}),
            "user_vector_context": state.get("user_vector_context", {}),
            "plan": state.get("multiagent_plan", {}),
            "communication_contract": {
                "format": "json",
                "required_fields": ["agent", "findings", "evidence", "recommendations"],
            },
        }
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "You are an APM child agent. Return only a JSON object with "
                        "agent (string), findings (string[]), evidence (string[]), "
                        "recommendations (string[]), and confidence (float 0.0–1.0)."
                    )
                ),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ],
            config=config,
        )
        report = _coerce_report(name, getattr(response, "content", response))
        await _record_multiagent_log(memory, config, f"{name}_agent", input=payload, output=report)
        return {"apm_reports": [report]}

    def child_node(name: str):
        async def run_child(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
            return await child_agent(state, config, name=name)

        return run_child

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
                        "You are the main APM supervisor. Synthesize child JSON reports "
                        "into a concise user-facing answer. Preserve concrete evidence."
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
    graph.add_node("synthesize", synthesize)
    graph.set_entry_point("rewrite_intent")
    graph.add_edge("rewrite_intent", "retrieve_user_vector_context")
    graph.add_edge("retrieve_user_vector_context", "supervisor")
    for agent_name in ("metrics_agent", "troubleshoot_agent", "patrol_agent", "audit_agent"):
        graph.add_edge("supervisor", agent_name)
        graph.add_edge(agent_name, "synthesize")
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


async def _retrieve_user_vector_context(settings: Settings, query: str) -> dict[str, Any]:
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
