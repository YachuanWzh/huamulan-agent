from personal_assistant.agent import agent as agent_module
from personal_assistant.agent.harness import AgentHarness
from personal_assistant.agent.hook import AgentHookManager


def test_harness_delegates_compilation_to_agent_module(monkeypatch):
    calls = []
    sentinel = object()

    def fake_compile_agent(settings, registry, memory, decisions, llm_config=None):
        calls.append((settings, registry, memory, decisions, llm_config))
        return sentinel

    monkeypatch.setattr(agent_module, "compile_agent", fake_compile_agent)
    harness = AgentHarness(settings="settings", registry="registry", memory="memory")

    assert harness._compile("llm-config") is sentinel
    assert calls == [
        ("settings", "registry", "memory", harness.decisions, "llm-config")
    ]


def test_harness_passes_hook_manager_to_compiled_agent(monkeypatch):
    calls = []
    hook_manager = AgentHookManager()
    sentinel = object()

    def fake_compile_agent(
        settings,
        registry,
        memory,
        decisions,
        llm_config=None,
        hook_manager=None,
    ):
        calls.append((settings, registry, memory, decisions, llm_config, hook_manager))
        return sentinel

    monkeypatch.setattr(agent_module, "compile_agent", fake_compile_agent)
    harness = AgentHarness(
        settings="settings",
        registry="registry",
        memory="memory",
        hook_manager=hook_manager,
    )

    assert harness._compile("llm-config") is sentinel
    assert calls == [
        (
            "settings",
            "registry",
            "memory",
            harness.decisions,
            "llm-config",
            hook_manager,
        )
    ]


def test_compile_cached_reuses_single_agent_graph(monkeypatch):
    calls = []
    sentinel = object()

    def fake_compile_agent(
        settings,
        registry,
        memory,
        decisions,
        llm_config=None,
        enable_memory_reflection=True,
        **kwargs,
    ):
        calls.append(
            (
                settings,
                registry,
                memory,
                decisions,
                llm_config,
                enable_memory_reflection,
                kwargs,
            )
        )
        return sentinel

    monkeypatch.setattr(agent_module, "compile_agent", fake_compile_agent)
    harness = AgentHarness(settings="settings", registry="registry", memory="memory")

    assert harness._compile_cached("llm-config", enable_memory_reflection=False) is sentinel
    assert harness._compile_cached("llm-config", enable_memory_reflection=False) is sentinel
    assert calls == [
        (
            "settings",
            "registry",
            "memory",
            harness.decisions,
            "llm-config",
            False,
            {},
        )
    ]


def test_resume_after_approval_injects_approval_turn_count(monkeypatch):
    class FakeApp:
        async def ainvoke(self, state, config=None):
            calls.append((state, config))
            return {"messages": []}

    calls = []

    async def fake_record_decision(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "personal_assistant.agent.harness._record_tool_approval_decision",
        fake_record_decision,
    )

    harness = AgentHarness(settings="settings", registry="registry", memory="memory")
    monkeypatch.setattr(harness, "_compile", lambda _llm_config=None: FakeApp())

    import asyncio

    asyncio.run(harness.resume_after_approval("thread-1", "approval-1", True))

    assert calls[0][0]["approval_turn_count"] == 1
