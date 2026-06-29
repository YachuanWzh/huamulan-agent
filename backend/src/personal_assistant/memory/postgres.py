import ast
import json
from typing import Any

from fastapi.encoders import jsonable_encoder
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from personal_assistant.api.schemas import AuditEvent, AuditEventCreate


class PostgresMemory:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None

    async def start(self) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=self.database_url,
            open=False,
            kwargs={"autocommit": True},
        )
        await self.pool.open()
        self.checkpointer = AsyncPostgresSaver(self.pool)
        await self.checkpointer.setup()
        await self._setup_audit_events()
        await self._setup_long_term_memories()
        await self._setup_tool_results()

    async def stop(self) -> None:
        if self.pool is not None:
            await self.pool.close()

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
                SELECT thread_id, MAX((checkpoint->>'ts')::timestamptz) AS updated_at
                FROM checkpoints
                GROUP BY thread_id
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
    message_type = getattr(message, "type", None)
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool_call",
    }.get(message_type)
    if role not in {"user", "assistant", "tool_call"}:
        return None
    content = _message_content_text(getattr(message, "content", ""))
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
