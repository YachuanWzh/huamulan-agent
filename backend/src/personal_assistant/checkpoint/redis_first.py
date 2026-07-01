import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple

from personal_assistant.checkpoint.serde import CompressedJsonPlusSerializer

logger = logging.getLogger(__name__)


class RedisFirstCheckpointSaver(BaseCheckpointSaver):
    def __init__(
        self,
        postgres_saver: Any,
        redis_client: Any,
        *,
        ttl_seconds: int,
        skip_nodes: set[str] | list[str] | tuple[str, ...] | None = None,
        serde: Any | None = None,
    ):
        serde = serde or CompressedJsonPlusSerializer()
        super().__init__(serde=serde)
        self.postgres_saver = postgres_saver
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self.skip_nodes = set(skip_nodes or [])
        self._pending_tasks: set[asyncio.Task] = set()

    @property
    def config_specs(self) -> list:
        return getattr(self.postgres_saver, "config_specs", [])

    def get_next_version(self, current, channel):
        return self.postgres_saver.get_next_version(current, channel)

    async def setup(self) -> None:
        setup = getattr(self.postgres_saver, "setup", None)
        if callable(setup):
            await setup()

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        context = _checkpoint_context_from_metadata(metadata)
        result_config = _config_with_checkpoint_id(config, checkpoint.get("id"))
        redis_config = _redis_checkpoint_config(result_config)
        redis_parent_config = _redis_checkpoint_config(config)
        if context["write_node"] in self.skip_nodes:
            return result_config

        envelope = {
            "config": redis_config,
            "checkpoint": checkpoint,
            "metadata": metadata,
            "new_versions": new_versions,
            "parent_config": redis_parent_config,
            **context,
        }
        thread_id = _thread_id(result_config)
        checkpoint_id = _checkpoint_id(result_config)
        if not thread_id or not checkpoint_id:
            return await self.postgres_saver.aput(config, checkpoint, metadata, new_versions)

        try:
            redis_envelope = _redis_safe_value(envelope, self.serde)
            await self._write_checkpoint_envelope(thread_id, checkpoint_id, checkpoint, redis_envelope)
        except Exception:
            logger.exception("Redis checkpoint write failed; falling back to PostgreSQL")
            return await self.postgres_saver.aput(config, checkpoint, metadata, new_versions)
        logger.info(
            "Redis checkpoint write completed",
            extra={
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                **context,
                "ttl_seconds": self.ttl_seconds,
            },
        )

        self._schedule_archive(
            self.postgres_saver.aput(config, checkpoint, metadata, new_versions),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            **context,
        )
        return result_config

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes,
        task_id: str,
        task_path: str = "",
    ) -> None:
        try:
            thread_id = _thread_id(config)
            checkpoint_id = _checkpoint_id(config) or task_id
            if thread_id and checkpoint_id:
                redis_payload = _redis_safe_value(
                    {
                        "config": _redis_checkpoint_config(config),
                        "writes": list(writes),
                        "task_id": task_id,
                        "task_path": task_path,
                    },
                    self.serde,
                )
                payload = self.serde.dumps_typed(redis_payload)
                await self.redis.set(
                    _write_key(thread_id, checkpoint_id, task_id),
                    payload[1],
                    ex=self.ttl_seconds,
                )
                logger.info(
                    "Redis checkpoint writes completed",
                    extra={
                        "thread_id": thread_id,
                        "checkpoint_id": checkpoint_id,
                        "task_id": task_id,
                        "ttl_seconds": self.ttl_seconds,
                    },
                )
        except Exception:
            logger.exception("Redis checkpoint writes failed; falling back to PostgreSQL")
            await self.postgres_saver.aput_writes(config, writes, task_id, task_path)
            return

        self._schedule_archive(
            self.postgres_saver.aput_writes(config, writes, task_id, task_path),
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            source=None,
            write_node="checkpoint_writes",
        )

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        thread_id = _thread_id(config)
        checkpoint_id = _checkpoint_id(config)
        if thread_id:
            if not checkpoint_id:
                checkpoint_id = await self._latest_checkpoint_id(thread_id)
            if checkpoint_id:
                checkpoint = await self._read_checkpoint(thread_id, checkpoint_id)
                if checkpoint is not None:
                    return checkpoint
        return await self.postgres_saver.aget_tuple(config)

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        thread_id = _thread_id(config or {})
        if thread_id:
            members = await self.redis.zrevrange(_thread_key(thread_id), 0, limit - 1 if limit else -1)
            emitted = False
            for member in members:
                checkpoint_id = member.decode() if isinstance(member, bytes) else str(member)
                checkpoint = await self._read_checkpoint(thread_id, checkpoint_id)
                if checkpoint is not None:
                    emitted = True
                    yield checkpoint
            if emitted:
                return

        async for item in self.postgres_saver.alist(
            config,
            filter=filter,
            before=before,
            limit=limit,
        ):
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        keys = []
        async for key in self.redis.scan_iter(match=f"pa:v1:checkpoint:{thread_id}:*"):
            keys.append(key)
        async for key in self.redis.scan_iter(match=f"pa:v1:checkpoint_writes:{thread_id}:*"):
            keys.append(key)
        keys.append(_thread_key(thread_id))
        await self.redis.delete(*keys)
        await self.postgres_saver.adelete_thread(thread_id)

    async def drain(self, timeout: float = 5.0) -> None:
        if not self._pending_tasks:
            return
        done, pending = await asyncio.wait(self._pending_tasks, timeout=timeout)
        for task in done:
            try:
                task.result()
            except Exception:
                logger.exception("Async PostgreSQL checkpoint archive failed")
        if pending:
            logger.warning("Timed out waiting for %s checkpoint archive tasks", len(pending))

    async def _write_checkpoint_envelope(
        self,
        thread_id: str,
        checkpoint_id: str,
        checkpoint: dict[str, Any],
        envelope: dict[str, Any],
    ) -> None:
        marker, payload = self.serde.dumps_typed(envelope)
        await self.redis.set(_checkpoint_key(thread_id, checkpoint_id), marker.encode() + b"\0" + payload, ex=self.ttl_seconds)
        await self.redis.zadd(
            _thread_key(thread_id),
            {checkpoint_id: _checkpoint_score(checkpoint)},
        )

    async def _read_checkpoint(self, thread_id: str, checkpoint_id: str) -> CheckpointTuple | None:
        raw = await self.redis.get(_checkpoint_key(thread_id, checkpoint_id))
        if raw is None:
            return None
        try:
            marker, payload = raw.split(b"\0", 1)
            envelope = self.serde.loads_typed((marker.decode(), payload))
            return CheckpointTuple(
                config=envelope["config"],
                checkpoint=envelope["checkpoint"],
                metadata=envelope.get("metadata", {}),
                parent_config=envelope.get("parent_config"),
            )
        except Exception:
            logger.exception("Failed to decode Redis checkpoint")
            await self.redis.delete(_checkpoint_key(thread_id, checkpoint_id))
            return None

    async def _latest_checkpoint_id(self, thread_id: str) -> str | None:
        members = await self.redis.zrevrange(_thread_key(thread_id), 0, 0)
        if not members:
            return None
        member = members[0]
        return member.decode() if isinstance(member, bytes) else str(member)

    def _schedule_archive(
        self,
        coro,
        *,
        thread_id: str | None,
        checkpoint_id: str | None,
        source: str | None,
        write_node: str | None,
    ) -> None:
        logger.info(
            "Async PostgreSQL checkpoint archive scheduled",
            extra={
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "source": source,
                "write_node": write_node,
            },
        )
        task = asyncio.create_task(coro)
        task._checkpoint_log_context = {  # type: ignore[attr-defined]
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "source": source,
            "write_node": write_node,
        }
        self._pending_tasks.add(task)
        task.add_done_callback(self._archive_done)

    def _archive_done(self, task: asyncio.Task) -> None:
        self._pending_tasks.discard(task)
        context = getattr(task, "_checkpoint_log_context", {})
        try:
            task.result()
        except Exception:
            logger.exception(
                "Async PostgreSQL checkpoint archive failed",
                extra=context,
            )
            return
        logger.info(
            "Async PostgreSQL checkpoint archive completed",
            extra=context,
        )


