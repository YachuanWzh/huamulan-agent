import pytest
from fastapi.testclient import TestClient

from personal_assistant.api import server
from personal_assistant.memory.postgres import PostgresMemory


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted_thread_ids: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_thread_ids.append(thread_id)


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
                },
                {
                    "thread_id": "thread-1",
                    "updated_at": "2026-06-29T04:00:00+00:00",
                },
            ]

    monkeypatch.setattr(server, "harness", FakeHarness())

    response = TestClient(server.app).get("/api/threads?limit=50")

    assert response.status_code == 200
    assert response.json() == [
        {"thread_id": "thread-2", "updated_at": "2026-06-29T05:00:00Z"},
        {"thread_id": "thread-1", "updated_at": "2026-06-29T04:00:00Z"},
    ]
