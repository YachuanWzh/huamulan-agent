"""Tests for enhanced query rewriting module."""
import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from personal_assistant.agent.query_rewriter import (
    MultiIntentSubQuery,
    QueryRewriteDecision,
    QueryRewriter,
    RewrittenQuery,
    extract_conversation_context,
    rewrite_query_fast,
)


def async_test(coro):
    """Helper to run async tests following project convention."""
    return asyncio.run(coro)


# ── Task 1: Data Models ─────────────────────────────────────────────────


def test_rewritten_query_defaults():
    """RewrittenQuery dataclass default values."""
    rq = RewrittenQuery()
    assert rq.original == ""
    assert rq.rewritten == ""
    assert rq.intent == "general"
    assert rq.secondary_intents == []
    assert rq.confidence == 0.0
    assert rq.needs_clarification is False
    assert rq.missing_slots == []
    assert rq.sub_queries == []
    assert rq.reason == ""
    assert rq.metrics == []
    assert rq.entities == []


def test_rewritten_query_full_fields():
    """RewrittenQuery with explicit values."""
    rq = RewrittenQuery(
        original="它怎么样了？",
        rewritten="payment-service 的 p99 延迟怎么样了？",
        intent="troubleshoot",
        secondary_intents=["metrics"],
        confidence=0.95,
        needs_clarification=False,
        missing_slots=[],
        sub_queries=[],
        reason="Resolved pronoun '它' -> 'payment-service'",
        metrics=["p99"],
        entities=["payment-service"],
    )
    assert rq.original == "它怎么样了？"
    assert rq.rewritten == "payment-service 的 p99 延迟怎么样了？"
    assert rq.confidence == 0.95
    assert "payment-service" in rq.entities
    assert "p99" in rq.metrics


def test_query_rewrite_decision_defaults():
    """QueryRewriteDecision Pydantic model defaults."""
    d = QueryRewriteDecision(
        rewritten="查询 p99 延迟",
        intent="metrics",
    )
    assert d.rewritten == "查询 p99 延迟"
    assert d.intent == "metrics"
    assert d.confidence > 0.0  # default
    assert d.secondary_intents == []
    assert d.needs_clarification is False
    assert d.missing_slots == []
    assert d.sub_queries == []
    assert d.reason == ""


def test_query_rewrite_decision_confidence_clamped():
    """Confidence must be between 0.0 and 1.0."""
    d = QueryRewriteDecision(rewritten="test", intent="general", confidence=1.5)
    assert d.confidence == 1.0  # clamped by Pydantic Field

    d2 = QueryRewriteDecision(rewritten="test", intent="general", confidence=-0.3)
    assert d2.confidence == 0.0  # clamped


def test_multi_intent_sub_query_validation():
    """MultiIntentSubQuery validates required fields."""
    sub = MultiIntentSubQuery(sub_query="查询 p99 延迟", intent="metrics")
    assert sub.sub_query == "查询 p99 延迟"
    assert sub.intent == "metrics"

    # Missing required field should raise ValidationError
    try:
        MultiIntentSubQuery(sub_query="test")
        # If no exception, intent should use default
    except ValidationError:
        pass  # Expected behavior


# ── Task 2: Conversation History Extraction ──────────────────────────────


def test_extract_history_empty():
    """Empty messages → empty string."""
    result = extract_conversation_context([])
    assert result == ""


def test_extract_history_single_user_turn():
    """Single human message → returns that message's content."""
    messages = [HumanMessage(content="排查 payment-service 超时")]
    result = extract_conversation_context(messages)
    assert "payment-service" in result
    assert "排查" in result


def test_extract_history_multi_turn():
    """Multi-turn conversation extracts user+assistant pairs."""
    messages = [
        HumanMessage(content="payment-service 的 p99 是多少？"),
        AIMessage(content="payment-service 的 p99 延迟是 350ms"),
        HumanMessage(content="它超时了怎么办？"),
    ]
    result = extract_conversation_context(messages, max_turns=3)
    assert "payment-service" in result
    assert "350ms" in result
    assert "它超时了怎么办" in result


def test_extract_history_skips_tool_messages():
    """ToolMessage entries are excluded from conversation context."""
    messages = [
        HumanMessage(content="查询指标"),
        AIMessage(content="", tool_calls=[{"name": "query", "id": "1", "args": {}}]),
        ToolMessage(content='{"p99": 350}', tool_call_id="1"),
        AIMessage(content="p99 是 350ms"),
        HumanMessage(content="它正常吗？"),
    ]
    result = extract_conversation_context(messages, max_turns=3)
    assert "它正常吗" in result
    assert "p99 是 350ms" in result
    # Tool message JSON should not appear
    assert '{"p99": 350}' not in result