def _config_with_checkpoint_id(config: dict[str, Any], checkpoint_id: Any) -> dict[str, Any]:
    configurable = dict(config.get("configurable", {}))
    if checkpoint_id:
        configurable["checkpoint_id"] = str(checkpoint_id)
    return {**config, "configurable": configurable}


def _redis_checkpoint_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return {}
    return {"configurable": dict(configurable)}


def _redis_safe_value(value: Any, serde: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    if isinstance(value, dict):
        return {
            _redis_safe_key(key, serde): _redis_safe_value(item, serde)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redis_safe_value(item, serde) for item in value]
    if isinstance(value, tuple):
        return tuple(_redis_safe_value(item, serde) for item in value)
    if isinstance(value, (set, frozenset)):
        return [_redis_safe_value(item, serde) for item in value]

    try:
        serde.dumps_typed(value)
    except Exception:
        return repr(value)
    return value


def _redis_safe_key(key: Any, serde: Any) -> str | int | float | bool | bytes | None:
    safe_key = _redis_safe_value(key, serde)
    if safe_key is None or isinstance(safe_key, (str, int, float, bool, bytes)):
        return safe_key
    return repr(safe_key)


def _checkpoint_context_from_metadata(metadata: dict[str, Any] | None) -> dict[str, str | None]:
    context = {"source": None, "write_node": None}
    if not isinstance(metadata, dict):
        return context
    source = metadata.get("source")
    if isinstance(source, str):
        context["source"] = source
    writes = metadata.get("writes")
    if isinstance(writes, dict) and writes:
        context["write_node"] = str(next(iter(writes)))
    return context


def _thread_id(config: dict[str, Any]) -> str | None:
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return str(thread_id) if thread_id else None


def _checkpoint_id(config: dict[str, Any]) -> str | None:
    configurable = config.get("configurable", {})
    checkpoint_id = configurable.get("checkpoint_id") if isinstance(configurable, dict) else None
    return str(checkpoint_id) if checkpoint_id else None


def _checkpoint_score(checkpoint: dict[str, Any]) -> float:
    ts = checkpoint.get("ts")
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _checkpoint_key(thread_id: str, checkpoint_id: str) -> str:
    return f"pa:v1:checkpoint:{thread_id}:{checkpoint_id}"


def _write_key(thread_id: str, checkpoint_id: str, task_id: str) -> str:
    return f"pa:v1:checkpoint_writes:{thread_id}:{checkpoint_id}:{task_id}"


def _thread_key(thread_id: str) -> str:
    return f"pa:v1:checkpoint_thread:{thread_id}"
