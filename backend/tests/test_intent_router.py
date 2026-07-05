"""Tests for multi-agent hybrid intent routing (3-tier funnel)."""
import asyncio
import json

from personal_assistant.agent.intent_router import (
    INTENT_UTTERANCES,
    IntentCandidate,
    IntentSlots,
)


def async_test(coro):
    """Helper to run async tests."""
    return asyncio.run(coro)


# ── Task 1: Schema & utterances ────────────────────────────────────────


def test_intent_utterances_covers_all_intents():
    """每个意图类别都有足够的示例语句（≥6条）"""
    for intent in ("troubleshoot", "patrol", "audit", "metrics"):
        assert intent in INTENT_UTTERANCES, f"missing intent: {intent}"
        assert len(INTENT_UTTERANCES[intent]) >= 6, (
            f"{intent} needs >=6 utterances, got {len(INTENT_UTTERANCES[intent])}"
        )


def test_intent_utterances_have_bilingual_coverage():
    """每个意图至少有一条中文和一条英文示例"""
    for intent, utterances in INTENT_UTTERANCES.items():
        text = " ".join(utterances)
        has_cjk = any("一" <= c <= "鿿" for c in text)
        has_ascii = any(c.isascii() and c.isalpha() for c in text)
        assert has_cjk, f"{intent}: missing Chinese utterance"
        assert has_ascii, f"{intent}: missing English utterance"


def test_intent_slots_defaults():
    """IntentSlots 默认值"""
    slots = IntentSlots()
    assert slots.domain == "general"
    assert slots.primary_intent == "general"
    assert slots.secondary_intents == []
    assert slots.confidence == 0.0
    assert slots.source == "regex"
    assert slots.metrics == []
    assert slots.entities == []


def test_intent_candidate_fields():
    """IntentCandidate 字段"""
    c = IntentCandidate(name="troubleshoot", score=0.85, description="排障")
    assert c.name == "troubleshoot"
    assert c.score == 0.85
    assert c.description == "排障"


def test_intent_slots_to_dict_backward_compatible():
    """to_dict() 输出兼容旧的 intent_slots dict 格式（_supervisor_plan 依赖 'intent' key）"""
    slots = IntentSlots(
        domain="apm",
        primary_intent="troubleshoot",
        secondary_intents=["metrics", "audit"],
        confidence=0.90,
        source="regex",
        metrics=["p99"],
        entities=["checkout"],
    )
    result = slots.to_dict()

    assert result["domain"] == "apm"
    assert result["intent"] == "troubleshoot"  # ← 旧代码依赖
    assert result["metrics"] == ["p99"]
    assert result["entities"] == ["checkout"]
    assert result["requires_user_vector_context"] is True
    assert result["confidence"] == 0.90
    assert result["source"] == "regex"
    assert result["secondary_intents"] == ["metrics", "audit"]


def test_intent_routing_result_holds_slots_and_trace():
    """IntentRoutingResult 包含 slots + trace"""
    from personal_assistant.agent.intent_router import IntentRoutingResult

    slots = IntentSlots(primary_intent="patrol", confidence=0.85, source="semantic")
    trace = [{"stage": "semantic", "status": "selected"}]
    result = IntentRoutingResult(intent_slots=slots, trace=trace)

    assert result.intent_slots.primary_intent == "patrol"
    assert len(result.trace) == 1
    assert result.trace[0]["stage"] == "semantic"


# ── Task 2: Tier 0 regex with confidence ──────────────────────────────

from personal_assistant.agent.intent_router import _regex_intent_with_confidence


def test_regex_intent_troubleshoot_high_confidence():
    """多关键词命中 → 高置信度（≥0.80）"""
    intent, conf = _regex_intent_with_confidence("排查 payment-service 超时 根因分析")
    assert intent == "troubleshoot"
    assert conf >= 0.80


def test_regex_intent_troubleshoot_single_keyword_medium_confidence():
    """单关键词 → 中等置信度"""
    intent, conf = _regex_intent_with_confidence("帮我排查一下")
    assert intent == "troubleshoot"
    assert 0.60 <= conf < 0.80


def test_regex_intent_patrol_single_keyword():
    intent, conf = _regex_intent_with_confidence("巡检")
    assert intent == "patrol"
    assert 0.60 <= conf < 0.80


def test_regex_intent_patrol_multi_keyword_high():
    intent, conf = _regex_intent_with_confidence("配置巡检告警规则")
    assert intent == "patrol"
    assert conf >= 0.80


def test_regex_intent_metrics_with_metric_names():
    """包含指标名 → metrics"""
    intent, conf = _regex_intent_with_confidence("帮我查下 p99 和 LCP")
    assert intent == "metrics"
    assert conf >= 0.80


def test_regex_intent_metrics_single_metric():
    intent, conf = _regex_intent_with_confidence("p99")
    assert intent == "metrics"
    assert 0.60 <= conf < 0.80


def test_regex_intent_general_fallback():
    """不匹配任何意图 → general 低置信"""
    intent, conf = _regex_intent_with_confidence("你好，帮我介绍一下系统功能")
    assert intent == "general"
    assert conf < 0.60


def test_regex_intent_audit_multi_keyword():
    intent, conf = _regex_intent_with_confidence("审计一下合规情况")
    assert intent == "audit"
    assert conf >= 0.80


