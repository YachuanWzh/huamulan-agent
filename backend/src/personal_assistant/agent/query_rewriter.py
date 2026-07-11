"""Enhanced query rewriting for single-agent and multi-agent paths.

Core capabilities (all config-gated, backward compatible):
- Coreference resolution: Resolve pronouns using conversation history
- Slot filling: Enrich ambiguous queries with context from history
- Semantic normalization: Convert colloquial expressions to standard forms
- Multi-intent splitting: Detect and split compound queries
- Confidence scoring: Rate rewrite quality for downstream gating
- Observability: Log original→rewritten pairs for quality tracking

Architecture follows the project's 3-tier pattern (regex → semantic → LLM)
established in ``intent_router.py`` and ``router.py``, with the LLM tier now
doing actual semantic rewriting instead of just intent classification.

Reuses:
- ``build_llm`` from ``llm.py`` for LLM construction (ChatDeepSeek)
- ``rewrite_query_and_slots`` from ``multi_agent.py`` for fast regex extraction
- ``_regex_intent_with_confidence`` from ``intent_router.py`` for intent signals
- Pydantic ``BaseModel`` for structured LLM output (pattern from router.py, harness.py)
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ── Data Models ─────────────────────────────────────────────────────────────


class MultiIntentSubQuery(BaseModel):
    """A single sub-query split from a multi-intent query."""

    sub_query: str = Field(description="Rewritten sub-query text")
    intent: str = Field(
        default="general",
        description="Intent classification: troubleshoot|patrol|audit|metrics|general",
    )


class QueryRewriteDecision(BaseModel):
    """LLM structured output for query rewriting.

    Follows the project's "JSON-over-prompt" pattern:
    prompt describes schema → LLM returns text JSON → post-parse with Pydantic.

    References:
        LLMSkillRouteDecision (router.py:305)
        LLMPromptGuardDecision (harness.py:54)
    """

    rewritten: str = Field(description="The rewritten/standardized query text")
    intent: str = Field(
        default="general",
        description="Primary intent: troubleshoot|patrol|audit|metrics|general",
    )
    secondary_intents: list[str] = Field(
        default_factory=list,
        description="Secondary intents detected in the query",
    )
    confidence: float = Field(
        default=0.80,
        description="Confidence in the rewrite quality (0.0-1.0). Clamped to [0, 1] by validator.",
    )
    needs_clarification: bool = Field(
        default=False,
        description="True when critical slots are missing and clarification is needed",
    )
    missing_slots: list[str] = Field(
        default_factory=list,
        description="Names of required slots that could not be filled",
    )
    sub_queries: list[MultiIntentSubQuery] = Field(
        default_factory=list,
        description="Split sub-queries when multi-intent detected",
    )
    reason: str = Field(
        default="",
        description="Explanation of what was rewritten and why (for observability)",
    )
    metrics: list[str] = Field(
        default_factory=list,
        description="Metric names extracted from the query",
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Entity names extracted from the query",
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: object) -> float:
        """Validator to clamp confidence to [0.0, 1.0] instead of rejecting."""
        if isinstance(v, (int, float)):
            return max(0.0, min(1.0, float(v)))
        return 0.80  # default for non-numeric input


@dataclass
class RewrittenQuery:
    """Result of query rewriting, consumed by both single and multi-agent paths.

    Follows the @dataclass result pattern from:
        SkillRoutingResult (router.py:269)
        IntentRoutingResult (intent_router.py:122)
    """

    original: str = ""  # Original user query (preserved for response generation)
    rewritten: str = ""  # Rewritten query (for downstream execution)
    intent: str = "general"  # Primary intent classification
    secondary_intents: list[str] = field(default_factory=list)
    confidence: float = 0.0  # Rewrite confidence 0.0-1.0
    needs_clarification: bool = False  # Whether to ask user for missing info
    missing_slots: list[str] = field(default_factory=list)  # Slots to clarify
    sub_queries: list[str] = field(default_factory=list)  # Multi-intent splits
    reason: str = ""  # Rewrite rationale (observability)
    metrics: list[str] = field(default_factory=list)  # Extracted metric names
    entities: list[str] = field(default_factory=list)  # Extracted entity names


# ── Conversation History Extraction ─────────────────────────────────────────


def extract_conversation_context(
    messages: list[Any],
    max_turns: int = 3,
    max_chars_per_message: int = 500,
) -> str:
    """Extract recent conversation turns for coreference resolution.

    Filters ``state["messages"]`` to keep only HumanMessage and AIMessage
    (skipping ToolMessage and SystemMessage), extracts the most recent N
    user/assistant pairs, and returns them as a formatted string.

    Args:
        messages: LangChain message list from ``state["messages"]``.
        max_turns: Max number of user/assistant pairs to include.
        max_chars_per_message: Truncate each message to this length.

    Returns:
        Formatted conversation context string, empty if no human messages found.

    Pattern reference:
        ``_last_human_text`` (agent.py:823, multi_agent.py:625)
        ``route_skills`` history extraction (router.py:733-737)
    """
    # Filter to human + AI messages only
    filtered: list[dict[str, str]] = []
    for m in messages:
        msg_type = getattr(m, "type", "")
        content = str(getattr(m, "content", "") or "").strip()
        if not content:
            continue
        if msg_type == "human":
            filtered.append({"role": "user", "content": content})
        elif msg_type == "ai":
            filtered.append({"role": "assistant", "content": content})

    if not filtered:
        return ""

    # Take last N turns (a turn = one user message)
    user_indices = [i for i, entry in enumerate(filtered) if entry["role"] == "user"]
    if not user_indices:
        return ""

    start_idx = max(0, user_indices[0] if len(user_indices) <= max_turns else user_indices[-max_turns])
    recent = filtered[start_idx:]

    # Format and truncate
    parts: list[str] = []
    for entry in recent:
        content = entry["content"]
        if len(content) > max_chars_per_message:
            content = content[:max_chars_per_message] + "..."
        parts.append(f"{entry['role']}: {content}")

    return "\n".join(parts)


# ── Fast Regex Rewrite (No LLM) ─────────────────────────────────────────────


# Lightweight colloquial → standard mappings, applied as a single pass via
# regex substitution. Order matters: longer / more specific patterns first so
# e.g. "帮我搞个" beats "帮我" alone. Only patterns that produce a visibly
# different rewrite (whitespace alone is not enough) are included.
_COLLOQUIAL_PATTERNS = re.compile(
    r"我想看看|我想看下|我想了解下|我想了解|帮我看看|帮我查下|帮我查一下|看看有没有|看看怎么|搞个|搞一个"
)


def _colloquial_repl(match: re.Match[str]) -> str:
    """Map a colloquial Chinese phrase to its standard form."""
    phrase = match.group(0)
    mapping = {
        "我想看看": "查看",
        "我想看下": "查看",
        "我想了解下": "了解",
        "我想了解": "了解",
        "帮我看看": "查看",
        "帮我查下": "查询",
        "帮我查一下": "查询",
        "看看有没有": "查询是否存在",
        "看看怎么": "查询如何",
        "搞个": "创建一个",
        "搞一个": "创建一个",
    }
    return mapping.get(phrase, phrase)


def _unique(items: list[str]) -> list[str]:
    """Deduplicate preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        lower = item.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(item)
    return result


