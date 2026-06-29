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