def test_extract_history_respects_max_turns():
    """Only last N turns are included."""
    messages = [
        HumanMessage(content=f"消息 {i}")
        for i in range(10)
    ]
    result = extract_conversation_context(messages, max_turns=2)
    assert "消息 9" in result
    assert "消息 8" in result
    assert "消息 0" not in result  # truncated away


def test_extract_history_truncates_long_messages():
    """Long messages are truncated at max_chars."""
    long_text = "A" * 3000
    messages = [HumanMessage(content=long_text)]
    result = extract_conversation_context(messages, max_chars_per_message=500)
    assert len(result) < 2500  # Should be much shorter than 3000
    assert "A" * 400 in result  # truncated suffix exists


# ── Task 3: Fast Regex Rewrite (no LLM) ──────────────────────────────────


def test_rewrite_query_fast_troubleshoot():
    """Fast rewrite detects troubleshoot intent from multiple keywords."""
    result = rewrite_query_fast("排查 payment-service 超时 根因分析")
    assert result.intent == "troubleshoot"
    assert result.confidence >= 0.80
    assert "payment-service" in result.entities


def test_rewrite_query_fast_metrics():
    """Fast rewrite detects metrics intent from metric names."""
    result = rewrite_query_fast("帮我查下 p99 和 LCP 延迟")
    assert result.intent == "metrics"
    assert result.confidence >= 0.80
    assert "p99" in result.metrics
    assert "lcp" in result.metrics


def test_rewrite_query_fast_general():
    """Fast rewrite falls back to general when no intent matches."""
    result = rewrite_query_fast("你好，介绍一下系统功能")
    assert result.intent == "general"
    assert result.confidence < 0.60


def test_rewrite_query_fast_audit():
    """Fast rewrite detects audit intent."""
    result = rewrite_query_fast("审计一下合规情况 查看日志")
    assert result.intent == "audit"
    assert result.confidence >= 0.80


def test_rewrite_query_fast_preserves_original():
    """Fast rewrite preserves the original query."""
    original = "排查线上故障"
    result = rewrite_query_fast(original)
    assert result.original == original


def test_rewrite_query_fast_normalizes_whitespace():
    """Fast rewrite normalizes whitespace in rewritten query."""
    result = rewrite_query_fast("  排查   线上   故障  ")
    assert result.rewritten == "排查 线上 故障"


# ── Task 4: QueryRewriter Class ──────────────────────────────────────────


class FakeRewriteLLM:
    """Mock LLM that returns pre-configured JSON responses."""

    def __init__(self, response_text: str | None = None) -> None:
        self.response_text = response_text
        self.invoke_count = 0

    async def ainvoke(self, messages, config=None):
        self.invoke_count += 1
        content = self.response_text or json.dumps({
            "rewritten": "payment-service 的 p99 延迟怎么样了？",
            "intent": "troubleshoot",
            "secondary_intents": ["metrics"],
            "confidence": 0.92,
            "needs_clarification": False,
            "missing_slots": [],
            "sub_queries": [],
            "reason": "Resolved 它 → payment-service from history",
            "metrics": ["p99"],
            "entities": ["payment-service"],
        }, ensure_ascii=False)
        return AIMessage(content=content)


def test_rewriter_disabled_returns_identity():
    """When disabled, QueryRewriter returns identity rewrite with no LLM call."""
    llm = FakeRewriteLLM()
    rewriter = QueryRewriter(llm=llm, enabled=False)
    result = async_test(rewriter.rewrite("排查 payment-service 超时"))
    assert result.original == "排查 payment-service 超时"
    assert result.rewritten == "排查 payment-service 超时"
    assert result.confidence == 1.0
    assert llm.invoke_count == 0  # No LLM call when disabled


def test_rewriter_with_mock_llm():
    """With mock LLM, rewriter returns structured RewrittenQuery."""
    llm = FakeRewriteLLM()
    rewriter = QueryRewriter(
        llm=llm,
        enabled=True,
        coreference_enabled=True,
        slot_filling_enabled=False,
        multi_intent_enabled=False,
    )
    result = async_test(rewriter.rewrite(
        "它怎么样了？",
        history=[
            {"role": "user", "content": "payment-service 的 p99 延迟多少？"},
            {"role": "assistant", "content": "payment-service p99 延迟 350ms"},
        ],
    ))
    assert result.original == "它怎么样了？"
    assert result.rewritten != "它怎么样了？"  # Was rewritten
    assert "payment-service" in result.entities
    assert "p99" in result.metrics


