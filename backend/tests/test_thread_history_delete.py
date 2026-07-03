import pytest
from fastapi.testclient import TestClient

from personal_assistant.api import server
from personal_assistant.memory.postgres import PostgresMemory, _thread_summary_from_checkpoint


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted_thread_ids: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_thread_ids.append(thread_id)


class _Cursor:
    async def fetchall(self):
        return []


class _Connection:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor()


class _ConnectionContext:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _Pool:
    def __init__(self) -> None:
        self.conn = _Connection()

    def connection(self):
        return _ConnectionContext(self.conn)


@pytest.mark.asyncio
async def test_delete_thread_uses_langgraph_postgres_delete() -> None:
    memory = PostgresMemory("postgresql://example")
    checkpointer = FakeCheckpointer()
    memory.checkpointer = checkpointer  # type: ignore[assignment]

    await memory.delete_thread("thread-1")

    assert checkpointer.deleted_thread_ids == ["thread-1"]


def test_delete_thread_endpoint_deletes_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHarness:
        def __init__(self) -> None:
            self.deleted_thread_ids: list[str] = []

        async def delete_thread(self, thread_id: str) -> None:
            self.deleted_thread_ids.append(thread_id)

    fake_harness = FakeHarness()
    monkeypatch.setattr(server, "harness", fake_harness)

    response = TestClient(server.app).delete("/api/threads/thread-1")

    assert response.status_code == 200
    assert response.json() == {"thread_id": "thread-1", "deleted": True}
    assert fake_harness.deleted_thread_ids == ["thread-1"]


@pytest.mark.asyncio
async def test_list_threads_excludes_internal_skill_evaluation_runs() -> None:
    memory = PostgresMemory("postgresql://example")
    pool = _Pool()
    memory.pool = pool

    await memory.list_threads()

    sql, _ = pool.conn.calls[0]
    assert "thread_id NOT LIKE 'skill-eval-%'" in sql


@pytest.mark.asyncio
async def test_clear_threads_deletes_each_thread_checkpoint() -> None:
    memory = PostgresMemory("postgresql://example")
    checkpointer = FakeCheckpointer()
    memory.checkpointer = checkpointer  # type: ignore[assignment]
    async def fake_list_threads(limit=500):
        return [
            {"thread_id": "thread-1", "updated_at": None},
            {"thread_id": "thread-2", "updated_at": None},
        ]

    memory.list_threads = fake_list_threads  # type: ignore[method-assign]

    deleted = await memory.clear_threads()

    assert deleted == ["thread-1", "thread-2"]
    assert checkpointer.deleted_thread_ids == ["thread-1", "thread-2"]


def test_clear_threads_endpoint_deletes_all_thread_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHarness:
        async def clear_threads(self):
            return ["thread-1", "thread-2"]

    monkeypatch.setattr(server, "harness", FakeHarness())

    response = TestClient(server.app).delete("/api/threads")

    assert response.status_code == 200
    assert response.json() == {"thread_ids": ["thread-1", "thread-2"], "deleted": 2}


def test_list_threads_endpoint_returns_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHarness:
        async def list_threads(self, limit: int = 100):
            assert limit == 50
            return [
                {
                    "thread_id": "thread-2",
                    "updated_at": "2026-06-29T05:00:00+00:00",
                    "summary": "修复首次发送消息丢失",
                },
                {
                    "thread_id": "thread-1",
                    "updated_at": "2026-06-29T04:00:00+00:00",
                    "summary": None,
                },
            ]

    monkeypatch.setattr(server, "harness", FakeHarness())

    response = TestClient(server.app).get("/api/threads?limit=50")

    assert response.status_code == 200
    assert response.json() == [
        {
            "thread_id": "thread-2",
            "updated_at": "2026-06-29T05:00:00Z",
            "summary": "修复首次发送消息丢失",
        },
        {
            "thread_id": "thread-1",
            "updated_at": "2026-06-29T04:00:00Z",
            "summary": None,
        },
    ]


def test_thread_summary_uses_first_user_message_from_checkpoint() -> None:
    checkpoint = {
        "channel_values": {
            "messages": [
                {"type": "system", "content": "hidden"},
                {"type": "human", "content": "帮我修复首次发送消息被吞掉的问题"},
                {"type": "ai", "content": "好的"},
            ],
        }
    }

    assert _thread_summary_from_checkpoint(checkpoint) == "帮我修复首次发送消息被吞掉的问题"
