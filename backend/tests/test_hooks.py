import inspect

import pytest
from langchain_core.runnables import RunnableConfig

from personal_assistant.agent import agent as agent_module
from personal_assistant.agent.hook import AgentHookManager, HookEvent, HookStage, with_hooks
from personal_assistant.agent.router import (
    InMemorySkillVectorIndex,
    OllamaBgeM3Reranker,
    QdrantSkillVectorIndex,
)
from personal_assistant.checkpoint.redis_first import RedisFirstCheckpointSaver
from personal_assistant.config import Settings
from tests.test_redis_first_checkpoint import FakePostgresSaver, FakeRedis


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
        HookStage.COMPACT_CONTEXT,
        HookStage.AGENT,
        HookStage.MEMORY_REFLECTION,
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
    monkeypatch.setattr(agent_module, "build_skill_router", lambda registry, **_kwargs: (lambda state: state))
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
    assert set(captured_nodes) == {
        "route_skills",
        "compact_context",
        "agent",
        "memory_reflection",
        "approval",
        "tools",
    }
    assert all(getattr(node, "_hook_stage", None) is not None for node in captured_nodes.values())
    assert captured_nodes["route_skills"]._hook_stage == HookStage.ROUTE_SKILLS
    assert captured_nodes["agent"]._hook_stage == HookStage.AGENT
    assert captured_nodes["approval"]._hook_stage == HookStage.APPROVAL
    assert captured_nodes["tools"]._hook_stage == HookStage.TOOLS


def test_build_skill_vector_index_defaults_to_memory() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        _env_file=None,
    )

    index = agent_module.build_skill_vector_index(settings)

    assert isinstance(index, InMemorySkillVectorIndex)


def test_compile_agent_accepts_redis_first_checkpointer(monkeypatch) -> None:
    class FakeLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages, config=None):
            return "ai-response"

    class FakeRegistry:
        def tool_map_for_skills(self, selected_skills):
            return {}

    monkeypatch.setattr(agent_module, "build_llm", lambda settings, llm_config=None: FakeLLM())
    monkeypatch.setattr(agent_module, "build_basic_tools", lambda workspace, **_kwargs: [])
    monkeypatch.setattr(
        agent_module,
        "build_skill_router",
        lambda registry, **_kwargs: (lambda state: state),
    )

    checkpointer = RedisFirstCheckpointSaver(FakePostgresSaver(), FakeRedis(), ttl_seconds=60)

    compiled = agent_module.compile_agent(
        settings=Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        ),
        registry=FakeRegistry(),
        memory=type("Memory", (), {"checkpointer": checkpointer})(),
        decisions={},
        enable_memory_reflection=False,
    )

    assert compiled is not None


def test_build_skill_vector_index_uses_qdrant_when_configured() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        SKILL_ROUTING_VECTOR_STORE="qdrant",
        SKILL_ROUTING_QDRANT_URL="http://qdrant.example.test:6333",
        SKILL_ROUTING_QDRANT_COLLECTION="assistant_skill_routes",
        _env_file=None,
    )

    index = agent_module.build_skill_vector_index(settings)

    assert isinstance(index, QdrantSkillVectorIndex)
    assert index.url == "http://qdrant.example.test:6333"
    assert index.collection == "assistant_skill_routes"


def test_build_skill_reranker_returns_none_when_disabled() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        _env_file=None,
    )

    assert agent_module.build_skill_reranker(settings) is None


def test_build_skill_reranker_uses_ollama_when_enabled() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        SKILL_ROUTING_RERANK_ENABLED=True,
        SKILL_ROUTING_OLLAMA_BASE_URL="http://ollama.example.test:11434",
        SKILL_ROUTING_RERANK_MODEL="custom-reranker",
        _env_file=None,
    )

    reranker = agent_module.build_skill_reranker(settings)

    assert isinstance(reranker, OllamaBgeM3Reranker)
    assert reranker.base_url == "http://ollama.example.test:11434"
    assert reranker.model == "custom-reranker"


