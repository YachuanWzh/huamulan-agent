from datetime import UTC, datetime

from personal_assistant.api.schemas import AuditEvent, ExecutionLog, ExecutionLogCreate, ExecutionSummary
from personal_assistant.memory.cached import CachedPostgresMemory


class FakeCache:
    def __init__(self):
        self.values = {}
        self.deleted = []
        self.deleted_patterns = []
        self.get_errors = set()

    async def get_json(self, key):
        if key in self.get_errors:
            raise RuntimeError("cache unavailable")
        return self.values.get(key)

    async def set_json(self, key, value, ttl_seconds):
        self.values[key] = value

    async def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)

    async def delete_pattern(self, pattern):
        self.deleted_patterns.append(pattern)
        prefix = pattern.rstrip("*")
        for key in list(self.values):
            if key.startswith(prefix):
                self.values.pop(key, None)

    async def close(self):
        return None


class FakeMemory:
    def __init__(self):
        self.summary_calls = 0
        self.log_calls = 0
        self.thread_calls = 0
        self.audit_calls = 0
        self.tool_error_calls = 0
        self.recorded_logs = []
        self.deleted_threads = []
        self.cleared = False

    async def execution_log_summary(self, thread_id):
        self.summary_calls += 1
        return ExecutionSummary(thread_id=thread_id, total_events=self.summary_calls)

    async def list_execution_logs(self, thread_id, limit=500):
        self.log_calls += 1
        return [
            ExecutionLog(
                id=self.log_calls,
                created_at=datetime(2026, 6, 30, tzinfo=UTC),
                thread_id=thread_id,
                event_type="llm",
                status="completed",
            )
        ]

    async def list_threads(self, limit=100):
        self.thread_calls += 1
        return [{"thread_id": f"thread-{self.thread_calls}", "summary": "hello"}]

    async def list_audit_events(self, thread_id=None, limit=100):
        self.audit_calls += 1
        return [
            AuditEvent(
                id=self.audit_calls,
                created_at=datetime(2026, 6, 30, tzinfo=UTC),
                thread_id=thread_id,
                source="tool",
                category="tool_approval_requested",
                severity="LOW",
                reason="waiting",
            )
        ]

    async def list_tool_errors(self, thread_id=None, limit=100):
        self.tool_error_calls += 1
        return []

    async def record_execution_log(self, log):
        self.recorded_logs.append(log)

    async def delete_thread(self, thread_id):
        self.deleted_threads.append(thread_id)

    async def clear_threads(self):
        self.cleared = True
        return ["thread-1"]


async def test_execution_log_summary_uses_cached_value_after_first_read() -> None:
    memory = FakeMemory()
    cached = CachedPostgresMemory(memory, FakeCache())

    first = await cached.execution_log_summary("thread-1")
    second = await cached.execution_log_summary("thread-1")

    assert first.total_events == 1
    assert second.total_events == 1
    assert memory.summary_calls == 1


async def test_record_execution_log_invalidates_thread_execution_and_list_keys() -> None:
    cache = FakeCache()
    cached = CachedPostgresMemory(FakeMemory(), cache)

    await cached.record_execution_log(
        ExecutionLogCreate(thread_id="thread-1", event_type="llm", status="completed")
    )

    assert "pa:v1:execution_summary:thread-1" in cache.deleted
    assert "pa:v1:execution_logs:thread-1:*" in cache.deleted_patterns
    assert "pa:v1:threads:list:*" in cache.deleted_patterns


async def test_list_threads_cache_is_invalidated_by_delete_and_clear() -> None:
    cache = FakeCache()
    memory = FakeMemory()
    cached = CachedPostgresMemory(memory, cache)

    await cached.list_threads(limit=25)
    await cached.delete_thread("thread-1")
    await cached.clear_threads()

    assert memory.thread_calls == 1
    assert memory.deleted_threads == ["thread-1"]
    assert memory.cleared is True
    assert cache.deleted_patterns.count("pa:v1:threads:list:*") == 2


async def test_audit_events_are_cached_per_thread_and_all_threads() -> None:
    memory = FakeMemory()
    cached = CachedPostgresMemory(memory, FakeCache())

    await cached.list_audit_events(thread_id=None, limit=10)
    await cached.list_audit_events(thread_id=None, limit=10)
    await cached.list_audit_events(thread_id="thread-1", limit=10)

    assert memory.audit_calls == 2


async def test_cache_get_error_falls_back_to_underlying_memory() -> None:
    cache = FakeCache()
    cache.get_errors.add("pa:v1:execution_summary:thread-1")
    memory = FakeMemory()
    cached = CachedPostgresMemory(memory, cache)

    summary = await cached.execution_log_summary("thread-1")

    assert summary.total_events == 1
    assert memory.summary_calls == 1
