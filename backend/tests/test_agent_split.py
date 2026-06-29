from personal_assistant.agent import agent as agent_module
from personal_assistant.agent.harness import AgentHarness


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