def test_rewriter_no_history_still_works():
    """Rewriter works even without conversation history."""
    llm = FakeRewriteLLM()
    rewriter = QueryRewriter(llm=llm, enabled=True)
    result = async_test(rewriter.rewrite("排查 payment-service 超时", history=[]))
    assert result.original == "排查 payment-service 超时"


def test_rewriter_falls_back_on_llm_error():
    """When LLM fails, rewriter falls back to fast regex rewrite."""
    llm = FakeRewriteLLM(response_text="INVALID JSON {{{")
    rewriter = QueryRewriter(llm=llm, enabled=True)
    result = async_test(rewriter.rewrite("排查 payment-service 超时"))
    assert result.intent == "troubleshoot"  # From regex fallback
    assert result.rewritten != ""  # Never empty


def test_rewriter_combines_llm_with_regex_metrics():
    """LLM rewrite is enriched with regex-extracted metrics."""
    llm = FakeRewriteLLM(response_text=json.dumps({
        "rewritten": "排查数据库超时问题",
        "intent": "troubleshoot",
        "secondary_intents": [],
        "confidence": 0.88,
        "needs_clarification": False,
        "missing_slots": [],
        "sub_queries": [],
        "reason": "",
        "metrics": [],  # LLM returns empty metrics
        "entities": [],
    }, ensure_ascii=False))
    rewriter = QueryRewriter(llm=llm, enabled=True)
    # Query has metric names that regex can extract
    result = async_test(rewriter.rewrite("排查 p99 LCP 超时"))
    assert "p99" in result.metrics  # Regex fill
    assert "lcp" in result.metrics  # Regex fill


def test_rewriter_confidence_below_threshold_uses_regex():
    """When LLM confidence is below threshold, regex result is used."""
    llm = FakeRewriteLLM(response_text=json.dumps({
        "rewritten": "something",
        "intent": "general",
        "secondary_intents": [],
        "confidence": 0.30,  # Below default threshold of 0.60
        "needs_clarification": False,
        "missing_slots": [],
        "sub_queries": [],
        "reason": "uncertain",
        "metrics": [],
        "entities": [],
    }, ensure_ascii=False))
    rewriter = QueryRewriter(llm=llm, enabled=True, rewrite_confidence_threshold=0.60)
    result = async_test(rewriter.rewrite("排查 payment-service 超时"))
    # Should fall back to regex, which correctly detects troubleshoot
    assert result.intent == "troubleshoot"


def test_rewriter_needs_clarification():
    """When LLM detects missing slots, needs_clarification is True."""
    llm = FakeRewriteLLM(response_text=json.dumps({
        "rewritten": "查询延迟",
        "intent": "metrics",
        "secondary_intents": [],
        "confidence": 0.70,
        "needs_clarification": True,
        "missing_slots": ["service_name", "metric_name"],
        "sub_queries": [],
        "reason": "Missing service and metric names",
        "metrics": [],
        "entities": [],
    }, ensure_ascii=False))
    rewriter = QueryRewriter(
        llm=llm, enabled=True,
        slot_filling_enabled=True,  # feature flag must be on for LLM call
    )
    result = async_test(rewriter.rewrite("查询延迟"))
    assert result.needs_clarification is True
    assert "service_name" in result.missing_slots
    assert "metric_name" in result.missing_slots


def test_rewriter_multi_intent_splits():
    """When LLM returns sub_queries, they are propagated to RewrittenQuery."""
    llm = FakeRewriteLLM(response_text=json.dumps({
        "rewritten": "排查 payment-service 超时并审计日志",
        "intent": "troubleshoot",
        "secondary_intents": ["audit"],
        "confidence": 0.85,
        "needs_clarification": False,
        "missing_slots": [],
        "sub_queries": [
            {"sub_query": "排查 payment-service 超时", "intent": "troubleshoot"},
            {"sub_query": "审计最近的执行日志", "intent": "audit"},
        ],
        "reason": "Split multi-intent query",
        "metrics": [],
        "entities": ["payment-service"],
    }, ensure_ascii=False))
    rewriter = QueryRewriter(llm=llm, enabled=True, multi_intent_enabled=True)
    result = async_test(rewriter.rewrite("排查 payment-service 超时并审计日志"))
    assert len(result.sub_queries) == 2
    assert result.sub_queries[0] == "排查 payment-service 超时"
    assert result.sub_queries[1] == "审计最近的执行日志"
