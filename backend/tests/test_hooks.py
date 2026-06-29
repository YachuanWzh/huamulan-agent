import inspect

import pytest
from langchain_core.runnables import RunnableConfig

from personal_assistant.agent import agent as agent_module
from personal_assistant.agent.hook import AgentHookManager, HookEvent, HookStage, with_hooks


@pytest.mark.asyncio
async def test_with_hooks_records_before_and_after_for_async_stage():
    events: list[HookEvent] = []
    manager = AgentHookManager([events.append])

    async def stage(state, config=None):
        return {"messages": ["ok"], "config": config}

    result = await with_hooks(manager, HookStage.AGENT, stage)({"messages": []}, {"thread_id": "t1"})

    assert result == {"messages": ["ok"], "config": {"thread_id": "t1"}}
    assert [(event.stage, event.phase) for event in events] == [
        (HookStage.AGENT, "before"),
        (HookStage.AGENT, "after"),
    ]
    assert events[0].state == {"messages": []}
    assert events[1].result == result


def test_with_hooks_exposes_runnable_config_annotation():
    async def stage(state, config=None):
        return state

    wrapped = with_hooks(AgentHookManager(), HookStage.AGENT, stage)

    config_parameter = inspect.signature(wrapped).parameters["config"]
    assert config_parameter.annotation == RunnableConfig | None


@pytest.mark.asyncio
async def test_hook_errors_are_isolated_from_stage_execution(caplog):
    def broken_hook(event):
        raise RuntimeError(f"boom {event.stage.value} {event.phase}")

    manager = AgentHookManager([broken_hook])

    async def stage(state):
        return {"messages": ["still-runs"]}

    result = await with_hooks(manager, HookStage.TOOLS, stage)({"messages": []})

    assert result == {"messages": ["still-runs"]}
    assert "Agent hook failed" in caplog.text


def test_default_hook_manager_has_all_necessary_stages():
    manager = AgentHookManager()

    assert set(manager.stages) == {
        HookStage.ROUTE_SKILLS,
        HookStage.AGENT,
        HookStage.APPROVAL,
        HookStage.TOOLS,
    }


def test_compile_agent_wraps_necessary_graph_nodes(monkeypatch):
    captured_nodes = {}

    class FakeGraph:
        def __init__(self, state_type):
            self.state_type = state_type

        def add_node(self, name, node):
            captured_nodes[name] = node

        def set_conditional_entry_point(self, *args, **kwargs):
            pass

        def add_edge(self, *args, **kwargs):
            pass

        def add_conditional_edges(self, *args, **kwargs):
            pass

        def compile(self, checkpointer=None):
            return "compiled"

    monkeypatch.setattr(agent_module, "StateGraph", FakeGraph)
    monkeypatch.setattr(agent_module, "build_llm", lambda settings, llm_config=None: object())
    monkeypatch.setattr(agent_module, "build_skill_router", lambda registry: (lambda state: state))
    monkeypatch.setattr(agent_module, "ToolNode", lambda tools: object())

    manager = AgentHookManager()

    result = agent_module.compile_agent(
        settings=object(),
        registry=object(),
        memory=type("Memory", (), {"checkpointer": object()})(),
        decisions={},
        hook_manager=manager,
    )

    assert result == "compiled"
    assert set(captured_nodes) == {"route_skills", "agent", "approval", "tools"}
    assert all(getattr(node, "_hook_stage", None) is not None for node in captured_nodes.values())
    assert captured_nodes["route_skills"]._hook_stage == HookStage.ROUTE_SKILLS
    assert captured_nodes["agent"]._hook_stage == HookStage.AGENT
    assert captured_nodes["approval"]._hook_stage == HookStage.APPROVAL
    assert captured_nodes["tools"]._hook_stage == HookStage.TOOLS


@pytest.mark.asyncio
async def test_agent_node_propagates_runnable_config_to_llm(monkeypatch):
    captured_nodes = {}
    llm_configs = []

    class FakeGraph:
        def __init__(self, state_type):
            self.state_type = state_type

        def add_node(self, name, node):
            captured_nodes[name] = node

        def set_conditional_entry_point(self, *args, **kwargs):
            pass

        def add_edge(self, *args, **kwargs):
            pass

        def add_conditional_edges(self, *args, **kwargs):
            pass

        def compile(self, checkpointer=None):
            return "compiled"

    class FakeLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages, config=None):
            llm_configs.append(config)
            return "ai-response"

    class FakeRegistry:
        def tool_map_for_skills(self, selected_skills):
            return {}

    monkeypatch.setattr(agent_module, "StateGraph", FakeGraph)
    monkeypatch.setattr(agent_module, "build_llm", lambda settings, llm_config=None: FakeLLM())
    monkeypatch.setattr(agent_module, "build_basic_tools", lambda workspace: [])
    monkeypatch.setattr(agent_module, "build_skill_router", lambda registry: (lambda state: state))
    monkeypatch.setattr(agent_module, "ToolNode", lambda tools: object())

    agent_module.compile_agent(
        settings=type("Settings", (), {"assistant_workspace_dir": "."})(),
        registry=FakeRegistry(),
        memory=type("Memory", (), {"checkpointer": object()})(),
        decisions={},
    )

    config = {"callbacks": [object()], "metadata": {"langfuse_session_id": "thread-1"}}
    result = await captured_nodes["agent"]({"messages": [], "selected_skills": []}, config)

    assert result == {"messages": ["ai-response"]}
    assert llm_configs == [config]
