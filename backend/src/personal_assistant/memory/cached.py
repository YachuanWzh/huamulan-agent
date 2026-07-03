from typing import Any

from fastapi.encoders import jsonable_encoder

from personal_assistant.api.schemas import (
    AuditEvent,
    ExecutionLog,
    ExecutionSummary,
    SkillEvaluationSnapshot,
    ToolError,
)
from personal_assistant.cache import AsyncCache


PREFIX = "pa:v1"


class CachedPostgresMemory:
    def __init__(
        self,
        inner: Any,
        cache: AsyncCache,
        *,
        default_ttl_seconds: int = 10,
        log_ttl_seconds: int = 5,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.default_ttl_seconds = default_ttl_seconds
        self.log_ttl_seconds = log_ttl_seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def list_threads(self, limit: int = 100) -> list[dict[str, Any]]:
        key = f"{PREFIX}:threads:list:{limit}"
        return await self._cached(key, self.default_ttl_seconds, self.inner.list_threads, limit=limit)

    async def list_execution_logs(self, thread_id: str, limit: int = 500) -> list[ExecutionLog]:
        key = f"{PREFIX}:execution_logs:{_key_part(thread_id)}:{limit}"
        data = await self._cached(
            key,
            self.log_ttl_seconds,
            self.inner.list_execution_logs,
            thread_id=thread_id,
            limit=limit,
        )
        return [_model(ExecutionLog, item) for item in data]

    async def execution_log_summary(self, thread_id: str) -> ExecutionSummary:
        key = f"{PREFIX}:execution_summary:{_key_part(thread_id)}"
        data = await self._cached(
            key,
            self.default_ttl_seconds,
            self.inner.execution_log_summary,
            thread_id=thread_id,
        )
        return _model(ExecutionSummary, data)

    async def list_audit_events(
        self,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        scope = _key_part(thread_id) if thread_id else "all"
        key = f"{PREFIX}:audit_events:{scope}:{limit}"
        data = await self._cached(
            key,
            self.default_ttl_seconds,
            self.inner.list_audit_events,
            thread_id=thread_id,
            limit=limit,
        )
        return [_model(AuditEvent, item) for item in data]

    async def list_tool_errors(
        self,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> list[ToolError]:
        scope = _key_part(thread_id) if thread_id else "all"
        key = f"{PREFIX}:tool_errors:{scope}:{limit}"
        data = await self._cached(
            key,
            self.default_ttl_seconds,
            self.inner.list_tool_errors,
            thread_id=thread_id,
            limit=limit,
        )
        return [_model(ToolError, item) for item in data]

    async def list_skill_evaluation_history(
        self,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[SkillEvaluationSnapshot]:
        scope = _key_part(skill_name) if skill_name else "all"
        key = f"{PREFIX}:skills:evaluation_history:{scope}:{limit}"
        data = await self._cached(
            key,
            self.default_ttl_seconds,
            self.inner.list_skill_evaluation_history,
            skill_name=skill_name,
            limit=limit,
        )
        return [_model(SkillEvaluationSnapshot, item) for item in data]

    async def record_execution_log(self, log) -> None:
        await self.inner.record_execution_log(log)
        thread_id = _key_part(getattr(log, "thread_id", ""))
        await self._delete(f"{PREFIX}:execution_summary:{thread_id}")
        await self._delete_pattern(f"{PREFIX}:execution_logs:{thread_id}:*")
        await self._delete_pattern(f"{PREFIX}:threads:list:*")

    async def record_audit_event(self, event) -> None:
        await self.inner.record_audit_event(event)
        thread_id = _key_part(getattr(event, "thread_id", ""))
        await self._delete_pattern(f"{PREFIX}:audit_events:all:*")
        await self._delete_pattern(f"{PREFIX}:audit_events:{thread_id}:*")

    async def record_tool_error(self, **kwargs) -> None:
        await self.inner.record_tool_error(**kwargs)
        thread_id = _key_part(kwargs.get("thread_id", ""))
        await self._delete_pattern(f"{PREFIX}:tool_errors:all:*")
        await self._delete_pattern(f"{PREFIX}:tool_errors:{thread_id}:*")

    async def record_skill_evaluation_results(self, report, *, source=None) -> None:
        await self.inner.record_skill_evaluation_results(report, source=source)
        await self._delete_pattern(f"{PREFIX}:skills:*")

    async def reset_skill_evaluation_results(self) -> int:
        deleted = await self.inner.reset_skill_evaluation_results()
        await self._delete_pattern(f"{PREFIX}:skills:*")
        return deleted

    async def delete_thread(self, thread_id: str) -> None:
        await self.inner.delete_thread(thread_id)
        safe_thread = _key_part(thread_id)
        await self._delete(f"{PREFIX}:execution_summary:{safe_thread}")
        await self._delete_pattern(f"{PREFIX}:execution_logs:{safe_thread}:*")
        await self._delete_pattern(f"{PREFIX}:audit_events:{safe_thread}:*")
        await self._delete_pattern(f"{PREFIX}:tool_errors:{safe_thread}:*")
        await self._delete_pattern(f"{PREFIX}:threads:list:*")

    async def clear_threads(self) -> list[str]:
        thread_ids = await self.inner.clear_threads()
        for pattern in (
            f"{PREFIX}:threads:list:*",
            f"{PREFIX}:execution_summary:*",
            f"{PREFIX}:execution_logs:*",
            f"{PREFIX}:audit_events:*",
            f"{PREFIX}:tool_errors:*",
        ):
            await self._delete_pattern(pattern)
        return thread_ids

    async def _cached(self, key: str, ttl_seconds: int, fn, **kwargs):
        try:
            cached = await self.cache.get_json(key)
        except Exception:
            cached = None
        if cached is not None:
            return cached
        value = await fn(**kwargs)
        try:
            await self.cache.set_json(key, jsonable_encoder(value), ttl_seconds=ttl_seconds)
        except Exception:
            pass
        return value

    async def _delete(self, key: str) -> None:
        try:
            await self.cache.delete(key)
        except Exception:
            pass

    async def _delete_pattern(self, pattern: str) -> None:
        try:
            await self.cache.delete_pattern(pattern)
        except Exception:
            pass


def _model(model_type, value):
    if isinstance(value, model_type):
        return value
    return model_type(**value)


def _key_part(value: Any) -> str:
    text = str(value or "none")
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
