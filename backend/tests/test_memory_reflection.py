from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from personal_assistant.agent.agent import (
    _build_compaction_summary_messages,
    _build_memory_reflection_messages,
    _memory_save_already_requested,
)


def test_memory_reflection_prompt_includes_five_summary_categories() -> None:
    messages = [HumanMessage(content="I prefer tabs"), AIMessage(content="Noted")]

    reflection_messages = _build_memory_reflection_messages(messages)

    prompt = "\n".join(message.content for message in reflection_messages)
    assert "==当前目标==" in prompt
    assert "==重要发现 / 决策==" in prompt
    assert "==已读 / 已改的文件==" in prompt
    assert "==剩余工作==" in prompt
    assert "==用户约束==" in prompt


def test_compaction_summary_prompt_includes_five_summary_categories() -> None:
    messages = [HumanMessage(content="Goal"), AIMessage(content="Decision")]

    summary_messages = _build_compaction_summary_messages(messages)
    prompt = "\n".join(message.content for message in summary_messages)

    assert "==当前目标==" in prompt
    assert "==重要发现 / 决策==" in prompt
    assert "==已读 / 已改的文件==" in prompt
    assert "==剩余工作==" in prompt
    assert "==用户约束==" in prompt


def test_compaction_summary_prompt_replaces_tool_result_with_reference() -> None:
    messages = [
        HumanMessage(content="Use the tool"),
        ToolMessage(content="secret raw tool output", tool_call_id="tool-result-1"),
    ]

    summary_messages = _build_compaction_summary_messages(messages)
    prompt = "\n".join(message.content for message in summary_messages)

    assert "secret raw tool output" not in prompt
    assert "[tool result can find by tool_result_id: tool-result-1]" in prompt


def test_memory_save_already_requested_detects_prior_save_tool_call() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "save-1",
                    "name": "save_conversation_memory",
                    "args": {"slug": "preference"},
                }
            ],
        ),
        ToolMessage(content="saved", tool_call_id="save-1"),
    ]

    assert _memory_save_already_requested(messages) is True
