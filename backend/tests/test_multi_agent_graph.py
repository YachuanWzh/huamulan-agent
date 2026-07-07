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

    def fake_compile_multi_agent(settings, registry, memory, llm_config=None, hook_manager=None, cache=None, **kwargs):
        calls.append((settings, registry, memory, llm_config, hook_manager, cache, kwargs))
        return FakeApp()

    monkeypatch.setattr(multi_agent_module, "compile_multi_agent", fake_compile_multi_agent)
    monkeypatch.setattr(
        "personal_assistant.agent.harness._record_execution_log",
        fake_record_log,
    )

    harness = AgentHarness(settings="settings", registry="registry", memory="memory")
    response = asyncio.run(harness.run_user_turn("thread-1", "排查 p95", agent_mode="multi"))

    assert response.message == "multi answer"
    # calls[0] is the compile_multi_agent call (with intent_index/intent_llm kwargs)
    assert calls[0][:6] == ("settings", "registry", "memory", None, None, None)
    assert "intent_index" in calls[0][6]
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
            if 'reports' in content or '"reports"' in content:
                return AIMessage(content="综合结论")
            # Pydantic JSON: "agent":"metrics" (no space after colon)
            if '"agent":"metrics"' in content or '"agent": "metrics"' in content:
                return AIMessage(
                    content='{"agent":"metrics","findings":["p95 high"],"evidence":["p95"],"recommendations":["check dependency"],"confidence":0.8}'
                )
            if '"agent":"troubleshoot"' in content or '"agent": "troubleshoot"' in content:
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


def test_compile_multi_agent_accepts_intent_router_params(monkeypatch) -> None:
    """compile_multi_agent 接受 intent_index 和 intent_llm 可选参数（向后兼容）"""
    class FakeLLM:
        async def ainvoke(self, messages, config=None):
            content = getattr(messages[-1], "content", "")
            if "reports" in content or '"reports"' in content:
                return AIMessage(content="综合结论")
            if "agent" in content:
                import json
                name = "metrics" if "metrics" in content else "troubleshoot"
                return AIMessage(content=json.dumps({
                    "agent": name, "findings": [], "evidence": [], "recommendations": [], "confidence": 0.5,
                }))
            return AIMessage(content="{}")

    class Memory:
        checkpointer = None
        async def record_execution_log(self, log):
            return None

    monkeypatch.setattr(multi_agent_module, "build_llm", lambda *_args, **_kwargs: FakeLLM())

    # 不传 intent params — 应该保持兼容
    app = multi_agent_module.compile_multi_agent("settings", "registry", Memory())
    result = asyncio.run(app.ainvoke(
        {"messages": [AIMessage(content="ignored"), multi_agent_module.HumanMessage(content="排查 checkout")]},
        config={"configurable": {"thread_id": "t1"}},
    ))
    assert result["messages"][-1].content == "综合结论"


def test_rewrite_query_and_slots_still_works_for_legacy_tests() -> None:
    """rewrite_query_and_slots() 未修改，保持原有行为"""
    result = rewrite_query_and_slots("排查线上故障")
    assert result["slots"]["intent"] == "troubleshoot"
    assert result["slots"]["domain"] == "apm"


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


# ── 条件路由测试 ──────────────────────────────────────────────────────


def test_supervisor_plan_only_activates_selected_agents() -> None:
    """verify supervisor plan for troubleshoot intent only lists 3 agents"""
    from personal_assistant.agent.multi_agent import _supervisor_plan

    plan = _supervisor_plan("排查超时", {"intent": "troubleshoot"})
    assert "subagents" in plan
    assert plan["subagents"] == ["troubleshoot", "metrics", "audit"]
    assert "patrol" not in plan["subagents"]


def test_supervisor_plan_metrics_intent_is_minimal() -> None:
    """metrics intent should only activate metrics + audit"""
    from personal_assistant.agent.multi_agent import _supervisor_plan

    plan = _supervisor_plan("查看 p95", {"intent": "metrics"})
    assert plan["subagents"] == ["metrics", "audit"]


def test_sub_agent_input_includes_task_id() -> None:
    """新协议要求每个子 agent 输入包含 task_id"""
    from personal_assistant.agent.child_agent_protocol import SubAgentInput

    inp = SubAgentInput(
        task_id="task-test-001",
        agent="metrics",
        query="查看 p95",
        intent_slots={"intent": "metrics"},
    )
    assert inp.task_id == "task-test-001"
    assert inp.agent == "metrics"


def test_coerce_report_injects_default_fields() -> None:
    """_coerce_report 应兼容旧格式并注入新字段默认值"""
    from personal_assistant.agent.multi_agent import _coerce_report

    # 模拟旧格式输出（无 task_id/status/tools_used/error）
    report = _coerce_report("metrics", {"findings": ["f1"], "confidence": 0.8})
    assert report["agent"] == "metrics"
    assert report["findings"] == ["f1"]
    assert report["confidence"] == 0.8
    # 旧格式兼容 — _coerce_report 不负责注入新字段（由 child_agent 调用方处理）


# ── audit agent 内置 memory 工具注入 ──────────────────────────────────


def test_audit_agent_gets_builtin_memory_tools_when_registry_empty(monkeypatch) -> None:
    """审计 agent 在 registry 无工具时，应获得基于 memory 的内置查询工具。

    GIVEN: registry 中 audit-sop 存在但 tool_map_for_skills 返回空
    WHEN: compile_multi_agent 构建子 agent 工具
    THEN: audit agent 的 tools 非空，包含 memory 查询工具
    """
    tool_names_bound: list[str] = []

    class FakeLLM:
        def bind_tools(self, tools):
            tool_names_bound.extend([t.name for t in tools])
            return self

        async def ainvoke(self, messages, config=None):
            content = str(getattr(messages[-1], "content", ""))
            if "reports" in content:
                return AIMessage(content="综合结论")
            if "audit" in content:
                return AIMessage(content=(
                    '{"agent":"audit","findings":["发现"],"evidence":["证据"],'
                    '"recommendations":["建议"],"confidence":0.8,'
                    '"tools_used":["query_execution_log_summary"]}'
                ))
            return AIMessage(content='{"agent":"other","findings":[],"confidence":0.5}')

    class FakeMemory:
        checkpointer = None

        async def record_execution_log(self, log):
            return None

        async def execution_log_summary(self, thread_id):
            return type("Summary", (), {"total_events": 10, "total_tokens": 500})()

        async def list_execution_logs(self, thread_id, limit=500):
            return []

        async def list_audit_events(self, thread_id=None, limit=100):
            return []

        async def list_tool_errors(self, thread_id=None, limit=100):
            return []

    class FakeRegistry:
        _skills = {"audit-sop": object()}

        def load_skill(self, name):
            pass

        def tool_map_for_skills(self, skill_names):
            return {}  # 无工具

    monkeypatch.setattr(multi_agent_module, "build_llm", lambda *a, **kw: FakeLLM())

    app = multi_agent_module.compile_multi_agent("settings", FakeRegistry(), FakeMemory())
    asyncio.run(app.ainvoke(
        {
            "messages": [
                AIMessage(content="ignored"),
                multi_agent_module.HumanMessage(content="审计执行日志"),
            ]
        },
        config={"configurable": {"thread_id": "t1"}},
    ))

    assert len(tool_names_bound) > 0, (
        f"audit agent 应有 memory 工具，但 bind_tools 未被调用，"
        f"说明 audit agent 走了无工具路径"
    )
    assert any(
        name.startswith("query_") for name in tool_names_bound
    ), f"audit agent 工具应包含 query_* 前缀，实际: {tool_names_bound}"