def rewrite_query_fast(query: str) -> RewrittenQuery:
    """Pure regex/rule-based rewrite, no LLM call. Used as fallback.

    Reuses ``rewrite_query_and_slots`` from ``multi_agent.py`` for
    metrics/entities extraction and ``_regex_intent_with_confidence``
    from ``intent_router.py`` for intent classification.

    Applies basic colloquial → standard normalization so even the
    fallback path produces a visible rewrite for informal Chinese
    (e.g. "我想看看 X" → "查看 X").

    Args:
        query: Raw user query.

    Returns:
        RewrittenQuery with normalized text, regex-extracted
        metrics/entities, and signal-counting intent classification.
    """
    from personal_assistant.agent.intent_router import _regex_intent_with_confidence
    from personal_assistant.agent.multi_agent import _looks_like_apm

    # 1) Whitespace normalization
    normalized = " ".join(query.split())

    # 2) Colloquial → standard normalization (order matters: longer patterns first)
    normalized = _COLLOQUIAL_PATTERNS.sub(_colloquial_repl, normalized).strip()

    lowered = normalized.lower()

    # Extract metric names via regex
    metrics = _unique(
        match.group(0).lower()
        for match in re.finditer(
            r"\b(?:p50|p75|p90|p95|p99|lcp|cls|inp|ttfb|fid|tbt|apdex|slo)\b",
            lowered,
        )
    )

    # Intent from signal-counting heuristics
    intent, confidence = _regex_intent_with_confidence(normalized)

    # Extract potential entity names
    entities = _unique(
        token
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", normalized)
        if token.lower() not in {"api", "apm", "rca", *metrics}
    )

    return RewrittenQuery(
        original=query,
        rewritten=normalized,
        intent=intent,
        confidence=confidence,
        reason=f"fast regex rewrite: intent={intent} conf={confidence:.0%}",
        metrics=metrics,
        entities=entities,
    )


