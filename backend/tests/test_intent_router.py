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
