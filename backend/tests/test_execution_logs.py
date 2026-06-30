from datetime import UTC, datetime

from langchain_core.messages import AIMessage

from personal_assistant.agent.agent import _execute_tool_calls_with_retry, _extract_token_usage
from personal_assistant.api.schemas import ExecutionLogCreate, ExecutionSummary
from personal_assistant.memory.postgres import PostgresMemory


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return FakeCursor(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self):
        return self.conn


async def test_record_execution_log_inserts_jsonb_payloads() -> None:
    conn = FakeConnection()
    memory = PostgresMemory("postgresql://example")
    memory.pool = FakePool(conn)

    await memory.record_execution_log(
        ExecutionLogCreate(
            thread_id="thread-1",
            event_type="llm",
            status="completed",
            name="agent",
            input={"messages": 2},
            output={"text": "hello"},
            token_usage={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
            metadata={"model": "test-model"},
        )
    )

    sql, params = conn.calls[0]
    assert "INSERT INTO agent_execution_logs" in sql
    assert params[0] == "thread-1"
    assert params[3] == "llm"
    assert params[4] == "completed"
    assert params[5] == "agent"


async def test_list_execution_logs_returns_thread_logs_in_database_order() -> None:
    created_at = datetime(2026, 6, 30, 1, 2, 3, tzinfo=UTC)
    conn = FakeConnection(
        rows=[
            (
                7,
                created_at,
                "thread-1",
                "run-1",
                None,
                "tool",
                "completed",
                "lookup",
                {"query": "alpha"},
                {"result": "ok"},
                {},
                25,
                {"total_tokens": 0},
                {"tool_call_id": "call-1"},
            )
        ]
    )
    memory = PostgresMemory("postgresql://example")
    memory.pool = FakePool(conn)

    logs = await memory.list_execution_logs("thread-1", limit=1000)

    assert len(logs) == 1
    assert logs[0].id == 7
    assert logs[0].thread_id == "thread-1"
    assert logs[0].event_type == "tool"
    assert logs[0].status == "completed"
    assert logs[0].duration_ms == 25
    assert logs[0].metadata["tool_call_id"] == "call-1"
    sql, params = conn.calls[0]
    assert "WHERE thread_id = %s" in sql
    assert params == ("thread-1", 500)


async def test_execution_log_summary_aggregates_counts_and_tokens() -> None:
    conn = FakeConnection(
        rows=[
            (
                5,
                3,
                2,
                1,
                1,
                1200,
                800,
                2000,
                345,
            )
        ]
    )
    memory = PostgresMemory("postgresql://example")
    memory.pool = FakePool(conn)

    summary = await memory.execution_log_summary("thread-1")

    assert isinstance(summary, ExecutionSummary)
    assert summary.thread_id == "thread-1"
    assert summary.total_events == 5
    assert summary.tool_calls == 3
    assert summary.tool_errors == 2
    assert summary.tool_retries == 1
    assert summary.security_events == 1
    assert summary.prompt_tokens == 1200
    assert summary.completion_tokens == 800
    assert summary.total_tokens == 2000
    assert summary.total_duration_ms == 345


class RetryMemory:
    def __init__(self):
        self.execution_logs = []
        self.tool_errors = []

    async def record_execution_log(self, log):
        self.execution_logs.append(log)

    async def record_tool_error(self, **kwargs):
        self.tool_errors.append(kwargs)


class FlakyTool:
    name = "lookup"

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, args, config=None):
        self.calls += 1
        if self.calls < 3:
            raise ValueError(f"bad query {self.calls}")
        return {"answer": "ok"}


async def no_sleep(_seconds):
    return None


def test_extract_token_usage_normalizes_response_metadata() -> None:
    message = AIMessage(
        content="hello",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 4,
                "completion_tokens": 6,
                "total_tokens": 10,
            }
        },
    )

    usage = _extract_token_usage(message)

    assert usage["prompt_tokens"] == 4
    assert usage["completion_tokens"] == 6
    assert usage["total_tokens"] == 10
    assert usage["raw"]["prompt_tokens"] == 4


async def test_tool_retries_record_execution_logs_for_each_failed_attempt() -> None:
    memory = RetryMemory()
    tool = FlakyTool()

    messages = await _execute_tool_calls_with_retry(
        [tool],
        [{"id": "call-1", "name": "lookup", "args": {"query": "alpha"}}],
        memory=memory,
        thread_id="thread-1",
        sleep=no_sleep,
    )

    assert messages[0].content == '{"answer": "ok"}'
    retry_logs = [
        log for log in memory.execution_logs if log.event_type == "tool_retry"
    ]
    tool_logs = [
        log for log in memory.execution_logs if log.event_type == "tool"
    ]
    assert len(retry_logs) == 2
    assert retry_logs[0].status == "retrying"
    assert retry_logs[0].metadata["attempt"] == 1
    assert retry_logs[0].metadata["will_retry"] is True
    assert retry_logs[1].metadata["attempt"] == 2
    assert len(tool_logs) == 1
    assert tool_logs[0].status == "completed"
    assert tool_logs[0].metadata["tool_call_id"] == "call-1"