# ── LLM Prompt ──────────────────────────────────────────────────────────────

QUERY_REWRITE_PROMPT = """你是 APM 平台的查询改写专家。你的任务是将用户口语化、省略、多义的原始查询，改写为下游系统能精准理解和执行的标准化查询。

## 改写场景

1. **指代消解**: 利用对话历史解析代词。"它怎么样？" → 结合上文改写成 "payment-service 的 p99 延迟怎么样？"
2. **槽位填充**: 补全省略的上下文信息。"查下延迟" → "查下 [从历史推断的服务名] 的延迟"
3. **语义标准化**: 口语转标准。"搞个巡检" → "创建巡检规则"；"看看有没有异常" → "排查异常"
4. **多意图拆分**: 检测并拆分复合查询。"查下错误率顺便看看日志" → 拆成两个子查询
5. **情绪/冗余去除**: 去掉敬语、感叹等无信息词汇但保留核心意图

## 规则

- 改写后的 query 必须比原始 query 更易于工具/RAG 系统执行
- 如果缺少关键信息（服务名、指标名等），设置 needs_clarification=true 并列出 missing_slots
- 不要捏造信息补全槽位——只能从对话历史中提取
- 如果查询已经很清晰无需改写，rewritten 可以和 original 相同，**但此时 confidence 必须=1.0**。
  如果 confidence < 1.0，意味着你也认为改写不完整，应该继续尝试给出可执行的改写。
- 对口语化/模糊查询，至少做语义标准化改写（"我想看看 X" → "查看 X"、"搞个" → "创建"等），不能完全照搬原文。
- 保持改写后 query 简洁、无歧义
- 只输出 JSON，不要 Markdown、不要解释

## Few-Shot 示例

### 示例 1: 指代消解
输入: {"history": "user: payment-service 的 p99 延迟多少？\\nassistant: payment-service p99 延迟为 350ms", "query": "它超时了怎么办？"}
输出: {"rewritten": "payment-service 超时了怎么办？如何排查超时问题？", "intent": "troubleshoot", "secondary_intents": [], "confidence": 0.95, "needs_clarification": false, "missing_slots": [], "sub_queries": [], "reason": "将代词'它'解析为对话历史中的 payment-service", "metrics": [], "entities": ["payment-service"]}

### 示例 2: 语义标准化
输入: {"history": "", "query": "帮我搞个巡检看看服务健康不"}
输出: {"rewritten": "创建巡检规则检查所有服务的健康状态", "intent": "patrol", "secondary_intents": [], "confidence": 0.90, "needs_clarification": false, "missing_slots": [], "sub_queries": [], "reason": "口语'搞个巡检'标准化为'创建巡检规则'，'健康不'标准化为'健康状态'", "metrics": [], "entities": []}

### 示例 3: 多意图拆分
输入: {"history": "", "query": "查下 payment-service 的 p99 顺便审计最近的执行日志"}
输出: {"rewritten": "查询 payment-service 的 p99 延迟并审计最近的执行日志", "intent": "troubleshoot", "secondary_intents": ["audit"], "confidence": 0.88, "needs_clarification": false, "missing_slots": [], "sub_queries": [{"sub_query": "查询 payment-service 的 p99 延迟", "intent": "troubleshoot"}, {"sub_query": "审计最近的执行日志", "intent": "audit"}], "reason": "检测到'顺便'分隔的复合意图", "metrics": ["p99"], "entities": ["payment-service"]}

### 示例 4: 槽位缺失需追问
输入: {"history": "", "query": "查下延迟"}
输出: {"rewritten": "查询延迟", "intent": "metrics", "secondary_intents": [], "confidence": 0.40, "needs_clarification": true, "missing_slots": ["service_name", "metric_name"], "sub_queries": [], "reason": "缺少服务名和具体指标名，无法精确查询", "metrics": [], "entities": []}

### 示例 5: 无需改写
输入: {"history": "", "query": "排查 payment-service 超时根因"}
输出: {"rewritten": "排查 payment-service 超时根因", "intent": "troubleshoot", "secondary_intents": [], "confidence": 1.0, "needs_clarification": false, "missing_slots": [], "sub_queries": [], "reason": "查询已足够清晰无需改写", "metrics": [], "entities": ["payment-service"]}

## 输出 Schema

{
  "rewritten": "<改写后的标准化查询文本>",
  "intent": "troubleshoot|patrol|audit|metrics|general",
  "secondary_intents": ["<次要意图>"],
  "confidence": 0.0-1.0,
  "needs_clarification": true|false,
  "missing_slots": ["<缺失槽位名>"],
  "sub_queries": [{"sub_query": "<子查询文本>", "intent": "<子查询意图>"}],
  "reason": "<改写理由简述>",
  "metrics": ["<提取的指标名>"],
  "entities": ["<提取的实体名>"]
}

## 输入

{rewrite_input}"""


