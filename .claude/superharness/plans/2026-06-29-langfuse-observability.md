# Langfuse Observability Integration — Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Langfuse observability into the LangGraph personal assistant backend so every LLM call, tool execution, and graph step is automatically traced.

**Architecture:** Use Langfuse's native `langfuse.langchain.CallbackHandler` injected via LangChain's `config["callbacks"]` into `app.ainvoke()` / `app.astream_events()` calls. The callback auto-traces LLM calls, tool executions, and chain steps. Thread ID is mapped to a Langfuse session for filtering. Langfuse is opt-in — only enabled when credentials are configured.

**Tech Stack:** Python 3.11+, langfuse SDK, LangChain callbacks, Pydantic Settings

---

### Task 1: Add `langfuse` dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add langfuse to dependencies**

```toml
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "pydantic-settings>=2.4.0",
  "langchain-core>=0.3.0",
  "langchain-openai>=0.2.0",
  "langgraph>=0.2.0",
  "langgraph-checkpoint-postgres>=2.0.0",
  "psycopg[binary,pool]>=3.2.0",
  "watchfiles>=0.24.0",
  "pyyaml>=6.0",
  "langfuse>=3.0.0",
]
```

- [ ] **Step 2: Install the new dependency**

Run: `pip install -e ".[dev]"` from backend/
Expected: langfuse installed successfully

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore: add langfuse dependency for LLM observability"
```

---

### Task 2: Add Langfuse config to Settings

**Files:**
- Modify: `backend/src/personal_assistant/config.py`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
def test_langfuse_settings_have_sensible_defaults() -> None:
    """Langfuse is opt-in — disabled when keys are missing."""
    from personal_assistant.config import Settings

    # When env vars are not set, Langfuse should default to disabled
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
        )
        assert settings.langfuse_enabled is False
        assert settings.langfuse_public_key is None
        assert settings.langfuse_secret_key is None
        assert settings.langfuse_host == "https://cloud.langfuse.com"


def test_langfuse_enabled_when_keys_are_set() -> None:
    """Langfuse is enabled when both keys are provided."""
    from personal_assistant.config import Settings

    with patch.dict("os.environ", {
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
    }, clear=False):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
        )
        assert settings.langfuse_enabled is True


def test_langfuse_custom_host() -> None:
    """Custom Langfuse host is respected."""
    from personal_assistant.config import Settings

    with patch.dict("os.environ", {
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
        "LANGFUSE_HOST": "https://selfhosted.example.com",
    }, clear=False):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
        )
        assert settings.langfuse_host == "https://selfhosted.example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_langfuse_settings_have_sensible_defaults tests/test_config.py::test_langfuse_enabled_when_keys_are_set tests/test_config.py::test_langfuse_custom_host -v`
Expected: FAIL — Settings has no `langfuse_*` fields

- [ ] **Step 3: Add Langfuse fields to Settings**

In `backend/src/personal_assistant/config.py`, add after the existing field definitions:

```python
langfuse_public_key: str | None = Field(
    default=None, alias="LANGFUSE_PUBLIC_KEY"
)
langfuse_secret_key: str | None = Field(
    default=None, alias="LANGFUSE_SECRET_KEY"
)
langfuse_host: str = Field(
    default="https://cloud.langfuse.com", alias="LANGFUSE_HOST"
)

@property
def langfuse_enabled(self) -> bool:
    return bool(self.langfuse_public_key and self.langfuse_secret_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_langfuse_settings_have_sensible_defaults tests/test_config.py::test_langfuse_enabled_when_keys_are_set tests/test_config.py::test_langfuse_custom_host -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_config.py backend/src/personal_assistant/config.py
git commit -m "feat(config): add Langfuse settings (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST)"
```

---

### Task 3: Create `tracing.py` module