def test_regex_intent_audit_single_keyword():
    intent, conf = _regex_intent_with_confidence("审计")
    assert intent == "audit"
    assert 0.60 <= conf < 0.80


def test_regex_intent_case_insensitive():
    """大小写不敏感"""
    intent, conf = _regex_intent_with_confidence("RCA TIMEOUT ERROR")
    assert intent == "troubleshoot"
    assert conf >= 0.80


# ── Task 3: IntentEmbeddingIndex ──────────────────────────────────────

from personal_assistant.agent.intent_router import IntentEmbeddingIndex


class FakeEmbeddingProvider:
    """Fake embedding provider — deterministic vectors per text via hash."""

    async def embed(self, text: str) -> list[float]:
        h = hash(text) % 1000
        return [float((h + i) % 100) / 100.0 for i in range(8)]


def test_intent_index_warmup_and_search():
    """预热后 search() 返回意图候选"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)

    async_test(index.warmup())

    candidates = async_test(index.search("排查服务超时问题", top_k=3))
    assert len(candidates) >= 1
    assert candidates[0].name in ("troubleshoot", "patrol", "audit", "metrics")
    assert 0.0 <= candidates[0].score <= 1.0


def test_intent_index_only_searches_defined_intents():
    """不会返回未定义的意图"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    candidates = async_test(index.search("random query", top_k=10))
    for c in candidates:
        assert c.name in ("troubleshoot", "patrol", "audit", "metrics")


def test_intent_index_top_k_respected():
    """top_k 限制返回数量"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    candidates = async_test(index.search("anything", top_k=2))
    assert len(candidates) <= 2


def test_intent_index_empty_before_warmup():
    """warmup() 之前 search() 返回空列表"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)

    candidates = async_test(index.search("anything"))
    assert candidates == []


def test_intent_index_search_returns_sorted_by_score():
    """结果按相似度降序排列"""
    provider = FakeEmbeddingProvider()
    index = IntentEmbeddingIndex(provider)
    async_test(index.warmup())

    candidates = async_test(index.search("test query", top_k=4))
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


# ── Task 4: Tier 2 LLM classifier ─────────────────────────────────────

from personal_assistant.agent.intent_router import (
    IntentDecision,
    INTENT_CLASSIFIER_PROMPT,
    _parse_intent_llm_decision,
)


def test_parse_intent_llm_decision_dict():
    """解析字典格式"""
    decision = _parse_intent_llm_decision({
        "primary_intent": "troubleshoot",
        "confidence": 0.9,
        "reason": "用户明确要求排查故障",
    })
    assert decision.primary_intent == "troubleshoot"
    assert decision.confidence == 0.9
    assert decision.reason == "用户明确要求排查故障"
    # troubleshoot 的 secondary 应该是 metrics + audit
    assert "metrics" in decision.secondary_intents
    assert "audit" in decision.secondary_intents


def test_parse_intent_llm_decision_json_string():
    """解析 JSON 字符串"""
    decision = _parse_intent_llm_decision(
        '{"primary_intent": "patrol", "confidence": 0.85, "reason": "巡检规则配置"}'
    )
    assert decision.primary_intent == "patrol"
    assert decision.confidence == 0.85


def test_parse_intent_llm_decision_invalid_fallback():
    """非法输入 → general fallback"""
    decision = _parse_intent_llm_decision(None)
    assert decision.primary_intent == "general"
    assert decision.confidence < 0.3


def test_parse_intent_llm_decision_markdown_code_block():
    """解析 Markdown 代码块包裹的 JSON"""
    decision = _parse_intent_llm_decision(
        '```json\n{"primary_intent": "metrics", "confidence": 0.7}\n```'
    )
    assert decision.primary_intent == "metrics"


def test_parse_intent_llm_decision_unknown_intent_fallback():
    """未知意图 → general fallback"""
    decision = _parse_intent_llm_decision(
        {"primary_intent": "unknown_thing", "confidence": 0.99}
    )
    assert decision.primary_intent == "general"
    assert decision.confidence <= 0.3


def test_parse_intent_llm_decision_explicit_secondary():
    """LLM 显式返回 secondary_intents"""
    decision = _parse_intent_llm_decision({
        "primary_intent": "troubleshoot",
        "confidence": 0.88,
        "secondary_intents": ["patrol"],
    })
    assert decision.primary_intent == "troubleshoot"
    # 自动推断 + 显式指定
    assert "patrol" in decision.secondary_intents
    assert "metrics" in decision.secondary_intents


def test_classifier_prompt_contains_all_intents():
    """Prompt 中定义所有意图"""
    assert "troubleshoot" in INTENT_CLASSIFIER_PROMPT
    assert "patrol" in INTENT_CLASSIFIER_PROMPT
    assert "audit" in INTENT_CLASSIFIER_PROMPT
    assert "metrics" in INTENT_CLASSIFIER_PROMPT
    assert "general" in INTENT_CLASSIFIER_PROMPT


def test_intent_decision_pydantic_validation():
    """Pydantic 模型验证"""
    decision = IntentDecision(
        primary_intent="audit",
        confidence=0.75,
        secondary_intents=["metrics"],
        reason="合规审计",
    )
    assert decision.primary_intent == "audit"
    assert decision.secondary_intents == ["metrics"]
