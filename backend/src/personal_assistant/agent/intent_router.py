"""Multi-agent intent routing with a 3-tier funnel (regex → semantic → LLM).

Reuses the single-agent embedding infrastructure (OllamaBgeM3EmbeddingProvider,
OllamaBgeM3Reranker) from router.py.

The key difference from single-agent routing:
  - Single-agent: embeds *skill metadata* (name + description + triggers)
    to match user query → specific Skill
  - Multi-agent: embeds *intent utterances* (example queries per category)
    to classify user query → intent category (troubleshoot/patrol/audit/metrics)
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Intent utterances (Tier 1 semantic matching) ──────────────────────────
# Each intent category has example user queries in Chinese and English.
# These are embedded via BGE-M3 and mean-pooled to form an intent centroid
# vector. At query time the user's query embedding is compared against
# each centroid via cosine similarity.
#
# Reuses the same OllamaBgeM3EmbeddingProvider from router.py — no new
# embedding infrastructure needed.

INTENT_UTTERANCES: dict[str, list[str]] = {
    "troubleshoot": [
        "排查 payment-service 超时问题",
        "帮我做一下根因分析",
        "最近错误率升高了是什么原因",
        "RCA the latency spike on api-gateway",
        "为什么数据库查询突然变慢了",
        "服务挂了帮我看看",
        "frontend error rate is spiking, need root cause",
        "分析一下 APM 里面的异常 trace",
    ],
    "patrol": [
        "设置一个夜间自动巡检规则",
        "配置告警阈值",
        "创建健康检查任务",
        "定时巡检所有服务",
        "帮我配一条 p99 > 500ms 的告警",
        "add a patrol rule for error_rate > 5%",
        "每日凌晨自动巡检生产环境",
        "配置核心接口的可用性监控",
    ],
    "audit": [
        "审计一下最近的工具调用记录",
        "查看执行日志",
        "检查 SLA 合规情况",
        "审批通过率是多少",
        "audit the tool execution logs for security events",
        "跨线程治理巡检",
        "查看最近一周的操作审计日志",
        "合规性检查：谁修改了告警规则",
    ],
    "metrics": [
        "LCP 指标怎么定义的",
        "查看 p95 延迟趋势",
        "业务转化率是多少",
        "Web Vitals 指标解读",
        "what is Apdex and how to collect it",
        "自定义指标怎么采集",
        "Dashboard 里 FID 数据异常",
        "按服务维度查看错误率趋势",
    ],
}


# ── Types ──────────────────────────────────────────────────────────────────


@dataclass
class IntentCandidate:
    """A semantic match result for an intent category (Tier 1 output)."""
    name: str
    score: float
    description: str = ""


@dataclass
class IntentSlots:
    """Structured intent classification result.

    Replaces the flat dict currently stored in AgentState.intent_slots.
    The to_dict() method maintains backward compatibility with the
    existing 'intent' key that _supervisor_plan() consumes.
    """
    domain: str = "general"
    primary_intent: str = "general"
    secondary_intents: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "regex"  # "regex" | "semantic" | "llm"
    reason: str = ""
    metrics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    requires_user_vector_context: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict compatible with legacy intent_slots consumers.

        Key compatibility: 'intent' maps from primary_intent so
        _supervisor_plan() works without modification.
        """
        return {
            "domain": self.domain,
            "intent": self.primary_intent,  # ← legacy key
            "secondary_intents": self.secondary_intents,
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "metrics": self.metrics,
            "entities": self.entities,
            "requires_user_vector_context": self.requires_user_vector_context,
        }


@dataclass
class IntentRoutingResult:
    """Result of the 3-tier intent routing funnel with diagnostic trace."""
    intent_slots: IntentSlots
    trace: list[dict[str, Any]]


# ── Tier 0: Regex with confidence ─────────────────────────────────────────