# ── LLM Response Parsing ────────────────────────────────────────────────────


def _extract_json_object(text: str) -> str:
    """Extract JSON object from LLM text output, stripping Markdown fences.

    Pattern reference: router.py:1542-1551, harness.py:1056-1066
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]


def _parse_rewrite_llm_response(raw: object, original_query: str) -> QueryRewriteDecision:
    """Parse LLM response into QueryRewriteDecision with robust fallback.

    Handles: dict, JSON string, AIMessage, Markdown code blocks,
    None, and malformed input. On parse failure returns identity rewrite.

    Pattern reference:
        _parse_intent_llm_decision (intent_router.py:349-410)
        _parse_llm_route_decision (router.py:1529-1539)
    """
    if raw is None:
        return QueryRewriteDecision(
            rewritten=original_query,
            intent="general",
            confidence=0.1,
            reason="empty LLM response",
        )

    text = ""
    if isinstance(raw, dict):
        text = json.dumps(raw)
    elif hasattr(raw, "content"):
        text = str(getattr(raw, "content", ""))
    else:
        text = str(raw)

    if not text.strip():
        return QueryRewriteDecision(
            rewritten=original_query,
            intent="general",
            confidence=0.1,
            reason="empty response content",
        )

    # Strip Markdown code fences
    json_text = _extract_json_object(text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("Query rewrite JSON parse failed: %s", text[:200])
        return QueryRewriteDecision(
            rewritten=original_query,
            intent="general",
            confidence=0.1,
            reason=f"json parse failed: {text[:100]}",
        )

    if not isinstance(data, dict):
        return QueryRewriteDecision(
            rewritten=original_query,
            intent="general",
            confidence=0.1,
            reason="response not a dict",
        )

    # Validate and normalize
    try:
        # Convert sub_queries to MultiIntentSubQuery list
        raw_subs = data.get("sub_queries", [])
        subs: list[MultiIntentSubQuery] = []
        if isinstance(raw_subs, list):
            for s in raw_subs:
                if isinstance(s, dict):
                    subs.append(MultiIntentSubQuery(
                        sub_query=str(s.get("sub_query", "")),
                        intent=str(s.get("intent", "general")),
                    ))

        decision = QueryRewriteDecision(
            rewritten=str(data.get("rewritten") or original_query),
            intent=str(data.get("intent") or "general"),
            secondary_intents=[
                str(si) for si in (data.get("secondary_intents") or [])
                if isinstance(si, str)
            ],
            confidence=float(data.get("confidence", 0.80)),
            needs_clarification=bool(data.get("needs_clarification", False)),
            missing_slots=[
                str(s) for s in (data.get("missing_slots") or [])
                if isinstance(s, str)
            ],
            sub_queries=subs,
            reason=str(data.get("reason") or ""),
            metrics=[
                str(m) for m in (data.get("metrics") or [])
                if isinstance(m, str)
            ],
            entities=[
                str(e) for e in (data.get("entities") or [])
                if isinstance(e, str)
            ],
        )
        return decision
    except Exception as exc:
        logger.warning("Query rewrite decision construction failed: %s", exc)
        return QueryRewriteDecision(
            rewritten=original_query,
            intent="general",
            confidence=0.1,
            reason=f"decision construction failed: {exc}",
        )


# ── QueryRewriter Class ─────────────────────────────────────────────────────


class QueryRewriter:
    """Unified query rewriting for single-agent and multi-agent paths.

    Handles: coreference resolution, slot filling, semantic normalization,
    multi-intent splitting, and confidence scoring.

    When ``enabled=False`` (default), returns identity rewrite with zero
    LLM calls — fully backward compatible. When enabled, makes a single
    LLM call that covers all enabled features in one round trip.

    Pattern reference:
        IntentEmbeddingIndex (intent_router.py:248-294) — class with
        async methods, embedding provider dependency, warmup/search pattern
    """

    def __init__(
        self,
        llm,
        enabled: bool = False,
        coreference_enabled: bool = False,
        slot_filling_enabled: bool = False,
        multi_intent_enabled: bool = False,
        semantic_normalize_enabled: bool = False,
        history_max_turns: int = 3,
        rewrite_confidence_threshold: float = 0.60,
    ) -> None:
        """Args:
            llm: LangChain ChatDeepSeek instance from build_llm().
            enabled: Master switch — False means identity rewrite.
            coreference_enabled: Resolve pronouns using conversation history.
            slot_filling_enabled: Fill missing slots from history.
            multi_intent_enabled: Detect and split compound queries.
            semantic_normalize_enabled: Normalize colloquial expressions.
            history_max_turns: Max conversation turns for context extraction.
            rewrite_confidence_threshold: Min confidence to accept LLM rewrite
                (below this, fall back to fast regex rewrite).
        """
        self.llm = llm
        self.enabled = enabled
        self.coreference_enabled = coreference_enabled
        self.slot_filling_enabled = slot_filling_enabled
        self.multi_intent_enabled = multi_intent_enabled
        self.semantic_normalize_enabled = semantic_normalize_enabled
        self.history_max_turns = history_max_turns
        self.rewrite_confidence_threshold = rewrite_confidence_threshold

    async def rewrite(
        self,
        user_query: str,
        *,
        history: list[dict[str, str]] | None = None,
    ) -> RewrittenQuery:
        """Main entry point: rewrite a user query with optional history.

        Args:
            user_query: Raw user query text.
            history: Optional conversation history as list of
                ``{"role": "user"|"assistant", "content": "..."}`` dicts.

        Returns:
            RewrittenQuery with original, rewritten, intent, confidence, etc.
        """
        # Fast path: disabled → identity rewrite
        if not self.enabled:
            return RewrittenQuery(
                original=user_query,
                rewritten=user_query,
                intent="general",
                confidence=1.0,
                reason="rewriter disabled",
            )

        # Always run fast regex for baseline metrics/entities extraction
        fast = rewrite_query_fast(user_query)

        # Build LLM input
        history_str = ""
        if history:
            history_str = "\n".join(
                f"{h['role']}: {h['content']}" for h in history[-self.history_max_turns * 2:]
            )

        rewrite_input = json.dumps({
            "history": history_str,
            "query": user_query,
        }, ensure_ascii=False)

        prompt = QUERY_REWRITE_PROMPT.replace("{rewrite_input}", rewrite_input)

        # Call LLM
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            decision = _parse_rewrite_llm_response(response, user_query)
        except Exception:
            logger.exception("Query rewrite LLM call failed, falling back to regex")
            return fast

        # Guard: if LLM returned rewritten==original with confidence < 1.0 AND did not
        # split sub_queries (the only structural value worth preserving at the
        # rewrite level), demote to regex fallback. missing_slots alone don't
        # count as a rewrite — the user still sees no visible text change.
        identity_rewrite = (
            decision.rewritten.strip() == user_query.strip()
        )
        if identity_rewrite and decision.confidence < 0.99 and not decision.sub_queries:
            logger.info(
                "LLM returned identity rewrite with conf=%.2f (<1.0) and no "
                "sub_queries — demoting to regex fallback",
                decision.confidence,
            )
            fast.missing_slots = decision.missing_slots
            fast.needs_clarification = decision.needs_clarification
            fast.reason = (
                f"LLM identity rewrite at conf={decision.confidence:.2f}, "
                "regex fallback applied"
            )
            return fast

        # Check confidence threshold
        if decision.confidence < self.rewrite_confidence_threshold:
            logger.info(
                "Query rewrite confidence %.2f below threshold %.2f, using regex fallback",
                decision.confidence,
                self.rewrite_confidence_threshold,
            )
            # Merge LLM entity detection with regex metrics
            fast.missing_slots = decision.missing_slots
            fast.needs_clarification = decision.needs_clarification
            fast.reason = f"LLM low confidence ({decision.confidence:.2f}), regex fallback"
            return fast

        # Build final result: LLM rewrite + regex-extracted metrics/entities enrichment
        metrics = _unique(list(decision.metrics) + fast.metrics)
        entities = _unique(list(decision.entities) + fast.entities)

        # Extract sub_queries as plain strings
        sub_queries = [sq.sub_query for sq in decision.sub_queries]

        result = RewrittenQuery(
            original=user_query,
            rewritten=decision.rewritten,
            intent=decision.intent,
            secondary_intents=list(decision.secondary_intents),
            confidence=decision.confidence,
            needs_clarification=decision.needs_clarification,
            missing_slots=list(decision.missing_slots),
            sub_queries=sub_queries,
            reason=decision.reason,
            metrics=metrics,
            entities=entities,
        )

        logger.info(
            "Query rewrite: original=%r → rewritten=%r confidence=%.2f "
            "intent=%s metrics=%s entities=%s",
            result.original,
            result.rewritten,
            result.confidence,
            result.intent,
            result.metrics,
            result.entities,
        )

        return result
