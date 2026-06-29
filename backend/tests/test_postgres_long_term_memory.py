import pytest

from personal_assistant.memory.postgres import PostgresMemory


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
async def test_record_long_term_memory_inserts_memory_row() -> None:
    memory = PostgresMemory("postgresql://example")
    pool = _Pool()
    memory.pool = pool

    await memory.record_long_term_memory(
        slug="user-prefers-tabs",
        title="user-prefers-tabs",
        summary="User prefers tabs",
        body="User prefers tabs over spaces.",
    )

    sql, params = pool.conn.calls[0]
    assert "INSERT INTO long_term_memories" in sql
    assert params == (
        "user-prefers-tabs",
        "user-prefers-tabs",
        "User prefers tabs",
        "User prefers tabs over spaces.",
    )


@pytest.mark.asyncio
async def test_record_tool_result_inserts_tool_result_row() -> None:
    memory = PostgresMemory("postgresql://example")
    pool = _Pool()
    memory.pool = pool

    await memory.record_tool_result(
        thread_id="thread-1",
        tool_result_id="tool-result-1",
        tool_name="lookup",
        content="large result",
        metadata={"ok": True},
    )

    sql, params = pool.conn.calls[0]
    assert "INSERT INTO tool_results" in sql
    assert params[:4] == (
        "tool-result-1",
        "thread-1",
        "lookup",
        "large result",
    )
    assert params[4].obj == {"ok": True}