def _regex_intent_with_confidence(normalized: str) -> tuple[str, float]:
    """Tier 0: regex intent classification with confidence heuristics.

    Returns (intent, confidence). When confidence < 0.80, the caller
    should proceed to Tier 1 (semantic) instead of short-circuiting.

    Confidence levels:
      - >= 0.90: 2+ signal keywords matched (high confidence)
      - 0.70: exactly 1 signal keyword matched (medium confidence)
      - 0.40: no signal matched (fallback to general)

    Knowledge-context adjustment:
      When the query is a definition / explanation question (e.g. "什么是
      LCP / error rate"), stray ``troubleshoot`` / ``patrol`` signals from
      words like "error" or "告警" are demoted unless accompanied by
      **action** keywords (排查 / 根因 / 配置 / 设置 / …).  This prevents
      knowledge-seeking queries from being misclassified as action intents.
    """
    lowered = normalized.lower()

    # ── Knowledge-context detection ─────────────────────────────────
    # When the query is asking "what is X" / "explain X" / "how to collect X",
    # "error" and "告警" are metric concepts, not action signals.
    knowledge_keywords = re.findall(
        r"什么是|怎么定义|含义|解读|解释|how\s*to\s*collect"
        r"|what\s*is\b|define|definition|采集方法|怎么采集",
        lowered,
    )
    is_knowledge_query = len(knowledge_keywords) > 0

    # ── Count keyword signals per intent ──
    troubleshoot_signals = len(re.findall(
        r"\b(?:rca|root\s*cause|troubleshoot|timeout|slow|error)\b|排查|根因|超时|异常|故障|挂了",
        lowered,
    ))
    patrol_signals = len(re.findall(
        r"\b(?:patrol|health\s*check|alert)\b|巡检|告警|健康检查|监控",
        lowered,
    ))
    audit_signals = len(re.findall(
        r"\b(?:audit|approval|compliance|log)\b|审计|合规|审批|日志|治理",
        lowered,
    ))
    metric_keywords = len(re.findall(
        r"\b(?:metric|web\s*vitals|conversion)\b|指标|转化率|趋势|解读|定义",
        lowered,
    ))
    metric_names = len(re.findall(
        r"\b(?:p50|p75|p90|p95|p99|lcp|cls|inp|ttfb|fid|tbt|apdex|slo)\b", lowered,
    ))
    metrics_signals = metric_keywords + metric_names

    # ── Knowledge-context signal demotion ───────────────────────────
    # In knowledge-question context, demote troubleshoot/patrol signals
    # that lack accompanying **action** keywords.  "什么是 error rate"
    # is a metrics question, not a troubleshoot request.
    if is_knowledge_query:
        has_troubleshoot_action = bool(re.search(
            r"排查|根因|rca|故障|挂了|timeout|slow|超时|异常",
            lowered,
        ))
        if troubleshoot_signals > 0 and not has_troubleshoot_action:
            troubleshoot_signals = 0

        has_patrol_action = bool(re.search(
            r"配置|设置|创建|定时|巡检规则|patrol\s*rule|健康检查\s*任务",
            lowered,
        ))
        if patrol_signals > 0 and not has_patrol_action:
            patrol_signals = 0

    # ── High confidence: 2+ signals ──
    if troubleshoot_signals >= 2:
        return ("troubleshoot", 0.90)
    if patrol_signals >= 2:
        return ("patrol", 0.90)
    if audit_signals >= 2:
        return ("audit", 0.90)
    if metrics_signals >= 2:
        return ("metrics", 0.90)

    # ── Medium confidence: exactly 1 signal ──
    if troubleshoot_signals == 1:
        return ("troubleshoot", 0.70)
    if patrol_signals == 1:
        return ("patrol", 0.70)
    if audit_signals == 1:
        return ("audit", 0.70)
    if metrics_signals == 1:
        return ("metrics", 0.70)

    # ── Fallback ──
    return ("general", 0.40)


# ── Vector helpers (reused from router.py pattern) ─────────────────────────


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors of equal dimension."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Tier 1: Intent Embedding Index ─────────────────────────────────────────
# Reuses the SAME OllamaBgeM3EmbeddingProvider from router.py.
# Each intent's utterances are embedded and mean-pooled into a centroid
# vector. Query-time: embed query → cosine similarity against centroids.