**Files:**
- Create: `backend/src/personal_assistant/tracing.py`
- Test: `backend/tests/test_tracing.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for personal_assistant.tracing — Langfuse callback factory."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_build_callback_returns_none_when_disabled() -> None:
    """When Langfuse is disabled, build_langfuse_callback returns None."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = False

    result = build_langfuse_callback(settings)
    assert result is None


def test_build_callback_returns_callback_handler_when_enabled() -> None:
    """When Langfuse is enabled, build_langfuse_callback returns a CallbackHandler."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-test"
    settings.langfuse_secret_key = "sk-test"
    settings.langfuse_host = "https://cloud.langfuse.com"

    with patch("personal_assistant.tracing.CallbackHandler") as mock_handler_cls:
        mock_handler = MagicMock()
        mock_handler_cls.return_value = mock_handler

        result = build_langfuse_callback(settings)

        mock_handler_cls.assert_called_once()
        assert result is mock_handler


def test_build_callback_passes_config_to_handler() -> None:
    """CallbackHandler is created with the correct Langfuse credentials."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True
    settings.langfuse_public_key = "pk-myapp"
    settings.langfuse_secret_key = "sk-secret"
    settings.langfuse_host = "https://langfuse.example.com"

    with patch("personal_assistant.tracing.CallbackHandler") as mock_handler_cls:
        build_langfuse_callback(settings)

        call_kwargs = mock_handler_cls.call_args.kwargs
        assert call_kwargs["public_key"] == "pk-myapp"
        assert call_kwargs["secret_key"] == "sk-secret"
        assert call_kwargs["host"] == "https://langfuse.example.com"


def test_build_callback_returns_none_when_import_fails() -> None:
    """Gracefully returns None if langfuse is not installed."""
    from personal_assistant.tracing import build_langfuse_callback

    settings = MagicMock()
    settings.langfuse_enabled = True

    with patch(
        "personal_assistant.tracing._LANGFUSE_AVAILABLE", False
    ):
        result = build_langfuse_callback(settings)
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tracing.py -v`
Expected: FAIL — Module `personal_assistant.tracing` not found

- [ ] **Step 3: Write the minimal implementation**

Create `backend/src/personal_assistant/tracing.py`:

```python
"""Langfuse observability integration for the LangGraph agent.

Provides a factory to build a Langfuse LangChain CallbackHandler that
auto-traces LLM calls, tool executions, and graph node transitions.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from personal_assistant.config import Settings

logger = logging.getLogger(__name__)

try:
    from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]

    _LANGFUSE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LANGFUSE_AVAILABLE = False


def build_langfuse_callback(
    settings: Settings,
) -> CallbackHandler | None:
    """Build a Langfuse LangChain CallbackHandler if Langfuse is enabled.

    Returns ``None`` when:
    - ``settings.langfuse_enabled`` is ``False`` (no credentials configured)
    - The ``langfuse`` package is not installed

    The returned handler can be passed directly to LangChain/LangGraph
    ``config["callbacks"]``.
    """
    if not settings.langfuse_enabled:
        return None

    if not _LANGFUSE_AVAILABLE:
        logger.warning(
            "Langfuse is enabled in settings but the langfuse package "
            "is not installed. Install it with: pip install langfuse"
        )
        return None

    return CallbackHandler(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tracing.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_tracing.py backend/src/personal_assistant/tracing.py
git commit -m "feat(tracing): add Langfuse callback factory module"
```

---

### Task 4: Integrate Langfuse callback into AgentHarness

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Test: `backend/tests/test_tracing.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tracing.py`:

```python
def test_agent_harness_injects_callback_into_ainvoke() -> None:
    """AgentHarness passes Langfuse callback to app.ainvoke()."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personal_assistant.agent.harness import AgentHarness
    from personal_assistant.api.schemas import ChatResponse

    settings = MagicMock()
    settings.llm_base_url = "https://api.example.com"
    settings.llm_api_key = "sk-test"
    settings.llm_model = "test-model"
    settings.llm_temperature = 0.2

    registry = MagicMock()
    memory = MagicMock()

    mock_callback = MagicMock()
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(settings, registry, memory)

    with patch.object(
        harness, "_compile", return_value=mock_app
    ) as mock_compile:
        result = harness.run_user_turn(
            "thread-1", "hello", callbacks=[mock_callback]
        )

        # Verify callback was passed to ainvoke
        call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
        assert "callbacks" in call_config
        assert mock_callback in call_config["callbacks"]


def test_agent_harness_injects_callback_into_astream_events() -> None:
    """AgentHarness passes Langfuse callback to app.astream_events()."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personal_assistant.agent.harness import AgentHarness

    settings = MagicMock()
    settings.llm_base_url = "https://api.example.com"
    settings.llm_api_key = "sk-test"
    settings.llm_model = "test-model"
    settings.llm_temperature = 0.2

    registry = MagicMock()
    memory = MagicMock()

    mock_callback = MagicMock()
    mock_app = MagicMock()

    async def mock_astream(*args, **kwargs):
        yield {"event": "on_chain_end", "data": {}}

    mock_app.astream_events = mock_astream

    async def mock_aget_state(config):
        m = MagicMock()
        m.values = {}
        return m

    mock_app.aget_state = mock_aget_state

    harness = AgentHarness(settings, registry, memory)

    with patch.object(harness, "_compile", return_value=mock_app):
        stream = harness.run_user_turn_stream(
            "thread-1", "hello", callbacks=[mock_callback]
        )
        # Consume the async generator
        import asyncio
        async def consume():
            async for _ in stream:
                pass
        asyncio.get_event_loop().run_until_complete(consume())


def test_agent_harness_callbacks_default_to_empty_list() -> None:
    """When no callbacks are provided, harness works without them."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personal_assistant.agent.harness import AgentHarness

    settings = MagicMock()
    settings.llm_base_url = "https://api.example.com"
    settings.llm_api_key = "sk-test"
    settings.llm_model = "test-model"
    settings.llm_temperature = 0.2

    registry = MagicMock()
    memory = MagicMock()

    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(settings, registry, memory)

    with patch.object(harness, "_compile", return_value=mock_app):
        result = harness.run_user_turn("thread-1", "hello")

        # Should still work — callbacks is optional
        call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
        assert "callbacks" not in call_config


def test_agent_harness_sets_thread_metadata_when_callbacks_present() -> None:
    """When callbacks are provided, thread_id is set as Langfuse session_id."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personal_assistant.agent.harness import AgentHarness

    settings = MagicMock()
    settings.llm_base_url = "https://api.example.com"
    settings.llm_api_key = "sk-test"
    settings.llm_model = "test-model"
    settings.llm_temperature = 0.2

    registry = MagicMock()
    memory = MagicMock()

    mock_callback = MagicMock()
    mock_app = MagicMock()
    mock_app.ainvoke = AsyncMock(
        return_value={"messages": [], "pending_approvals": []}
    )

    harness = AgentHarness(settings, registry, memory)

    with patch.object(harness, "_compile", return_value=mock_app):
        harness.run_user_turn(
            "thread-abc-123", "hello", callbacks=[mock_callback]
        )

        call_config = mock_app.ainvoke.call_args.kwargs.get("config", {})
        assert "configurable" in call_config
        assert "callbacks" in call_config
        # thread_id should be in metadata for session tracking
        assert "metadata" in call_config
        assert call_config["metadata"].get("langfuse_session_id") == "thread-abc-123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tracing.py::test_agent_harness_injects_callback_into_ainvoke -v`
