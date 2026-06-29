from typing import Any

from fastapi.encoders import jsonable_encoder
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


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

    async def delete_thread(self, thread_id: str) -> None:
        if self.checkpointer is None:
            raise RuntimeError("Postgres memory is not started")
        await self.checkpointer.adelete_thread(thread_id)


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
        "messages": [_serialize_message(message) for message in values.get("messages", [])],
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


def _serialize_message(message: Any) -> dict[str, Any]:
    message_type = getattr(message, "type", None)
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool_call",
    }.get(message_type, message_type or "assistant")
    content = getattr(message, "content", "")
    if not isinstance(content, str):
        content = jsonable_encoder(content)
    return {"role": role, "content": content}


def _jsonable(value: Any) -> Any:
    return jsonable_encoder(value)
