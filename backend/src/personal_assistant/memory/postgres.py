import ast
import json
import logging
from typing import Any

from fastapi.encoders import jsonable_encoder
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from personal_assistant.cache.redis_cache import configure_redis_lru
from personal_assistant.checkpoint.redis_first import RedisFirstCheckpointSaver
from personal_assistant.checkpoint.serde import CompressedJsonPlusSerializer
from personal_assistant.api.schemas import (
    AuditEvent,
    AuditEventCreate,
    ExecutionLog,
    ExecutionLogCreate,
    ExecutionSummary,
    SkillEvaluationSnapshot,
    ToolError,
)
from personal_assistant.skills.evaluation.models import SkillEvaluationReport

logger = logging.getLogger(__name__)


class PostgresMemory:
    def __init__(
        self,
        database_url: str,
        *,
        redis_url: str | None = None,
        checkpoint_ttl_seconds: int = 604800,
        checkpoint_pg_cleanup_enabled: bool = True,
        checkpoint_redis_lru_enabled: bool = True,
        checkpoint_redis_maxmemory_policy: str = "allkeys-lru",
        checkpoint_skip_nodes: list[str] | None = None,
    ):
        self.database_url = database_url
        self.redis_url = redis_url
        self.checkpoint_ttl_seconds = checkpoint_ttl_seconds
        self.checkpoint_pg_cleanup_enabled = checkpoint_pg_cleanup_enabled
        self.checkpoint_redis_lru_enabled = checkpoint_redis_lru_enabled
        self.checkpoint_redis_maxmemory_policy = checkpoint_redis_maxmemory_policy
        self.checkpoint_skip_nodes = checkpoint_skip_nodes or [
            "route_skills",
            "compact_context",
        ]
        self.pool: AsyncConnectionPool | None = None
        self.checkpointer: Any | None = None
        self._checkpoint_redis = None

    async def start(self) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=self.database_url,
            open=False,
            kwargs={"autocommit": True},
        )
        await self.pool.open()
        postgres_saver = AsyncPostgresSaver(
            self.pool,
            serde=CompressedJsonPlusSerializer(),
        )
        self.checkpointer = await self._build_checkpointer(postgres_saver)
        await self.checkpointer.setup()
        if self.checkpoint_pg_cleanup_enabled:
            await self.cleanup_expired_checkpoints()
        await self._setup_audit_events()
        await self._setup_execution_logs()
        await self._setup_long_term_memories()
        await self._setup_tool_results()
        await self._setup_tool_errors()
        await self._setup_skill_evaluation_results()

    async def stop(self) -> None:
        drain = getattr(self.checkpointer, "drain", None)
        if callable(drain):
            await drain()
        if self._checkpoint_redis is not None:
            await self._checkpoint_redis.aclose()
        if self.pool is not None:
            await self.pool.close()

    async def _build_checkpointer(self, postgres_saver: AsyncPostgresSaver):
        if not self.redis_url:
            return postgres_saver
        from redis.asyncio import Redis

        redis_client = Redis.from_url(self.redis_url, decode_responses=False)
        self._checkpoint_redis = redis_client
        if self.checkpoint_redis_lru_enabled:
            await configure_redis_lru(
                redis_client,
                self.checkpoint_redis_maxmemory_policy,
            )
        return RedisFirstCheckpointSaver(
            postgres_saver,
            redis_client,
            ttl_seconds=self.checkpoint_ttl_seconds,
            skip_nodes=set(self.checkpoint_skip_nodes),
        )

    async def cleanup_expired_checkpoints(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                WITH expired AS (
                    SELECT thread_id, checkpoint_ns, checkpoint_id
                    FROM checkpoints
                    WHERE (checkpoint->>'ts')::timestamptz
                        < now() - (%s * interval '1 second')
                )
                DELETE FROM checkpoint_writes w
                USING expired e
                WHERE w.thread_id = e.thread_id
                  AND w.checkpoint_ns = e.checkpoint_ns
                  AND w.checkpoint_id = e.checkpoint_id
                """,
                (self.checkpoint_ttl_seconds,),
            )
            await conn.execute(
                """
                WITH expired AS (
                    SELECT thread_id, checkpoint_ns, checkpoint_id
                    FROM checkpoints
                    WHERE (checkpoint->>'ts')::timestamptz
                        < now() - (%s * interval '1 second')
                )
                DELETE FROM checkpoints c
                USING expired e
                WHERE c.thread_id = e.thread_id
                  AND c.checkpoint_ns = e.checkpoint_ns
                  AND c.checkpoint_id = e.checkpoint_id
                """,
                (self.checkpoint_ttl_seconds,),
            )
            await conn.execute(
                """
                DELETE FROM checkpoint_blobs b
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM checkpoints c
                    WHERE c.thread_id = b.thread_id
                      AND c.checkpoint_ns = b.checkpoint_ns
                )
                """,
            )

    async def replay(self, thread_id: str) -> list[dict[str, Any]]:
        if self.checkpointer is None:
            raise RuntimeError("Postgres memory is not started")
        states: list[dict[str, Any]] = []
        config = {"configurable": {"thread_id": thread_id}}
        async for checkpoint in self.checkpointer.alist(config):
            states.append(_serialize_checkpoint(checkpoint))
        return states

    async def list_threads(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (thread_id)
                        thread_id,
                        (checkpoint->>'ts')::timestamptz AS updated_at,
                        checkpoint
                    FROM checkpoints
                    WHERE thread_id NOT LIKE 'skill-eval-%'
                    ORDER BY thread_id, (checkpoint->>'ts')::timestamptz DESC NULLS LAST
                )
                SELECT thread_id, updated_at, checkpoint
                FROM latest
                ORDER BY updated_at DESC NULLS LAST, thread_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "thread_id": row[0],
                "updated_at": row[1],
                "summary": _thread_summary_from_checkpoint(row[2]),
            }
            for row in rows
        ]

    async def delete_thread(self, thread_id: str) -> None:
        if self.checkpointer is None:
            raise RuntimeError("Postgres memory is not started")
        await self.checkpointer.adelete_thread(thread_id)
        if self.pool is not None:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM audit_events WHERE thread_id = %s",
                    (thread_id,),
                )
                await conn.execute(
                    "DELETE FROM tool_errors WHERE thread_id = %s",
                    (thread_id,),
                )
                await conn.execute(
                    "DELETE FROM agent_execution_logs WHERE thread_id = %s",
                    (thread_id,),
                )

    async def clear_threads(self) -> list[str]:
        thread_ids = [
            thread["thread_id"]
            for thread in await self.list_threads(limit=500)
            if thread.get("thread_id")
        ]
        for thread_id in thread_ids:
            await self.delete_thread(thread_id)
        return thread_ids

    async def record_audit_event(self, event: AuditEventCreate) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO audit_events
                    (thread_id, source, category, severity, reason, subject, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    event.thread_id,
                    event.source,
                    event.category,
                    event.severity,
                    event.reason,
                    event.subject,
                    Jsonb(_jsonable(event.metadata)),
                ),
            )

    async def record_execution_log(self, log: ExecutionLogCreate) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO agent_execution_logs
                    (thread_id, run_id, parent_id, event_type, status, name,
                     input, output, error, duration_ms, token_usage, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s::jsonb, %s::jsonb)
                """,
                (
                    log.thread_id,
                    log.run_id,
                    log.parent_id,
                    log.event_type,
                    log.status,
                    log.name,
                    Jsonb(_jsonable(log.input)),
                    Jsonb(_jsonable(log.output)),
                    Jsonb(_jsonable(log.error)),
                    log.duration_ms,
                    Jsonb(_jsonable(log.token_usage)),
                    Jsonb(_jsonable(log.metadata)),
                ),
            )

    async def record_long_term_memory(
        self,
        *,
        slug: str,
        title: str,
        summary: str,
        body: str,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO long_term_memories (slug, title, summary, body)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    body = EXCLUDED.body,
                    updated_at = now()
                """,
                (slug, title, summary, body),
            )

    async def record_tool_result(
        self,
        *,
        thread_id: str | None,
        tool_result_id: str,
        tool_name: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO tool_results
                    (tool_result_id, thread_id, tool_name, content, metadata)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (tool_result_id) DO UPDATE SET
                    thread_id = EXCLUDED.thread_id,
                    tool_name = EXCLUDED.tool_name,
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                """,
                (
                    tool_result_id,
                    thread_id,
                    tool_name,
                    content,
                    Jsonb(_jsonable(metadata or {})),
                ),
            )

    async def record_tool_error(
        self,
        *,
        thread_id: str | None,
        tool_call_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        attempt: int,
        max_attempts: int,
        error_type: str,
        error_message: str,
        will_retry: bool,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO tool_errors
                    (thread_id, tool_call_id, tool_name, attempt, max_attempts,
                     tool_args, error_type, error_message, will_retry)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    thread_id,
                    tool_call_id,
                    tool_name,
                    attempt,
                    max_attempts,
                    Jsonb(_jsonable(tool_args)),
                    error_type,
                    error_message,
                    will_retry,
                ),
            )

    async def record_skill_evaluation_results(
        self,
        report: SkillEvaluationReport,
        *,
        source: str | None = None,
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            for result in report.skills:
                components = result.score_components
                await conn.execute(
                    """
                    INSERT INTO skill_evaluation_results
                        (skill_name, overall_score, routing_score, runtime_score,
                         usage_score, static_score, source, report)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        result.skill_name,
                        result.overall_score,
                        components.get("routing"),
                        components.get("runtime"),
                        components.get("usage"),
                        components.get("static"),
                        source,
                        Jsonb(_jsonable(result.model_dump())),
                    ),
                )

    async def list_latest_skill_evaluations(
        self,
        limit: int = 100,
    ) -> list[SkillEvaluationSnapshot]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT ON (skill_name)
                    id, created_at, skill_name, overall_score, routing_score,
                    runtime_score, usage_score, static_score, source, report
                FROM skill_evaluation_results
                ORDER BY skill_name, created_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            SkillEvaluationSnapshot(
                id=row[0],
                created_at=row[1],
                skill_name=row[2],
                overall_score=row[3],
                routing_score=row[4],
                runtime_score=row[5],
                usage_score=row[6],
                static_score=row[7],
                source=row[8],
                report=row[9] or {},
            )
            for row in rows
        ]

    async def list_skill_evaluation_history(
        self,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[SkillEvaluationSnapshot]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            if skill_name:
                cursor = await conn.execute(
                    """
                    SELECT id, created_at, skill_name, overall_score, routing_score,
                           runtime_score, usage_score, static_score, source, report
                    FROM skill_evaluation_results
                    WHERE skill_name = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (skill_name, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT id, created_at, skill_name, overall_score, routing_score,
                           runtime_score, usage_score, static_score, source, report
                    FROM skill_evaluation_results
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = await cursor.fetchall()
        return [
            SkillEvaluationSnapshot(
                id=row[0],
                created_at=row[1],
                skill_name=row[2],
                overall_score=_normalized_score(row[3]),
                routing_score=_normalized_optional_score(row[4]),
                runtime_score=_normalized_optional_score(row[5]),
                usage_score=_normalized_optional_score(row[6]),
                static_score=_normalized_optional_score(row[7]),
                source=row[8],
                report=row[9] or {},
            )
            for row in rows
        ]

    async def reset_skill_evaluation_results(self) -> int:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM skill_evaluation_results
                RETURNING id
                """
            )
            rows = await cursor.fetchall()
        return len(rows)

    async def list_audit_events(
        self,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            if thread_id:
                cursor = await conn.execute(
                    """
                    SELECT id, created_at, thread_id, source, category, severity,
                           reason, subject, metadata
                    FROM audit_events
                    WHERE thread_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (thread_id, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT id, created_at, thread_id, source, category, severity,
                           reason, subject, metadata
                    FROM audit_events
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = await cursor.fetchall()
        return [
            AuditEvent(
                id=row[0],
                created_at=row[1],
                thread_id=row[2],
                source=row[3],
                category=row[4],
                severity=row[5],
                reason=row[6],
                subject=row[7],
                metadata=row[8] or {},
            )
            for row in rows
        ]

    async def list_execution_logs(
        self,
        thread_id: str,
        limit: int = 500,
    ) -> list[ExecutionLog]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, created_at, thread_id, run_id, parent_id, event_type,
                       status, name, input, output, error, duration_ms,
                       token_usage, metadata
                FROM agent_execution_logs
                WHERE thread_id = %s
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (thread_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            ExecutionLog(
                id=row[0],
                created_at=row[1],
                thread_id=row[2],
                run_id=row[3],
                parent_id=row[4],
                event_type=row[5],
                status=row[6],
                name=row[7],
                input=row[8] or {},
                output=row[9] or {},
                error=row[10] or {},
                duration_ms=row[11],
                token_usage=row[12] or {},
                metadata=row[13] or {},
            )
            for row in rows
        ]

    async def execution_log_summary(self, thread_id: str) -> ExecutionSummary:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(*)::int AS total_events,
                    COUNT(*) FILTER (WHERE event_type = 'tool')::int AS tool_calls,
                    COUNT(*) FILTER (
                        WHERE status = 'failed' OR event_type = 'tool_retry'
                    )::int AS tool_errors,
                    COUNT(*) FILTER (WHERE event_type = 'tool_retry')::int AS tool_retries,
                    COUNT(*) FILTER (WHERE event_type = 'security')::int AS security_events,
                    COALESCE(SUM((token_usage->>'prompt_tokens')::int), 0)::int AS prompt_tokens,
                    COALESCE(SUM((token_usage->>'completion_tokens')::int), 0)::int AS completion_tokens,
                    COALESCE(SUM((token_usage->>'total_tokens')::int), 0)::int AS total_tokens,
                    COALESCE(SUM(duration_ms), 0)::int AS total_duration_ms
                FROM agent_execution_logs
                WHERE thread_id = %s
                """,
                (thread_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return ExecutionSummary(thread_id=thread_id)
        return ExecutionSummary(
            thread_id=thread_id,
            total_events=row[0],
            tool_calls=row[1],
            tool_errors=row[2],
            tool_retries=row[3],
            security_events=row[4],
            prompt_tokens=row[5],
            completion_tokens=row[6],
            total_tokens=row[7],
            total_duration_ms=row[8],
        )

    async def list_tool_errors(
        self,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> list[ToolError]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            if thread_id:
                cursor = await conn.execute(
                    """
                    SELECT id, created_at, thread_id, tool_call_id, tool_name,
                           tool_args, attempt, max_attempts, error_type,
                           error_message, will_retry
                    FROM tool_errors
                    WHERE thread_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (thread_id, limit),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT id, created_at, thread_id, tool_call_id, tool_name,
                           tool_args, attempt, max_attempts, error_type,
                           error_message, will_retry
                    FROM tool_errors
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = await cursor.fetchall()
        return [
            ToolError(
                id=row[0],
                created_at=row[1],
                thread_id=row[2],
                tool_call_id=row[3],
                tool_name=row[4],
                tool_args=row[5] or {},
                attempt=row[6],
                max_attempts=row[7],
                error_type=row[8],
                error_message=row[9],
                will_retry=row[10],
            )
            for row in rows
        ]

    async def _setup_audit_events(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    thread_id TEXT,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    subject TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_events_thread_created
                ON audit_events (thread_id, created_at DESC)
                """
            )

    async def _setup_execution_logs(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_execution_logs (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    thread_id TEXT NOT NULL,
                    run_id TEXT,
                    parent_id TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    name TEXT,
                    input JSONB NOT NULL DEFAULT '{}'::jsonb,
                    output JSONB NOT NULL DEFAULT '{}'::jsonb,
                    error JSONB NOT NULL DEFAULT '{}'::jsonb,
                    duration_ms INTEGER,
                    token_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_execution_logs_thread_created
                ON agent_execution_logs (thread_id, created_at ASC, id ASC)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_execution_logs_thread_type
                ON agent_execution_logs (thread_id, event_type, created_at ASC)
                """
            )

    async def _setup_long_term_memories(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    slug TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    async def _setup_tool_results(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_results (
                    tool_result_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    tool_name TEXT,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_results_thread_created
                ON tool_results (thread_id, created_at DESC)
                """
            )

    async def _setup_tool_errors(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_errors (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    thread_id TEXT,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_args JSONB NOT NULL DEFAULT '{}'::jsonb,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    will_retry BOOLEAN NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tool_errors_thread_created
                ON tool_errors (thread_id, created_at DESC)
                """
            )

    async def _setup_skill_evaluation_results(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_evaluation_results (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    skill_name TEXT NOT NULL,
                    overall_score DOUBLE PRECISION NOT NULL,
                    routing_score DOUBLE PRECISION,
                    runtime_score DOUBLE PRECISION,
                    usage_score DOUBLE PRECISION,
                    static_score DOUBLE PRECISION,
                    source TEXT,
                    report JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_skill_evaluation_results_latest
                ON skill_evaluation_results (skill_name, created_at DESC, id DESC)
                """
            )


def _serialize_checkpoint(checkpoint: Any) -> dict[str, Any]:
    payload = _jsonable(checkpoint)
    payload_mapping = payload if isinstance(payload, dict) else {}
    checkpoint_data = _checkpoint_data(checkpoint)
    values = checkpoint_data.get("channel_values", {})
    config = _configurable(
        getattr(checkpoint, "config", None) or payload_mapping.get("config")
    )
    parent_config = _configurable(
        getattr(checkpoint, "parent_config", None) or payload_mapping.get("parent_config")
    )
    metadata = getattr(checkpoint, "metadata", None) or payload_mapping.get("metadata") or {}

    return {
        "checkpoint_id": config.get("checkpoint_id") or checkpoint_data.get("id"),
        "parent_checkpoint_id": parent_config.get("checkpoint_id"),
        "created_at": checkpoint_data.get("ts"),
        "node": _node_from_metadata(metadata),
        "values": _replay_values(values),
        "messages": [
            serialized
            for message in values.get("messages", [])
            if (serialized := _serialize_message(message)) is not None
        ],
        "checkpoint": payload,
    }


def _thread_summary_from_checkpoint(checkpoint: Any) -> str | None:
    checkpoint_data = checkpoint if isinstance(checkpoint, dict) else {}
    values = checkpoint_data.get("channel_values", {})
    if not isinstance(values, dict):
        return None
    messages = values.get("messages", [])
    if not isinstance(messages, list):
        return None

    for message in messages:
        if _message_role(message) == "user":
            content = _message_content_text(_message_content(message)).strip()
            if content:
                return _clip_text(content)
    for message in messages:
        content = _message_content_text(_message_content(message)).strip()
        if content:
            return _clip_text(content)
    return None


def _message_role(message: Any) -> str | None:
    message_type = getattr(message, "type", None)
    if message_type is None and isinstance(message, dict):
        message_type = message.get("type")
        if message_type is None and isinstance(message.get("id"), list):
            type_name = str(message["id"][-1])
            if type_name.endswith("Message"):
                type_name = type_name[: -len("Message")]
            message_type = type_name.lower()
    return {
        "human": "user",
        "ai": "assistant",
        "tool": "tool_call",
    }.get(message_type)


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        if "content" in message:
            return message.get("content", "")
        kwargs = message.get("kwargs")
        if isinstance(kwargs, dict):
            return kwargs.get("content", "")
        return ""
    return getattr(message, "content", "")


def _clip_text(value: str, limit: int = 80) -> str:
    single_line = " ".join(value.split())
    return single_line if len(single_line) <= limit else f"{single_line[:limit].rstrip()}..."


def _checkpoint_data(checkpoint: Any) -> dict[str, Any]:
    data = getattr(checkpoint, "checkpoint", None)
    if data is None and isinstance(checkpoint, dict):
        data = checkpoint.get("checkpoint", checkpoint)
    return data if isinstance(data, dict) else {}


def _configurable(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable", {})
    return configurable if isinstance(configurable, dict) else {}


def _node_from_metadata(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    writes = metadata.get("writes")
    if isinstance(writes, dict) and writes:
        return next(iter(writes.keys()))
    source = metadata.get("source")
    return source if isinstance(source, str) else None


def _replay_values(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    return {
        "selected_skills": _jsonable(values.get("selected_skills", [])),
        "pending_approvals": _jsonable(values.get("pending_approvals", [])),
    }


def _serialize_message(message: Any) -> dict[str, Any] | None:
    role = _message_role(message)
    if role not in {"user", "assistant", "tool_call"}:
        return None
    content = _message_content_text(_message_content(message))
    return {"role": role, "content": content}


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                return content
            return _json_text(parsed)
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
            else:
                text_parts.append(_json_text(item))
        return "\n".join(part for part in text_parts if part)
    return _json_text(content)


def _json_text(value: Any) -> str:
    encoded = jsonable_encoder(value)
    if isinstance(encoded, str):
        return encoded
    return json.dumps(encoded, ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    return jsonable_encoder(value)


def _normalized_score(value: Any) -> float:
    number = float(value)
    return number / 100 if number > 1 else number


def _normalized_optional_score(value: Any) -> float | None:
    if value is None:
        return None
    return _normalized_score(value)