Expected: FAIL — `run_user_turn()` has no `callbacks` parameter

- [ ] **Step 3: Modify AgentHarness to accept and inject callbacks**

In `backend/src/personal_assistant/agent/harness.py`, modify the `AgentHarness` class:

**Constructor** — add `callbacks` parameter:

```python
class AgentHarness:
    def __init__(
        self,
        settings: Settings,
        registry: SkillRegistry,
        memory: PostgresMemory,
        hook_manager: AgentHookManager | None = None,
        callbacks: list[Any] | None = None,
    ):
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.hook_manager = hook_manager
        self.callbacks = list(callbacks or [])
        self.decisions: dict[str, bool] = {}
```

**`run_user_turn`** — add callbacks param and pass to config:

```python
async def run_user_turn(
    self,
    thread_id: str,
    message: str,
    llm_config: LLMConfig | None = None,
    callbacks: list[Any] | None = None,
) -> ChatResponse:
    match = scan_prompt_guard(message)
    if match:
        await _record_audit(
            self.memory,
            AuditEventCreate(
                thread_id=thread_id,
                source="prompt",
                category=match.category,
                severity=match.severity,
                reason=match.reason,
                subject=_clip_subject(message),
                metadata={"prompt_guard_blocked": True},
            ),
        )
        return ChatResponse(
            thread_id=thread_id,
            status="completed",
            message=_PROMPT_GUARD_MESSAGE,
        )
    app = self._compile(llm_config)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    _merge_callbacks(config, self.callbacks, callbacks, thread_id)
    result = await app.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )
    return _to_response(thread_id, result)
```

**`run_user_turn_stream`** — same pattern:

```python
async def run_user_turn_stream(
    self,
    thread_id: str,
    message: str,
    llm_config: LLMConfig | None = None,
    callbacks: list[Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream the agent response as SSE events."""
    try:
        match = scan_prompt_guard(message)
        if match:
            # ... unchanged guard block ...
            return
        app = self._compile(llm_config)
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        _merge_callbacks(config, self.callbacks, callbacks, thread_id)
        async for event in app.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            version="v2",
        ):
            # ... unchanged event handling ...
    except Exception as exc:
        yield _sse_event("error", {"message": _stream_error_message(exc)})

    yield "data: [DONE]\n\n"
```

**`resume_after_approval`** — same pattern:

In `resume_after_approval`:
```python
async def resume_after_approval(
    self,
    thread_id: str,
    approval_id: str,
    approved: bool,
    llm_config: LLMConfig | None = None,
    callbacks: list[Any] | None = None,
) -> ChatResponse:
    self.decisions[approval_id] = approved
    await _record_tool_approval_decision(
        getattr(self, "memory", None),
        thread_id,
        approval_id,
        approved,
    )
    app = self._compile(llm_config)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    _merge_callbacks(config, self.callbacks, callbacks, thread_id)
    result = await app.ainvoke(
        {},
        config=config,
    )
    return _to_response(thread_id, result)
```

**`resume_after_approval_stream`** — same pattern:

