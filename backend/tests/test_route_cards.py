import json

import pytest
from langchain_core.messages import AIMessage

from personal_assistant.agent.harness import AgentHarness


class FakeChunk:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.additional_kwargs: dict = {}
        self.response_metadata: dict = {}


class FakeState:
    values = {"messages": [AIMessage(content="好的，让我来检查一下")], "pending_approvals": []}


ROUTE_OUTPUT = {
    "rewritten_query": "查询系统的运行状况和指标",
    "intent_slots": {
        "original_query": "我想看看这个系统的运行状况",
        "intent": "general",
        "secondary_intents": [],
        "confidence": 0.3,
        "needs_clarification": True,
        "missing_slots": ["service_name"],
        "sub_queries": [],
        "source": "rewriter",
    },
    "selected_skills": ["otel-query"],
    "routing_trace": [
        {
            "stage": "llm_judge",
            "status": "selected",
            "selected_skills": ["otel-query"],
            "confidence": 0.9,
            "reason": "用户想看系统运行状况和指标，otel-query 合适",
        }
    ],
}


class RouteThenAnswerApp:
    async def astream_events(self, *_args, **_kwargs):
        # route_skills 内部 LLM 产出的 JSON —— 绝不能变成可见 token
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk(content='{"rewritten": "查询系统的运行状况和指标"}')},
            "metadata": {"langgraph_node": "route_skills"},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk(content='{"selectedSkill": "otel-query"}')},
            "metadata": {"langgraph_node": "route_skills"},
        }
        # route_skills 节点结束，带出解析后的 state
        yield {
            "event": "on_chain_end",
            "name": "route_skills",
            "tags": ["langgraph_node"],
            "data": {"output": ROUTE_OUTPUT},
        }
        # 真正的回答来自 agent 节点 —— 必须是可见 token
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": FakeChunk(content="好的，")},
            "metadata": {"langgraph_node": "agent"},
        }

    async def aget_state(self, *_args, **_kwargs):
        return FakeState()


class RouteHarness(AgentHarness):
    def __init__(self) -> None:
        self.callbacks = []

    def _compile(self, _llm_config=None):
        return RouteThenAnswerApp()


def _cards(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        if chunk.startswith("event: card\n"):
            data = chunk.split("data: ", 1)[1].strip()
            out.append(json.loads(data))
    return out


def _tokens(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        if chunk.startswith("event: token\n"):
            data = chunk.split("data: ", 1)[1].strip()
            out.append(json.loads(data))
    return out


@pytest.mark.asyncio
async def test_route_skills_json_is_not_emitted_as_visible_tokens() -> None:
    chunks = [c async for c in RouteHarness().run_user_turn_stream("t1", "我想看看这个系统的运行状况")]

    tokens = _tokens(chunks)
    # route_skills 节点的 JSON 不得进入 token 流
    assert all(tok["node"] != "route_skills" for tok in tokens)
    assert all("rewritten" not in tok["content"] for tok in tokens)
    assert all("selectedSkill" not in tok["content"] for tok in tokens)
    # agent 节点的正文仍然照常流式
    assert any(tok["node"] == "agent" and tok["content"] == "好的，" for tok in tokens)


@pytest.mark.asyncio
async def test_route_skills_emits_query_rewrite_and_skill_route_cards() -> None:
    chunks = [c async for c in RouteHarness().run_user_turn_stream("t1", "我想看看这个系统的运行状况")]

    cards = _cards(chunks)
    types = [c["card_type"] for c in cards]
    assert "query_rewrite" in types
    assert "skill_route" in types

    rewrite = next(c for c in cards if c["card_type"] == "query_rewrite")
    assert rewrite["rewritten_query"] == "查询系统的运行状况和指标"
    assert rewrite["intent"] == "general"
    assert rewrite["needs_clarification"] is True
    assert rewrite["missing_slots"] == ["service_name"]

    route = next(c for c in cards if c["card_type"] == "skill_route")
    assert route["selected_skills"] == ["otel-query"]
    assert route["confidence"] == 0.9
    assert "otel-query" in route["reason"]


@pytest.mark.asyncio
async def test_skill_route_card_only_when_no_rewrite() -> None:
    class NoRewriteApp(RouteThenAnswerApp):
        async def astream_events(self, *_args, **_kwargs):
            yield {
                "event": "on_chain_end",
                "name": "route_skills",
                "tags": ["langgraph_node"],
                "data": {"output": {"selected_skills": [], "routing_trace": [], "intent_slots": {}}},
            }

    class NoRewriteHarness(RouteHarness):
        def _compile(self, _llm_config=None):
            return NoRewriteApp()

    chunks = [c async for c in NoRewriteHarness().run_user_turn_stream("t1", "hi")]
    cards = _cards(chunks)
    assert [c["card_type"] for c in cards] == ["skill_route"]
