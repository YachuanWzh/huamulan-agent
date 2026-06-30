from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from personal_assistant.agent.agent import _active_tools_for_state
from personal_assistant.agent.agent import _execute_tool_calls_with_retry
from personal_assistant.skills import SkillRegistry
from personal_assistant.tools.basic import build_basic_tools


def test_active_tools_include_basic_tools_even_without_selected_skills(
    skill_dir: Path, tmp_path: Path
):
    registry = SkillRegistry(skill_dir)
    basic_tools = build_basic_tools(tmp_path)

    tools = _active_tools_for_state(registry, [], basic_tools)
    names = {tool.name for tool in tools}

    assert "shell_command" in names
    assert "read_file" in names
    assert "do_thing" not in names


def test_active_tools_merge_basic_tools_with_selected_skill_tools(
    skill_dir: Path, tmp_path: Path
):
    registry = SkillRegistry(skill_dir)
    registry.load_skill("test-skill")
    basic_tools = build_basic_tools(tmp_path)

    tools = _active_tools_for_state(registry, ["test-skill"], basic_tools)
    names = {tool.name for tool in tools}

    assert "shell_command" in names
    assert "read_file" in names
    assert "do_thing" in names


class ToolErrorMemory:
    def __init__(self) -> None:
        self.events = []

    async def record_tool_error(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_tool_calls_retry_with_exponential_backoff_and_return_success() -> None:
    attempts = 0
    sleeps = []
    memory = ToolErrorMemory()

    @tool
    async def flaky_lookup(query: str) -> str:
        """Look up a value, failing twice before succeeding."""
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(f"temporary failure {attempts}")
        return f"found {query}"

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    messages = await _execute_tool_calls_with_retry(
        [flaky_lookup],
        [{"id": "call-1", "name": "flaky_lookup", "args": {"query": "alpha"}}],
        memory=memory,
        thread_id="thread-1",
        sleep=sleep,
        base_delay=0.25,
    )

    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert messages == [ToolMessage(tool_call_id="call-1", content="found alpha")]
    assert [event["attempt"] for event in memory.events] == [1, 2]
    assert memory.events[0]["tool_args"] == {"query": "alpha"}
    assert "temporary failure 1" in memory.events[0]["error_message"]


@pytest.mark.asyncio
async def test_tool_call_failure_after_three_retries_returns_error_and_args_to_agent() -> None:
    attempts = 0
    memory = ToolErrorMemory()

    @tool
    async def failing_lookup(query: str) -> str:
        """Always fail."""
        nonlocal attempts
        attempts += 1
        raise ValueError(f"bad query {query}")

    async def sleep(_delay: float) -> None:
        return None

    messages = await _execute_tool_calls_with_retry(
        [failing_lookup],
        [{"id": "call-2", "name": "failing_lookup", "args": {"query": "beta"}}],
        memory=memory,
        thread_id="thread-1",
        sleep=sleep,
    )

    assert attempts == 4
    assert len(memory.events) == 4
    assert memory.events[-1]["will_retry"] is False
    assert messages[0].tool_call_id == "call-2"
    assert "Tool call failed after 4 attempts" in str(messages[0].content)
    assert "failing_lookup" in str(messages[0].content)
    assert '{"query":"beta"}' in str(messages[0].content)
    assert "ValueError: bad query beta" in str(messages[0].content)