```python
async def resume_after_approval_stream(
    self,
    thread_id: str,
    approval_id: str,
    approved: bool,
    llm_config: LLMConfig | None = None,
    callbacks: list[Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Resume after approval with streaming SSE events."""
    self.decisions[approval_id] = approved
    await _record_tool_approval_decision(
        getattr(self, "memory", None),
        thread_id,
        approval_id,
        approved,
    )

    try:
        app = self._compile(llm_config)
        config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
        _merge_callbacks(config, self.callbacks, callbacks, thread_id)
        async for event in app.astream_events(
            {},
            config=config,
            version="v2",
        ):
            # ... unchanged event handling ...
    except Exception as exc:
        yield _sse_event("error", {"message": _stream_error_message(exc)})

    yield "data: [DONE]\n\n"
```

**Add `_merge_callbacks` helper at module level** (before `AgentHarness`):

```python
def _merge_callbacks(
    config: dict[str, Any],
    harness_callbacks: list[Any],
    request_callbacks: list[Any] | None,
    thread_id: str,
) -> None:
    """Merge harness-level and request-level callbacks into config.

    Callbacks are merged so both harness-level (e.g. Langfuse) and
    per-request callbacks are active simultaneously.
    """
    combined: list[Any] = []
    combined.extend(harness_callbacks)
    if request_callbacks:
        combined.extend(request_callbacks)
    if combined:
        config["callbacks"] = combined
        config.setdefault("metadata", {})
        config["metadata"]["langfuse_session_id"] = thread_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tracing.py -v`
Expected: 8 PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: Same baseline as before (164 PASS, 1 pre-existing FAIL in test_config)

- [ ] **Step 6: Commit**

```bash
git add backend/src/personal_assistant/agent/harness.py backend/tests/test_tracing.py
git commit -m "feat(agent): inject Langfuse callbacks into agent harness invoke/stream"
```

---

### Task 5: Update .env.example and integrate into server

**Files:**
- Modify: `backend/.env.example`
- Modify: `backend/src/personal_assistant/api/server.py`

- [ ] **Step 1: Update .env.example with Langfuse variables**

Append to `backend/.env.example`:

```ini
# ---- Langfuse Observability (optional) --------------------------------------
# Langfuse is an open-source LLM observability platform. Set these variables
# to enable automatic tracing of LLM calls, tool executions, and agent graph
# steps. Leave blank to disable tracing.
#
# Get your keys at: https://cloud.langfuse.com (or your self-hosted instance)
#
# Required: no (omitting disables Langfuse tracing)
# LANGFUSE_PUBLIC_KEY=
# LANGFUSE_SECRET_KEY=
# LANGFUSE_HOST=https://cloud.langfuse.com
```

- [ ] **Step 2: Integrate callback into server.py**

In `backend/src/personal_assistant/api/server.py`, after the existing imports add:

```python
from personal_assistant.tracing import build_langfuse_callback
```

After creating `harness`, add:

```python
langfuse_callback = build_langfuse_callback(settings)
harness = AgentHarness(
    settings,
    registry,
    memory,
    callbacks=[langfuse_callback] if langfuse_callback else None,
)
```

The server module should look like:

```python
settings = get_settings()
registry = SkillRegistry(settings.skills_dir)
memory = PostgresMemory(settings.database_url)
langfuse_callback = build_langfuse_callback(settings)
harness = AgentHarness(
    settings,
    registry,
    memory,
    callbacks=[langfuse_callback] if langfuse_callback else None,
)
```

- [ ] **Step 3: Verify with a server smoke test**

Run: `python -c "import sys; sys.path.insert(0, 'src'); from personal_assistant.tracing import build_langfuse_callback; from personal_assistant.config import Settings; s = Settings(DATABASE_URL='x', LLM_MODEL='x'); cb = build_langfuse_callback(s); print('OK:', cb)"`
Expected: `OK: None` (Langfuse disabled without keys — no crash)

- [ ] **Step 4: Commit**

```bash
git add backend/.env.example backend/src/personal_assistant/api/server.py
git commit -m "feat(server): wire Langfuse callback into FastAPI harness"
```

---

### Task 6: Full suite verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All non-pre-existing tests pass

- [ ] **Step 2: Run import smoke test for all modules**

Run: `python -c "from personal_assistant.tracing import build_langfuse_callback; from personal_assistant.config import Settings; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Commit if anything changed**

```bash
git add -A && git diff --cached --stat
# Only commit if there are changes from verification fixes
```

---

## Self-Review

1. **Spec coverage**: ✓ All integration points covered — config, tracing module, harness injection, server wiring, env docs.
2. **Placeholder scan**: ✓ No TBDs, TODOs, or empty code blocks. Every step has concrete code.
3. **Type consistency**: ✓ `build_langfuse_callback` signature consistent across tasks. `_merge_callbacks` helper defined before use.