class IntentEmbeddingIndex:
    """In-memory vector index for intent utterances.

    Embeds each utterance in INTENT_UTTERANCES via the provided embedding
    provider, then computes a mean-pooled centroid vector per intent.
    At query time, the user query is embedded and compared against each
    centroid via cosine similarity.

    This is the multi-agent analogue of InMemorySkillVectorIndex in
    router.py — same pattern, different documents to index.
    """

    def __init__(self, embedding_provider) -> None:
        """Args:
        embedding_provider: Any object with `async embed(text) -> list[float]`.
            Typically OllamaBgeM3EmbeddingProvider from router.py.
        """
        self.embedding_provider = embedding_provider
        self._intent_vectors: dict[str, list[float]] = {}

    async def warmup(self) -> None:
        """Pre-compute intent centroid vectors from utterance embeddings.

        Uses mean pooling: embeds each utterance, then averages the
        vectors element-wise to get a single centroid per intent.
        """
        for intent, utterances in INTENT_UTTERANCES.items():
            vectors: list[list[float]] = []
            for utterance in utterances:
                vectors.append(await self.embedding_provider.embed(utterance))
            if not vectors:
                continue
            dim = len(vectors[0])
            avg = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
            self._intent_vectors[intent] = avg

    async def search(self, query: str, top_k: int = 5) -> list[IntentCandidate]:
        """Return intent candidates sorted by descending cosine similarity."""
        if not self._intent_vectors:
            return []
        query_vector = await self.embedding_provider.embed(query)
        candidates: list[IntentCandidate] = []
        for intent, vector in self._intent_vectors.items():
            score = _cosine_similarity(query_vector, vector)
            candidates.append(IntentCandidate(name=intent, score=score))
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: max(1, top_k)]


# ── Tier 2: LLM Intent Classifier ─────────────────────────────────────────


class IntentDecision:
    """LLM structured output for intent classification.

    Uses a plain class (not Pydantic BaseModel) to keep the dependency
    surface minimal and consistent with the dataclass pattern in the
    rest of intent_router.py.
    """

    def __init__(
        self,
        primary_intent: str = "general",
        confidence: float = 0.0,
        reason: str = "",
        secondary_intents: list[str] | None = None,
    ) -> None:
        self.primary_intent = primary_intent
        self.confidence = min(max(float(confidence), 0.0), 1.0)
        self.reason = reason
        self.secondary_intents = secondary_intents or []


# Pre-defined secondary intents per primary intent.
# Mirrors the _supervisor_plan() logic so downstream sub-agent scheduling
# automatically benefits from richer intent signals.
_PRIMARY_TO_SECONDARY: dict[str, list[str]] = {
    "troubleshoot": ["metrics", "audit"],
    "patrol": ["metrics", "audit"],
    "audit": ["metrics"],
    "metrics": [],
    "general": [],
}

INTENT_CLASSIFIER_PROMPT = """你是 APM 意图分类器。分析用户查询，输出结构化分类结果。

## 意图定义

- **troubleshoot**: 排查故障、根因分析、性能异常诊断、RCA
- **patrol**: 巡检规则配置、告警阈值设置、定时健康检查
- **audit**: 执行日志审计、SLA 合规检查、审批记录查询、安全事件审查
- **metrics**: 指标定义/解读、性能数据查询、Web Vitals、业务指标
- **general**: 以上都不匹配的通用查询

## 规则

- 用户可能同时有多个意图，主意图放 primary_intent，次要意图放 secondary_intents
- 如果确实无法判断，primary_intent 设为 "general"，confidence 设为 0.3 以下
- 只输出 JSON，不要解释"""


