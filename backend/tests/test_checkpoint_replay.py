from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import CheckpointTuple

import personal_assistant.memory.postgres as postgres_memory
from personal_assistant.memory.postgres import _serialize_checkpoint


def test_serialize_checkpoint_exposes_replayable_state() -> None:
    checkpoint = CheckpointTuple(
        config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_id": "checkpoint-2",
            }
        },
        parent_config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_id": "checkpoint-1",
            }
        },
        checkpoint={
            "id": "checkpoint-2",
            "ts": "2026-06-29T04:00:00+00:00",
            "channel_values": {
                "messages": [
                    HumanMessage(content="Hi"),
                    AIMessage(content="Hello"),
                ],
                "selected_skills": ["resolve-time"],
                "pending_approvals": [],
            },
        },
        metadata={"source": "loop", "step": 2, "writes": {"agent": {"messages": []}}},
    )

    result = _serialize_checkpoint(checkpoint)

    assert result["checkpoint_id"] == "checkpoint-2"
    assert result["parent_checkpoint_id"] == "checkpoint-1"
    assert result["created_at"] == "2026-06-29T04:00:00+00:00"
    assert result["node"] == "agent"
    assert result["values"]["selected_skills"] == ["resolve-time"]
    assert result["messages"] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
    ]


def test_serialize_checkpoint_handles_non_mapping_encoded_payload(
    monkeypatch,
) -> None:
    checkpoint = CheckpointTuple(
        config={"configurable": {"checkpoint_id": "checkpoint-2"}},
        parent_config=None,
        checkpoint={
            "id": "checkpoint-2",
            "channel_values": {"messages": [HumanMessage(content="Hi")]},
        },
        metadata={},
    )
    monkeypatch.setattr(postgres_memory, "_jsonable", lambda _value: ["encoded"])

    result = _serialize_checkpoint(checkpoint)

    assert result["checkpoint_id"] == "checkpoint-2"
    assert result["checkpoint"] == ["encoded"]
