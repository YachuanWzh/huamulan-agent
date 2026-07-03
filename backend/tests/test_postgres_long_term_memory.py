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


@pytest.mark.asyncio
async def test_record_tool_error_inserts_error_archive_row() -> None:
    memory = PostgresMemory("postgresql://example")
    pool = _Pool()
    memory.pool = pool

    await memory.record_tool_error(
        thread_id="thread-1",
        tool_call_id="tool-call-1",
        tool_name="lookup",
        tool_args={"query": "alpha"},
        attempt=2,
        max_attempts=3,
        error_type="RuntimeError",
        error_message="temporary failure",
        will_retry=True,
    )

    sql, params = pool.conn.calls[0]
    assert "INSERT INTO tool_errors" in sql
    assert params[:5] == (
        "thread-1",
        "tool-call-1",
        "lookup",
        2,
        3,
    )
    assert params[5].obj == {"query": "alpha"}
    assert params[6:] == ("RuntimeError", "temporary failure", True)


@pytest.mark.asyncio
async def test_list_tool_errors_maps_rows_to_schema() -> None:
    class _RowsCursor:
        async def fetchall(self):
            return [
                (
                    9,
                    "2026-06-30T01:00:00+00:00",
                    "thread-1",
                    "tool-call-1",
                    "lookup",
                    {"query": "alpha"},
                    3,
                    3,
                    "ValueError",
                    "bad query",
                    False,
                )
            ]

    class _RowsConnection(_Connection):
        async def execute(self, sql, params=None):
            self.calls.append((sql, params))
            return _RowsCursor()

    class _RowsPool(_Pool):
        def __init__(self) -> None:
            self.conn = _RowsConnection()

    memory = PostgresMemory("postgresql://example")
    pool = _RowsPool()
    memory.pool = pool

    errors = await memory.list_tool_errors(thread_id="thread-1", limit=25)

    sql, params = pool.conn.calls[0]
    assert "FROM tool_errors" in sql
    assert params == ("thread-1", 25)
    assert errors[0].id == 9
    assert errors[0].tool_name == "lookup"
    assert errors[0].tool_args == {"query": "alpha"}
    assert errors[0].error_message == "bad query"


@pytest.mark.asyncio
async def test_list_skill_evaluation_history_maps_rows_newest_first() -> None:
    class _RowsCursor:
        async def fetchall(self):
            return [
                (
                    12,
                    "2026-07-03T01:00:00+00:00",
                    "weather",
                    0.91,
                    1.0,
                    0.8,
                    None,
                    0.7,
                    "golden:e2e.jsonl",
                    {"skill_name": "weather"},
                ),
                (
                    9,
                    "2026-07-02T01:00:00+00:00",
                    "weather",
                    0.72,
                    0.8,
                    None,
                    None,
                    0.6,
                    "golden:quick.jsonl",
                    {"skill_name": "weather"},
                ),
            ]

    class _RowsConnection(_Connection):
        async def execute(self, sql, params=None):
            self.calls.append((sql, params))
            return _RowsCursor()

    class _RowsPool(_Pool):
        def __init__(self) -> None:
            self.conn = _RowsConnection()

    memory = PostgresMemory("postgresql://example")
    pool = _RowsPool()
    memory.pool = pool

    history = await memory.list_skill_evaluation_history(skill_name="weather", limit=25)

    sql, params = pool.conn.calls[0]
    assert "FROM skill_evaluation_results" in sql
    assert "WHERE skill_name = %s" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert params == ("weather", 25)
    assert [item.id for item in history] == [12, 9]
    assert history[0].overall_score == 0.91