def _parse_intent_llm_decision(raw: object) -> IntentDecision:
    """Parse LLM response into an IntentDecision with robust fallback.

    Handles: dict, JSON string, Markdown code blocks, AIMessage objects,
    None, and malformed input.
    """
    if raw is None:
        return IntentDecision(primary_intent="general", confidence=0.1, reason="empty response")

    text = ""
    if isinstance(raw, dict):
        text = json.dumps(raw)
    elif hasattr(raw, "content"):
        text = str(getattr(raw, "content", ""))
    else:
        text = str(raw)

    # Strip Markdown code fences (```json ... ```)
    text = text.strip()
    if text.startswith("```"):
        end = text.rfind("```")
        if end > 3:
            newline = text.find("\n")
            if newline != -1 and newline < end:
                text = text[newline + 1 : end].strip()
            else:
                text = text[3:end].strip()

    # Parse JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Intent LLM decision JSON parse failed: %s", text[:200])
        return IntentDecision(primary_intent="general", confidence=0.1, reason="json parse failed")

    if not isinstance(data, dict):
        return IntentDecision(primary_intent="general", confidence=0.1, reason="not a dict")

    primary = str(data.get("primary_intent") or "general")
    confidence = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "")

    # Validate primary intent
    valid_intents = {"troubleshoot", "patrol", "audit", "metrics", "general"}
    if primary not in valid_intents:
        primary = "general"
        confidence = min(confidence, 0.3)

    # Build secondary intents: auto-inferred + explicit from LLM
    secondary = list(_PRIMARY_TO_SECONDARY.get(primary, []))
    explicit = data.get("secondary_intents")
    if isinstance(explicit, list):
        for s in explicit:
            if isinstance(s, str) and s in valid_intents and s != "general" and s not in secondary:
                secondary.append(s)

    return IntentDecision(
        primary_intent=primary,
        secondary_intents=secondary,
        confidence=min(max(confidence, 0.0), 1.0),
        reason=reason,
    )


# ── 3-Tier Funnel Orchestrator ─────────────────────────────────────────────


