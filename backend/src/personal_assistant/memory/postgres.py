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
from personal_assistant.skills.evaluation.ops import EvaluationCaseResult, EvaluationRun
from personal_assistant.skills.evaluation.sbs import SBSCandidate, SBSReview, SBSTask

logger = logging.getLogger(__name__)

_EVALUATION_RUN_SELECT = """
SELECT run_id, created_at, updated_at, mode, agent_mode, status, source,
       dataset_path, dataset_hash, git_sha, config_snapshot, total_cases,
       completed_cases, failed_cases, report
FROM evaluation_runs
"""


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
        await self._setup_evaluation_runs()
        await self._setup_sbs()
        await self._setup_otel_alerts()

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
                        checkpoint_ns,
                        (checkpoint->>'ts')::timestamptz AS updated_at,
                        checkpoint
                    FROM checkpoints
                    WHERE thread_id NOT LIKE 'skill-eval-%%'
                    ORDER BY thread_id, (checkpoint->>'ts')::timestamptz DESC NULLS LAST
                )
                SELECT
                    l.thread_id,
                    l.updated_at,
                    l.checkpoint,
                    bl.type AS messages_type,
                    bl.blob AS messages_blob
                FROM latest l
                LEFT JOIN checkpoint_blobs bl
                    ON bl.thread_id = l.thread_id
                    AND bl.checkpoint_ns = l.checkpoint_ns
                    AND bl.channel = 'messages'
                    AND bl.version = (l.checkpoint->'channel_versions'->>'messages')
                ORDER BY l.updated_at DESC NULLS LAST, l.thread_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        results = []
        for row in rows:
            summary = None
            messages_type = row[3]
            messages_blob = row[4]
            if messages_blob is not None and messages_type is not None and messages_type != "empty":
                summary = _thread_summary_from_blob(
                    self.checkpointer, messages_type, messages_blob
                )
            if summary is None:
                summary = _thread_summary_from_checkpoint(row[2])
            results.append({
                "thread_id": row[0],
                "updated_at": row[1],
                "summary": summary,
            })
        return results

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

    async def create_evaluation_run(self, run: EvaluationRun) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO evaluation_runs
                    (run_id, mode, agent_mode, status, source, dataset_path,
                     dataset_hash, git_sha, config_snapshot, total_cases,
                     completed_cases, failed_cases, report)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                        %s, %s, %s, %s::jsonb)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    run.run_id, run.mode, run.agent_mode, run.status, run.source,
                    run.dataset_path, run.dataset_hash, run.git_sha,
                    Jsonb(_jsonable(run.config_snapshot)), run.total_cases,
                    run.completed_cases, run.failed_cases,
                    Jsonb(_jsonable(run.report)),
                ),
            )

    async def record_evaluation_case_result(self, result: EvaluationCaseResult) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO evaluation_case_results
                    (run_id, case_id, status, passed, safety_passed,
                     forbidden_tools, latency_ms, total_tokens, trace_id,
                     thread_id, detail)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (run_id, case_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    passed = EXCLUDED.passed,
                    safety_passed = EXCLUDED.safety_passed,
                    forbidden_tools = EXCLUDED.forbidden_tools,
                    latency_ms = EXCLUDED.latency_ms,
                    total_tokens = EXCLUDED.total_tokens,
                    trace_id = EXCLUDED.trace_id,
                    thread_id = EXCLUDED.thread_id,
                    detail = EXCLUDED.detail,
                    updated_at = now()
                """,
                (
                    result.run_id, result.case_id, result.status, result.passed,
                    result.safety_passed, Jsonb(result.forbidden_tools),
                    result.latency_ms, result.total_tokens, result.trace_id,
                    result.thread_id, Jsonb(_jsonable(result.detail)),
                ),
            )

    async def complete_evaluation_run(
        self,
        run_id: str,
        *,
        status: str,
        completed_cases: int,
        failed_cases: int,
        report: dict[str, Any],
    ) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                UPDATE evaluation_runs
                SET status = %s, completed_cases = %s, failed_cases = %s,
                    report = %s::jsonb, updated_at = now()
                WHERE run_id = %s
                """,
                (status, completed_cases, failed_cases, Jsonb(_jsonable(report)), run_id),
            )

    async def list_evaluation_runs(self, limit: int = 100) -> list[EvaluationRun]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 500))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"{_EVALUATION_RUN_SELECT} ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_evaluation_run_from_row(row) for row in rows]

    async def get_evaluation_run(self, run_id: str) -> EvaluationRun | None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"{_EVALUATION_RUN_SELECT} WHERE run_id = %s",
                (run_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await conn.execute(
                """
                SELECT run_id, case_id, status, passed, safety_passed,
                       forbidden_tools, latency_ms, total_tokens, trace_id,
                       thread_id, detail
                FROM evaluation_case_results
                WHERE run_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (run_id,),
            )
            case_rows = await cursor.fetchall()
        run = _evaluation_run_from_row(row)
        run.case_results = [_evaluation_case_from_row(item) for item in case_rows]
        return run

    async def create_sbs_task(self, task: SBSTask) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO sbs_tasks
                    (task_id, prompt, candidate_a, candidate_b, status, provenance)
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (task_id) DO NOTHING
                """,
                (
                    task.task_id,
                    task.prompt,
                    Jsonb(task.candidate_a.model_dump(mode="json")),
                    Jsonb(task.candidate_b.model_dump(mode="json")),
                    task.status,
                    Jsonb(_jsonable(task.provenance)),
                ),
            )

    async def list_sbs_tasks(self, limit: int = 100) -> list[SBSTask]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 1000))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT task_id, prompt, candidate_a, candidate_b, status, provenance
                FROM sbs_tasks
                ORDER BY created_at ASC, task_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_sbs_task_from_row(row) for row in rows]

    async def get_sbs_task(self, task_id: str) -> SBSTask | None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT task_id, prompt, candidate_a, candidate_b, status, provenance
                FROM sbs_tasks WHERE task_id = %s
                """,
                (task_id,),
            )
            row = await cursor.fetchone()
        return _sbs_task_from_row(row) if row is not None else None

    async def delete_sbs_task(self, task_id: str) -> bool:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM sbs_tasks WHERE task_id = %s",
                (task_id,),
            )
        return cursor.rowcount > 0

    async def record_sbs_review(self, review: SBSReview) -> SBSReview:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT COALESCE(MAX(revision), 0) + 1
                FROM sbs_reviews WHERE task_id = %s AND reviewer = %s
                """,
                (review.task_id, review.reviewer),
            )
            row = await cursor.fetchone()
            revision = int(row[0]) if row else 1
            saved = review.model_copy(update={"revision": revision})
            await conn.execute(
                """
                INSERT INTO sbs_reviews
                    (task_id, reviewer, revision, display_winner,
                     canonical_winner, reason, dimension_scores)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    saved.task_id, saved.reviewer, revision, saved.winner,
                    saved.canonical_winner or saved.winner, saved.reason,
                    Jsonb(saved.dimension_scores),
                ),
            )
            await conn.execute(
                "UPDATE sbs_tasks SET status = 'reviewed', updated_at = now() WHERE task_id = %s",
                (saved.task_id,),
            )
        return saved

    async def get_latest_sbs_review(self, task_id: str) -> SBSReview | None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT task_id, reviewer, revision, display_winner,
                       canonical_winner, reason, dimension_scores
                FROM sbs_reviews
                WHERE task_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (task_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SBSReview(
            task_id=row[0],
            reviewer=row[1],
            revision=int(row[2]),
            winner=row[3],
            canonical_winner=row[4],
            reason=row[5] or "",
            dimension_scores=dict(row[6] or {}),
        )

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

    async def list_trace_logs(
        self,
        trace_id: str,
        limit: int = 2000,
    ) -> list[ExecutionLog]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 2000))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, created_at, thread_id, run_id, parent_id, event_type,
                       status, name, input, output, error, duration_ms,
                       token_usage, metadata
                FROM agent_execution_logs
                WHERE metadata->>'trace_id' = %s
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (trace_id, limit),
            )
            rows = await cursor.fetchall()
        return [_execution_log_from_row(row) for row in rows]

    async def list_thread_trace_ids(
        self,
        thread_id: str,
        limit: int = 200,
    ) -> list[str]:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        limit = max(1, min(limit, 200))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT trace_id
                FROM (
                    SELECT DISTINCT ON (metadata->>'trace_id')
                           metadata->>'trace_id' AS trace_id, created_at, id
                    FROM agent_execution_logs
                    WHERE thread_id = %s
                      AND metadata->>'trace_id' IS NOT NULL
                    ORDER BY metadata->>'trace_id', created_at DESC, id DESC
                ) recent
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (thread_id, limit),
            )
            rows = await cursor.fetchall()
        return [str(row[0]) for row in rows]

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

    # ── Harness Health query methods ───────────────────────────────────

    async def harness_approval_denial_rates(self, days: int = 30) -> list[dict[str, Any]]:
        """Per-tool approval denial rate aggregated over *days*."""
        if self.pool is None:
            return []
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    name AS tool_name,
                    COUNT(*)::int AS total_requests,
                    COUNT(*) FILTER (WHERE status = 'denied')::int AS denied_count,
                    COUNT(*) FILTER (WHERE status = 'approved')::int AS approved_count,
                    ROUND(
                        COUNT(*) FILTER (WHERE status = 'denied') * 100.0
                        / GREATEST(COUNT(*), 1),
                        2
                    ) AS denial_rate_pct
                FROM agent_execution_logs
                WHERE event_type = 'approval'
                  AND created_at > now() - (%s || ' days')::interval
                GROUP BY name
                ORDER BY denial_rate_pct DESC
                """,
                (str(days),),
            )
            rows = await cursor.fetchall()
        return [
            {
                "tool_name": row[0],
                "total_requests": row[1],
                "denied_count": row[2],
                "approved_count": row[3],
                "denial_rate_pct": float(row[4]),
            }
            for row in rows
        ]

    async def harness_compaction_trends(self, days: int = 7) -> list[dict[str, Any]]:
        """Daily compaction efficiency trend over *days*."""
        if self.pool is None:
            return []
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    date_trunc('day', created_at)::date AS day,
                    COUNT(*)::int AS compaction_count,
                    COALESCE(AVG((metadata->>'before_tokens')::int), 0)::int AS avg_before_tokens,
                    COALESCE(AVG((metadata->>'after_tokens')::int), 0)::int AS avg_after_tokens,
                    ROUND(COALESCE(AVG((metadata->>'saved_ratio')::float), 0) * 100, 2) AS avg_saved_pct,
                    COALESCE(AVG(duration_ms), 0)::float AS avg_duration_ms
                FROM agent_execution_logs
                WHERE event_type = 'harness'
                  AND name = 'compaction'
                  AND created_at > now() - (%s || ' days')::interval
                GROUP BY date_trunc('day', created_at)
                ORDER BY day DESC
                """,
                (str(days),),
            )
            rows = await cursor.fetchall()
        return [
            {
                "day": str(row[0]),
                "compaction_count": row[1],
                "avg_before_tokens": row[2],
                "avg_after_tokens": row[3],
                "avg_saved_pct": float(row[4]),
                "avg_duration_ms": float(row[5]),
            }
            for row in rows
        ]

    async def harness_latency_breakdown(self, thread_id: str) -> list[dict[str, Any]]:
        """Per-layer latency breakdown for the latest turn in *thread_id*."""
        if self.pool is None:
            return []
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT name, status, duration_ms, metadata
                FROM agent_execution_logs
                WHERE thread_id = %s
                  AND event_type = 'harness'
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (thread_id,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "name": row[0],
                "status": row[1],
                "duration_ms": row[2],
                "metadata": row[3] if isinstance(row[3], dict) else {},
            }
            for row in rows
        ]

    async def harness_tool_guard_intercept_rate(self, hours: int = 1) -> dict[str, Any]:
        """Tool Guard intercept rate vs 7-day P95 baseline."""
        if self.pool is None:
            return {"current_count": 0, "p95_baseline": 0, "anomaly": False}
        async with self.pool.connection() as conn:
            # Current rate
            cursor = await conn.execute(
                """
                SELECT COUNT(*)::int
                FROM audit_events
                WHERE source = 'tool'
                  AND created_at > now() - (%s || ' hours')::interval
                """,
                (str(hours),),
            )
            row = await cursor.fetchone()
            current_count = row[0] if row else 0

            # 7-day P95 hourly baseline
            cursor = await conn.execute(
                """
                SELECT COALESCE(
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cnt),
                    0
                )
                FROM (
                    SELECT date_trunc('hour', created_at) AS h,
                           COUNT(*) AS cnt
                    FROM audit_events
                    WHERE source = 'tool'
                      AND created_at > now() - interval '7 days'
                    GROUP BY date_trunc('hour', created_at)
                ) sub
                """
            )
            row = await cursor.fetchone()
            p95_baseline = float(row[0]) if row else 0.0

        return {
            "current_count": current_count,
            "p95_baseline": p95_baseline,
            "anomaly": current_count > p95_baseline,
        }

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


    async def _setup_evaluation_runs(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    mode TEXT NOT NULL,
                    agent_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT,
                    dataset_path TEXT NOT NULL,
                    dataset_hash TEXT NOT NULL,
                    git_sha TEXT,
                    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                    total_cases INTEGER NOT NULL DEFAULT 0,
                    completed_cases INTEGER NOT NULL DEFAULT 0,
                    failed_cases INTEGER NOT NULL DEFAULT 0,
                    report JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_case_results (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    run_id TEXT NOT NULL REFERENCES evaluation_runs(run_id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    passed BOOLEAN NOT NULL,
                    safety_passed BOOLEAN,
                    forbidden_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
                    latency_ms INTEGER,
                    total_tokens INTEGER,
                    trace_id TEXT,
                    thread_id TEXT,
                    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                    UNIQUE (run_id, case_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evaluation_runs_created
                ON evaluation_runs (created_at DESC)
                """
            )

    async def _setup_sbs(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sbs_tasks (
                    task_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    prompt TEXT NOT NULL,
                    candidate_a JSONB NOT NULL,
                    candidate_b JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    provenance JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sbs_reviews (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    task_id TEXT NOT NULL REFERENCES sbs_tasks(task_id) ON DELETE CASCADE,
                    reviewer TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    display_winner TEXT NOT NULL,
                    canonical_winner TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    dimension_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
                    UNIQUE (task_id, reviewer, revision)
                )
                """
            )


    async def _setup_otel_alerts(self) -> None:
        if self.pool is None:
            raise RuntimeError("Postgres memory is not started")
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS otel_alerts (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    received_at TIMESTAMPTZ NOT NULL,
                    severity TEXT NOT NULL,
                    level TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    alert_name TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    starts_at TEXT,
                    status TEXT NOT NULL DEFAULT 'firing',
                    rca_status TEXT NOT NULL DEFAULT 'pending',
                    rca_thread_id TEXT,
                    rca_pending_approvals JSONB,
                    rca_result_text TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_otel_alerts_received_at
                ON otel_alerts (received_at DESC)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_otel_alerts_level
                ON otel_alerts (level, received_at DESC)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_otel_alerts_service
                ON otel_alerts (service_name, received_at DESC)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_otel_alerts_rca_thread
                ON otel_alerts (rca_thread_id)
                """
            )

    async def upsert_otel_alert(self, alert_data: dict) -> None:
        """Insert or update an OTEL alert in PostgreSQL."""
        if self.pool is None:
            return
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO otel_alerts (
                    id, created_at, updated_at, received_at,
                    severity, level, service_name, alert_name,
                    summary, description, starts_at, status,
                    rca_status, rca_thread_id, rca_pending_approvals,
                    rca_result_text, metadata
                ) VALUES (
                    %s, now(), now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    updated_at = now(),
                    status = EXCLUDED.status,
                    rca_status = EXCLUDED.rca_status,
                    rca_thread_id = EXCLUDED.rca_thread_id,
                    rca_pending_approvals = EXCLUDED.rca_pending_approvals,
                    rca_result_text = EXCLUDED.rca_result_text,
                    metadata = EXCLUDED.metadata
                """,
                (
                    alert_data["id"],
                    alert_data["received_at"],
                    alert_data["severity"],
                    alert_data["level"],
                    alert_data["service_name"],
                    alert_data["alert_name"],
                    alert_data.get("summary", ""),
                    alert_data.get("description", ""),
                    alert_data.get("starts_at"),
                    alert_data.get("status", "firing"),
                    alert_data.get("rca_status", "pending"),
                    alert_data.get("rca_thread_id"),
                    json.dumps(alert_data.get("rca_pending_approvals") or []),
                    alert_data.get("rca_result_text"),
                    json.dumps(alert_data.get("metadata", {})),
                ),
            )

    async def list_otel_alerts(self, limit: int = 50) -> list[dict]:
        """List recent OTEL alerts from PostgreSQL."""
        if self.pool is None:
            return []
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, created_at, updated_at, received_at,
                       severity, level, service_name, alert_name,
                       summary, description, starts_at, status,
                       rca_status, rca_thread_id, rca_pending_approvals,
                       rca_result_text, metadata
                FROM otel_alerts
                ORDER BY received_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [_alert_row_to_dict(row) for row in rows]

    async def get_otel_alert(self, alert_id: str) -> dict | None:
        """Get a single OTEL alert by ID from PostgreSQL."""
        if self.pool is None:
            return None
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM otel_alerts WHERE id = %s", (alert_id,)
            )
            row = await cursor.fetchone()
            return _alert_row_to_dict(row) if row else None


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
    """Extract summary from inline channel_values (fallback for edge cases)."""
    checkpoint_data = checkpoint if isinstance(checkpoint, dict) else {}
    values = checkpoint_data.get("channel_values", {})
    if not isinstance(values, dict):
        return None
    messages = values.get("messages", [])
    if not isinstance(messages, list):
        return None
    return _extract_summary_from_messages(messages)


def _thread_summary_from_blob(
    checkpointer: Any, messages_type: str, messages_blob: bytes
) -> str | None:
    """Extract thread summary from serialized messages stored in checkpoint_blobs."""
    try:
        serde = getattr(checkpointer, "serde", None)
        if serde is None:
            return None
        messages = serde.loads_typed((messages_type, messages_blob))
        if isinstance(messages, list):
            return _extract_summary_from_messages(messages)
    except Exception:
        pass
    return None


def _extract_summary_from_messages(messages: list) -> str | None:
    """Extract a human-readable summary from a list of messages."""
    if not isinstance(messages, list):
        return None
    # Prefer the first user message
    for message in messages:
        if _message_role(message) == "user":
            content = _message_content_text(_message_content(message)).strip()
            if content:
                return _clip_text(content)
    # Fallback to any message with content
    for message in messages:
        content = _message_content_text(_message_content(message)).strip()
        if content:
            return _clip_text(content)
    return None


def _message_role(message: Any) -> str | None:
    message_type = getattr(message, "type", None)
    if message_type is None and isinstance(message, dict):
        message_type = message.get("type")

    # Handle LangChain serialized "constructor" format
    # e.g. {"lc": 1, "type": "constructor", "id": [..., "HumanMessage"],
    #       "kwargs": {"type": "human", "content": "..."}}
    if message_type == "constructor" and isinstance(message, dict):
        kwargs = message.get("kwargs", {})
        if isinstance(kwargs, dict):
            kwargs_type = kwargs.get("type")
            if kwargs_type in ("human", "ai", "tool"):
                message_type = kwargs_type
        if message_type == "constructor":
            msg_id = message.get("id")
            if isinstance(msg_id, list) and msg_id:
                type_name = str(msg_id[-1])
                if type_name.endswith("Message"):
                    type_name = type_name[: -len("Message")]
                message_type = type_name.lower()

    # Handle messages with only id (no type field)
    if message_type is None and isinstance(message, dict):
        msg_id = message.get("id")
        if isinstance(msg_id, list) and msg_id:
            type_name = str(msg_id[-1])
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


def _alert_row_to_dict(row: Any) -> dict:
    """Convert an asyncpg row from otel_alerts to a plain dict.

    Handles JSONB columns that may be returned as strings or parsed objects
    depending on the asyncpg configuration.
    """
    import json as _json

    def _parse_jsonb(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return _json.loads(value)
            except (_json.JSONDecodeError, TypeError):
                return value
        return value

    return {
        "id": row["id"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "received_at": row["received_at"].isoformat() if row["received_at"] else "",
        "severity": row["severity"],
        "level": row["level"],
        "service_name": row["service_name"],
        "alert_name": row["alert_name"],
        "summary": row["summary"] or "",
        "description": row["description"] or "",
        "starts_at": row["starts_at"],
        "status": row["status"] or "firing",
        "rca_status": row["rca_status"] or "pending",
        "rca_thread_id": row["rca_thread_id"],
        "rca_pending_approvals": _parse_jsonb(row["rca_pending_approvals"]),
        "rca_result_text": row["rca_result_text"],
        "metadata": _parse_jsonb(row["metadata"]) or {},
    }


def _jsonable(value: Any) -> Any:
    return jsonable_encoder(value, custom_encoder=_SEND_CUSTOM_ENCODER)


def _execution_log_from_row(row: Any) -> ExecutionLog:
    return ExecutionLog(
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


def _evaluation_run_from_row(row: Any) -> EvaluationRun:
    return EvaluationRun(
        run_id=row[0],
        created_at=row[1],
        updated_at=row[2],
        mode=row[3],
        agent_mode=row[4],
        status=row[5],
        source=row[6],
        dataset_path=row[7],
        dataset_hash=row[8],
        git_sha=row[9],
        config_snapshot=row[10] or {},
        total_cases=row[11],
        completed_cases=row[12],
        failed_cases=row[13],
        report=row[14] or {},
    )


def _evaluation_case_from_row(row: Any) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        run_id=row[0],
        case_id=row[1],
        status=row[2],
        passed=row[3],
        safety_passed=row[4],
        forbidden_tools=row[5] or [],
        latency_ms=row[6],
        total_tokens=row[7],
        trace_id=row[8],
        thread_id=row[9],
        detail=row[10] or {},
    )


def _sbs_task_from_row(row: Any) -> SBSTask:
    return SBSTask(
        task_id=row[0],
        prompt=row[1],
        candidate_a=SBSCandidate.model_validate(row[2]),
        candidate_b=SBSCandidate.model_validate(row[3]),
        status=row[4],
        provenance=row[5] or {},
    )


# Lazily built custom encoder for LangGraph Send objects
_SEND_CUSTOM_ENCODER: dict[type, Any] = {}
try:
    from langgraph.types import Send as _SendType

    _SEND_CUSTOM_ENCODER[_SendType] = lambda obj: {"node": obj.node, "arg": obj.arg}
except ImportError:
    pass


def _normalized_score(value: Any) -> float:
    number = float(value)
    return number / 100 if number > 1 else number


def _normalized_optional_score(value: Any) -> float | None:
    if value is None:
        return None
    return _normalized_score(value)
