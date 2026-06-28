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
