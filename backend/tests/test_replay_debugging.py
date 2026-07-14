from personal_assistant.debugging.replay import (
    create_fork_descriptor,
    diff_checkpoint_states,
    find_checkpoint,
)


def test_diff_checkpoint_states_groups_recursive_changes() -> None:
    before = {"values": {"route": "a", "count": 1, "removed": True}}
    after = {"values": {"route": "b", "count": "1", "tools": ["x"]}}

    diff = diff_checkpoint_states(before, after)

    assert "values.route" in [item.path for item in diff.changed]
    assert "values.count" in [item.path for item in diff.changed]
    assert "values.tools" in [item.path for item in diff.added]
    assert "values.removed" in [item.path for item in diff.removed]


def test_diff_checkpoint_states_summarizes_messages_and_truncates_values() -> None:
    before = {"messages": [{"type": "human", "content": "hello"}]}
    after = {"messages": [{"type": "ai", "content": "x" * 3000}]}

    diff = diff_checkpoint_states(before, after)

    item = next(change for change in diff.changed if change.path == "messages[0].type")
    assert item.before == "human"
    content = next(change for change in diff.changed if change.path == "messages[0].content")
    assert len(content.after) <= 2000


def test_find_checkpoint_and_create_safe_fork_descriptor() -> None:
    states = [
        {"checkpoint_id": "cp-1", "values": {"route": "a"}},
        {"config": {"configurable": {"checkpoint_id": "cp-2"}}, "values": {"route": "b"}},
    ]

    checkpoint = find_checkpoint(states, "cp-2")
    fork = create_fork_descriptor(
        source_thread_id="thread-1",
        checkpoint=checkpoint,
        target_thread_id=None,
    )

    assert checkpoint.checkpoint_id == "cp-2"
    assert fork.target_thread_id.startswith("fork-thread-1-")
    assert fork.source_checkpoint_id == "cp-2"
    assert fork.execute is False


def test_find_checkpoint_raises_explicit_error() -> None:
    try:
        find_checkpoint([], "missing")
    except LookupError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected LookupError")


async def test_replay_debug_endpoints_only_read_replay_state(monkeypatch) -> None:
    from personal_assistant.api import server
    from personal_assistant.debugging.replay import ReplayDiffRequest, ReplayForkRequest

    calls = []

    async def replay(thread_id: str):
        calls.append(thread_id)
        return [
            {"checkpoint_id": "cp-1", "values": {"route": "a"}},
            {"checkpoint_id": "cp-2", "values": {"route": "b"}},
        ]

    monkeypatch.setattr(server.harness, "replay", replay)

    diff = await server.diff_replay(
        "thread-1",
        ReplayDiffRequest(before_checkpoint_id="cp-1", after_checkpoint_id="cp-2"),
    )
    fork = await server.create_replay_fork(
        "thread-1",
        ReplayForkRequest(checkpoint_id="cp-2"),
    )

    assert diff.changed[0].path == "values.route"
    assert fork.execute is False
    assert calls == ["thread-1", "thread-1"]