def test_compile_agent_passes_reranker_when_enabled(monkeypatch) -> None:
    captured_router_kwargs = {}

    class FakeLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages, config=None):
            return "ai-response"

    class FakeRegistry:
        def tool_map_for_skills(self, selected_skills):
            return {}

    class FakeReranker:
        pass

    def fake_build_skill_router(registry, **kwargs):
        captured_router_kwargs.update(kwargs)
        return lambda state: state

    monkeypatch.setattr(agent_module, "build_llm", lambda settings, llm_config=None: FakeLLM())
    monkeypatch.setattr(agent_module, "build_basic_tools", lambda workspace, **_kwargs: [])
    monkeypatch.setattr(agent_module, "build_skill_vector_index", lambda settings: object())
    monkeypatch.setattr(agent_module, "build_skill_reranker", lambda settings: FakeReranker())
    monkeypatch.setattr(agent_module, "build_skill_router", fake_build_skill_router)

    agent_module.compile_agent(
        settings=Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            SKILL_ROUTING_SEMANTIC_ENABLED=True,
            SKILL_ROUTING_RERANK_ENABLED=True,
            SKILL_ROUTING_RERANK_THRESHOLD=0.87,
            SKILL_ROUTING_RERANK_TOP_K=2,
            _env_file=None,
        ),
        registry=FakeRegistry(),
        memory=type("Memory", (), {"checkpointer": None})(),
        decisions={},
        enable_memory_reflection=False,
    )

    assert isinstance(captured_router_kwargs["reranker"], FakeReranker)
    assert captured_router_kwargs["rerank_threshold"] == 0.87
    assert captured_router_kwargs["rerank_top_k"] == 2


def test_build_skill_routing_llm_uses_dedicated_model(monkeypatch) -> None:
    captured_configs = []

    def fake_build_llm(settings, llm_config=None):
        captured_configs.append(llm_config)
        return object()

    monkeypatch.setattr(agent_module, "build_llm", fake_build_llm)
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="main-model",
        SKILL_ROUTING_LLM_MODEL="deepseek-v4-flash",
        _env_file=None,
    )

    agent_module.build_skill_routing_llm(settings, llm_config=None)

    assert captured_configs[0].model == "deepseek-v4-flash"


def test_build_skill_routing_llm_defaults_to_primary_model(monkeypatch) -> None:
    captured_configs = []

    def fake_build_llm(settings, llm_config=None):
        captured_configs.append(llm_config)
        return object()

    monkeypatch.setattr(agent_module, "build_llm", fake_build_llm)
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="main-model",
        _env_file=None,
    )

    agent_module.build_skill_routing_llm(settings, llm_config=None)

    assert captured_configs == [None]


@pytest.mark.asyncio
async def test_warmup_skill_routing_skips_when_semantic_disabled(monkeypatch) -> None:
    called = False

    def fake_build_skill_vector_index(settings):
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(agent_module, "build_skill_vector_index", fake_build_skill_vector_index)
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        _env_file=None,
    )

    await agent_module.warmup_skill_routing(settings, registry=object())

    assert called is False


@pytest.mark.asyncio
async def test_warmup_skill_routing_invokes_vector_index(monkeypatch) -> None:
    warmed_registries = []

    class FakeIndex:
        async def warmup(self, registry):
            warmed_registries.append(registry)

    monkeypatch.setattr(agent_module, "build_skill_vector_index", lambda settings: FakeIndex())
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        SKILL_ROUTING_SEMANTIC_ENABLED=True,
        _env_file=None,
    )
    registry = object()

    await agent_module.warmup_skill_routing(settings, registry)

    assert warmed_registries == [registry]


@pytest.mark.asyncio
async def test_warmup_skill_routing_swallows_index_errors(monkeypatch) -> None:
    class FailingIndex:
        async def warmup(self, registry):
            raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(agent_module, "build_skill_vector_index", lambda settings: FailingIndex())
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        SKILL_ROUTING_SEMANTIC_ENABLED=True,
        _env_file=None,
    )

    await agent_module.warmup_skill_routing(settings, registry=object())


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
    monkeypatch.setattr(agent_module, "build_basic_tools", lambda workspace, **_kwargs: [])
    monkeypatch.setattr(agent_module, "build_skill_router", lambda registry, **_kwargs: (lambda state: state))
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
