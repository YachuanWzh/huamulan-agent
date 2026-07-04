import asyncio

from langchain_core.messages import AIMessage

from personal_assistant.agent import agent as agent_module
from personal_assistant.agent import multi_agent as multi_agent_module
from personal_assistant.agent.harness import AgentHarness
from personal_assistant.agent.multi_agent import _coerce_report, rewrite_query_and_slots


def test_rewrite_query_extracts_apm_intent_slots() -> None:
    result = rewrite_query_and_slots("请排查 checkout API p95 超时并给出 RCA")

    assert result["rewritten_query"] == "请排查 checkout API p95 超时并给出 RCA"
    assert result["slots"]["domain"] == "apm"
    assert result["slots"]["intent"] == "troubleshoot"
    assert "p95" in result["slots"]["metrics"]
    assert "checkout" in result["slots"]["entities"]


def test_harness_dispatches_multi_agent_mode_to_multi_graph(monkeypatch) -> None:
    calls = []

    class FakeApp:
        async def ainvoke(self, state, config=None):
            calls.append((state, config))
            return {"messages": [AIMessage(content="multi answer")]}

    async def fake_record_log(*_args, **_kwargs):
        return None

    def fake_compile_multi_agent(settings, registry, memory, llm_config=None, hook_manager=None, cache=None):
        calls.append((settings, registry, memory, llm_config, hook_manager, cache))
        return FakeApp()

    monkeypatch.setattr(multi_agent_module, "compile_multi_agent", fake_compile_multi_agent)
    monkeypatch.setattr(
        "personal_assistant.agent.harness._record_execution_log",
        fake_record_log,
    )

    harness = AgentHarness(settings="settings", registry="registry", memory="memory")
    response = asyncio.run(harness.run_user_turn("thread-1", "排查 p95", agent_mode="multi"))

    assert response.message == "multi answer"
    assert calls[0] == ("settings", "registry", "memory", None, None, None)
    assert calls[1][0]["messages"][0].content == "排查 p95"


def test_harness_keeps_single_agent_mode_on_existing_compile_path(monkeypatch) -> None:
    calls = []

    class FakeApp:
        async def ainvoke(self, state, config=None):
            calls.append((state, config))
            return {"messages": [AIMessage(content="single answer")]}

    async def fake_record_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_module, "compile_agent", lambda *_args, **_kwargs: FakeApp())
    monkeypatch.setattr(
        "personal_assistant.agent.harness._record_execution_log",
        fake_record_log,
    )
    harness = AgentHarness(settings="settings", registry="registry", memory="memory")

    response = asyncio.run(harness.run_user_turn("thread-1", "hello"))

    assert response.message == "single answer"
    assert calls[0][0]["messages"][0].content == "hello"


def test_compiled_multi_agent_graph_runs_child_agents(monkeypatch) -> None:
    class FakeLLM:
        async def ainvoke(self, messages, config=None):
            content = getattr(messages[-1], "content", "")
            if '"reports"' in content:
                return AIMessage(content="综合结论")
            if '"agent": "metrics"' in content:
                return AIMessage(
                    content='{"agent":"metrics","findings":["p95 high"],"evidence":["p95"],"recommendations":["check dependency"],"confidence":0.8}'
                )
            if '"agent": "troubleshoot"' in content:
                return AIMessage(
                    content='{"agent":"troubleshoot","findings":["timeout"],"evidence":["checkout"],"recommendations":["inspect upstream"],"confidence":0.7}'
                )
            return AIMessage(content='{"agent":"child","findings":[],"evidence":[],"recommendations":[]}')

    class Memory:
        checkpointer = None

        async def record_execution_log(self, log):
            return None

    monkeypatch.setattr(multi_agent_module, "build_llm", lambda *_args, **_kwargs: FakeLLM())

    app = multi_agent_module.compile_multi_agent("settings", "registry", Memory())
    result = asyncio.run(
        app.ainvoke(
            {"messages": [AIMessage(content="ignored"), multi_agent_module.HumanMessage(content="排查 checkout p95")]},
            config={"configurable": {"thread_id": "thread-1"}},
        )
    )

    assert result["messages"][-1].content == "综合结论"
    assert {report["agent"] for report in result["apm_reports"]} >= {"metrics", "troubleshoot"}
    assert result["user_vector_context"]["status"] == "skipped"


# ── _coerce_report confidence handling ──────────────────────────────


def test_coerce_report_handles_qualitative_confidence_string() -> None:
    """LLM may return 'high'/'medium'/'low' for confidence — must not crash."""
    report = _coerce_report("test_agent", {"confidence": "high", "findings": ["f1"]})
    assert isinstance(report["confidence"], float)
    assert report["confidence"] >= 0.8  # maps to a high confidence number


def test_coerce_report_handles_numeric_confidence() -> None:
    report = _coerce_report("test_agent", {"confidence": 0.9, "findings": ["f1"]})
    assert report["confidence"] == 0.9


def test_coerce_report_defaults_missing_confidence_to_point_five() -> None:
    report = _coerce_report("test_agent", {"findings": ["f1"]})
    assert report["confidence"] == 0.5


def test_coerce_report_handles_string_numeric_confidence() -> None:
    report = _coerce_report("test_agent", {"confidence": "0.75", "findings": ["f1"]})
    assert report["confidence"] == 0.75


def test_coerce_report_handles_text_input_with_qualitative_confidence() -> None:
    """When LLM returns raw text JSON with qualitative confidence."""
    report = _coerce_report("test_agent", '{"confidence":"medium","findings":["f1"]}')
    assert isinstance(report["confidence"], float)
    assert 0.3 <= report["confidence"] <= 0.7
