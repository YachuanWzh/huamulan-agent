from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

from personal_assistant.agent.agent import _record_tool_result_messages, _replace_messages_update
from personal_assistant.memory.compaction import ContextCompactor


def test_replace_messages_update_clears_reducer_state_before_new_messages() -> None:
    message = HumanMessage(content="[Compacted]\nsummary")

    update = _replace_messages_update([message])

    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert update["messages"][1:] == [message]


def test_compactor_uses_approval_turn_count_for_trigger(tmp_path) -> None:
    messages = [HumanMessage(content=f"user {index}") for index in range(20)]
    compactor = ContextCompactor(transcript_dir=tmp_path)

    assert compactor.should_compact(messages, additional_turns=1) is True


async def test_record_tool_result_messages_persists_tool_messages() -> None:
    class Memory:
        def __init__(self) -> None:
            self.records = []

        async def record_tool_result(self, **kwargs):
            self.records.append(kwargs)

    memory = Memory()

    await _record_tool_result_messages(
        memory,
        thread_id="thread-1",
        messages=[ToolMessage(content="large result", tool_call_id="tool-result-1")],
        tool_calls=[{"id": "tool-result-1", "name": "lookup", "args": {"q": "x"}}],
    )

    assert memory.records == [
        {
            "thread_id": "thread-1",
            "tool_result_id": "tool-result-1",
            "tool_name": "lookup",
            "content": "large result",
            "metadata": {"tool_call_id": "tool-result-1"},
        }
    ]