async def route_intent_with_trace(
    user_text: str,
    *,
    intent_index: IntentEmbeddingIndex | None = None,
    llm=None,
    regex_threshold: float = 0.80,
    semantic_threshold: float = 0.75,
    semantic_top_k: int = 3,
    llm_threshold: float = 0.60,
    existing_slots: dict[str, Any] | None = None,
) -> IntentRoutingResult:
    """3-tier intent routing funnel: regex → semantic → LLM.

    Follows the same 3-tier pattern as the single-agent skill router
    (route_skill_names_with_trace in router.py), adapted for intent
    classification instead of skill selection.

    Args:
        user_text: Raw user query.
        intent_index: Optional IntentEmbeddingIndex for Tier 1.
        llm: Optional LLM for Tier 2 structured output classification.
        regex_threshold: Confidence >= this short-circuits after Tier 0.
        semantic_threshold: Cosine similarity >= this selects after Tier 1.
        semantic_top_k: Max candidates from semantic search.
        llm_threshold: Confidence >= this selects after Tier 2.
        existing_slots: Pre-extracted metrics/entities from legacy regex
            (rewrite_query_and_slots output).

    Returns:
        IntentRoutingResult with classified intent and diagnostic trace.
    """
    normalized = " ".join(user_text.split())
    trace: list[dict[str, Any]] = []

    # Merge pre-extracted metrics/entities from legacy regex
    metrics: list[str] = []
    entities: list[str] = []
    if isinstance(existing_slots, dict):
        metrics = list(existing_slots.get("metrics") or [])
        entities = list(existing_slots.get("entities") or [])

    # ── Tier 0: Regex with confidence ─────────────────────────────────
    regex_intent, regex_conf = _regex_intent_with_confidence(normalized)

    if regex_conf >= regex_threshold and regex_intent != "general":
        trace.append({
            "stage": "regex",
            "status": "selected",
            "intent": regex_intent,
            "confidence": regex_conf,
            "threshold": regex_threshold,
        })
        return IntentRoutingResult(
            intent_slots=IntentSlots(
                domain=_looks_like_apm_domain(normalized),
                primary_intent=regex_intent,
                confidence=regex_conf,
                source="regex",
                reason=f"regex matched with {regex_conf:.0%} confidence",
                metrics=metrics,
                entities=entities,
            ),
            trace=trace,
        )

    trace.append({
        "stage": "regex",
        "status": "below_threshold" if regex_intent != "general" else "missed",
        "intent": regex_intent,
        "confidence": regex_conf,
        "threshold": regex_threshold,
    })

    # ── Tier 1: Semantic Router ──────────────────────────────────────
    if intent_index is not None and normalized.strip():
        try:
            candidates = await intent_index.search(normalized, top_k=semantic_top_k)
        except Exception:
            logger.exception("Intent semantic search failed")
            candidates = []
            trace.append({"stage": "semantic", "status": "failed", "reason": "search error"})

        if candidates:
            top = candidates[0]
            candidate_trace = [
                {"name": c.name, "score": round(c.score, 4)} for c in candidates
            ]
            if top.score >= semantic_threshold:
                trace.append({
                    "stage": "semantic",
                    "status": "selected",
                    "candidates": candidate_trace,
                    "selected_intent": top.name,
                    "threshold": semantic_threshold,
                })
                return IntentRoutingResult(
                    intent_slots=IntentSlots(
                        domain=_looks_like_apm_domain(normalized),
                        primary_intent=top.name,
                        confidence=top.score,
                        source="semantic",
                        reason=f"semantic match score={top.score:.3f}",
                        metrics=metrics,
                        entities=entities,
                    ),
                    trace=trace,
                )
            trace.append({
                "stage": "semantic",
                "status": "below_threshold",
                "candidates": candidate_trace,
                "threshold": semantic_threshold,
            })
        else:
            trace.append({"stage": "semantic", "status": "no_candidates"})
    elif intent_index is None:
        trace.append({"stage": "semantic", "status": "skipped", "reason": "no intent index"})

    # ── Tier 2: LLM Classifier ───────────────────────────────────────
    if llm is not None:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await llm.ainvoke([
                SystemMessage(content=INTENT_CLASSIFIER_PROMPT),
                HumanMessage(content=(
                    f"用户查询: {normalized}\n"
                    f"已提取的指标: {metrics}\n"
                    f"已提取的实体: {entities}"
                )),
            ])
            decision = _parse_intent_llm_decision(response)
        except Exception:
            logger.exception("Intent LLM classification failed")
            decision = IntentDecision(primary_intent="general", confidence=0.1, reason="llm invocation failed")

        trace.append({
            "stage": "llm_judge",
            "status": "selected" if decision.confidence >= llm_threshold else "below_threshold",
            "primary_intent": decision.primary_intent,
            "secondary_intents": decision.secondary_intents,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "threshold": llm_threshold,
        })

        return IntentRoutingResult(
            intent_slots=IntentSlots(
                domain=_looks_like_apm_domain(normalized),
                primary_intent=decision.primary_intent,
                secondary_intents=decision.secondary_intents,
                confidence=decision.confidence,
                source="llm",
                reason=decision.reason,
                metrics=metrics,
                entities=entities,
            ),
            trace=trace,
        )

    trace.append({"stage": "llm_judge", "status": "skipped", "reason": "no llm available"})

    # ── Fallback ──────────────────────────────────────────────────────
    return IntentRoutingResult(
        intent_slots=IntentSlots(
            domain=_looks_like_apm_domain(normalized),
            primary_intent=regex_intent,
            confidence=regex_conf,
            source="regex",
            reason="fallback: regex result used",
            metrics=metrics,
            entities=entities,
        ),
        trace=trace,
    )


def _looks_like_apm_domain(text: str) -> str:
    """Heuristic domain detection: 'apm' vs 'general'."""
    lowered = text.lower()
    if re.search(
        r"\b(?:apm|api|p95|p99|lcp|cls|inp|apdex|slo|rca|patrol|troubleshoot)\b|"
        r"排查|根因|巡检|告警|指标|审计|合规",
        lowered,
    ):
        return "apm"
    return "general"
