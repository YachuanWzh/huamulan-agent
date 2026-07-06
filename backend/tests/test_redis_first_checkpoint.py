import asyncio

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.base import CheckpointTuple

from personal_assistant.checkpoint.redis_first import RedisFirstCheckpointSaver
from personal_assistant.memory.postgres import PostgresMemory


class FakeRedis:
    def __init__(self, *, fail_set: bool = False):
        self.fail_set = fail_set
        self.values: dict[str, bytes] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.sets: dict[str, set[bytes]] = {}
        self.ops: list[tuple] = []

    async def set(self, key, value, ex=None):
        self.ops.append(("set", key, ex))
        if self.fail_set:
            raise RuntimeError("redis down")
        self.values[key] = value

    async def get(self, key):
        self.ops.append(("get", key))
        return self.values.get(key)

    async def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        self.zsets.setdefault(key, {}).update(mapping)

    async def zrevrange(self, key, start, end):
        self.ops.append(("zrevrange", key, start, end))
        items = sorted(
            self.zsets.get(key, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if end == -1:
            return [member for member, _score in items[start:]]
        return [member for member, _score in items[start : end + 1]]

    async def sadd(self, key, value):
        self.ops.append(("sadd", key, value))
        self.sets.setdefault(key, set()).add(
            value.encode() if isinstance(value, str) else value
        )

    async def smembers(self, key):
        self.ops.append(("smembers", key))
        return self.sets.get(key, set())

    async def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))

    async def delete(self, *keys):
        self.ops.append(("delete", keys))
        for key in keys:
            self.values.pop(key, None)
            self.zsets.pop(key, None)
            self.sets.pop(key, None)

    async def scan_iter(self, match):
        prefix = match.rstrip("*")
        for key in [*self.values, *self.zsets]:
            if key.startswith(prefix):
                yield key


class FakePostgresSaver:
    def __init__(self):
        self.aputs = []
        self.writes = []
        self.deleted_threads = []
        self.tuples: list[CheckpointTuple] = []
        self.config_specs = ["postgres-config-spec"]

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.aputs.append((config, checkpoint, metadata, new_versions))
        return {
            "configurable": {
                **config.get("configurable", {}),
                "checkpoint_id": checkpoint.get("id"),
            }
        }

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self.writes.append((config, writes, task_id, task_path))

    async def aget_tuple(self, config):
        return self.tuples[0] if self.tuples else None

    async def alist(self, config, **_kwargs):
        for item in self.tuples:
            yield item

    async def adelete_thread(self, thread_id):
        self.deleted_threads.append(thread_id)

    async def setup(self):
        return None

    def get_next_version(self, current, channel):
        return f"postgres-version:{current}:{channel}"


class FailingArchivePostgresSaver(FakePostgresSaver):
    async def aput(self, config, checkpoint, metadata, new_versions):
        raise RuntimeError("archive failed")


class Runtime:
    def __repr__(self):
        return "<Runtime>"


def _config(thread_id: str = "thread-1", checkpoint_id: str | None = None):
    configurable = {"thread_id": thread_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _config_with_callback(thread_id: str = "thread-1", checkpoint_id: str | None = None):
    return {
        **_config(thread_id=thread_id, checkpoint_id=checkpoint_id),
        "callbacks": [object()],
    }


def _checkpoint(checkpoint_id: str, ts: str = "2026-07-01T00:00:00+00:00"):
    return {
        "id": checkpoint_id,
        "ts": ts,
        "channel_values": {"messages": [{"type": "human", "content": "hello"}]},
    }


def test_redis_first_saver_satisfies_langgraph_base_checkpointer_contract() -> None:
    saver = RedisFirstCheckpointSaver(FakePostgresSaver(), FakeRedis(), ttl_seconds=60)

    assert isinstance(saver, BaseCheckpointSaver)
    assert saver.config_specs == ["postgres-config-spec"]
    assert saver.get_next_version("1.0", "messages") == "postgres-version:1.0:messages"


@pytest.mark.asyncio
async def test_aput_writes_redis_before_scheduling_postgres() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    result = await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"writes": {"agent": {"messages": []}}},
        {"messages": 1},
    )

    assert result["configurable"]["checkpoint_id"] == "checkpoint-1"
    assert redis.ops[0][:2] == ("set", "pa:v1:checkpoint:thread-1:checkpoint-1")
    assert redis.ops[0][2] == 60
    assert postgres.aputs == []

    await saver.drain()
    assert len(postgres.aputs) == 1


@pytest.mark.asyncio
async def test_aput_strips_runtime_callbacks_from_redis_envelope() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput(
        _config_with_callback(),
        _checkpoint("checkpoint-1"),
        {"writes": {"agent": {"messages": []}}},
        {},
    )

    result = await saver.aget_tuple(_config(checkpoint_id="checkpoint-1"))

    assert result is not None
    assert "callbacks" not in result.config
    assert "callbacks" not in (result.parent_config or {})


@pytest.mark.asyncio
async def test_aput_writes_strips_runtime_callbacks_from_redis_payload() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput_writes(
        _config_with_callback(checkpoint_id="checkpoint-1"),
        [("messages", {"content": "hello"})],
        "task-1",
    )

    assert redis.ops[0][:2] == (
        "set",
        "pa:v1:checkpoint_writes:thread-1:checkpoint-1:task-1",
    )


@pytest.mark.asyncio
async def test_aput_sanitizes_runtime_objects_from_redis_envelope() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"writes": {"agent": {"runtime": Runtime()}}},
        {"runtime": Runtime()},
    )

    result = await saver.aget_tuple(_config(checkpoint_id="checkpoint-1"))

    assert result is not None
    assert result.metadata["writes"]["agent"]["runtime"] == "<Runtime>"
    assert postgres.aputs == []


@pytest.mark.asyncio
async def test_aput_writes_sanitizes_runtime_objects_from_redis_payload() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput_writes(
        _config(checkpoint_id="checkpoint-1"),
        [("runtime", Runtime())],
        "task-1",
    )

    assert redis.ops[0][:2] == (
        "set",
        "pa:v1:checkpoint_writes:thread-1:checkpoint-1:task-1",
    )
    assert postgres.writes == []


@pytest.mark.asyncio
async def test_aput_falls_back_to_sync_postgres_when_redis_fails() -> None:
    redis = FakeRedis(fail_set=True)
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    result = await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"writes": {"agent": {"messages": []}}},
        {},
    )

    assert result["configurable"]["checkpoint_id"] == "checkpoint-1"
    assert len(postgres.aputs) == 1


@pytest.mark.asyncio
async def test_aget_tuple_prefers_redis_checkpoint() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput(_config(), _checkpoint("checkpoint-1"), {"writes": {"agent": {}}}, {})
    await saver.drain()

    result = await saver.aget_tuple(_config(checkpoint_id="checkpoint-1"))

    assert result is not None
    assert result.config["configurable"]["checkpoint_id"] == "checkpoint-1"
    assert result.checkpoint["id"] == "checkpoint-1"


@pytest.mark.asyncio
async def test_aget_tuple_falls_back_to_postgres_on_miss() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    postgres.tuples = [
        CheckpointTuple(
            config=_config(checkpoint_id="checkpoint-pg"),
            checkpoint=_checkpoint("checkpoint-pg"),
            metadata={},
            parent_config=None,
        )
    ]
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    result = await saver.aget_tuple(_config(checkpoint_id="checkpoint-pg"))

    assert result is postgres.tuples[0]


@pytest.mark.asyncio
async def test_alist_returns_redis_checkpoints_newest_first() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput(
        _config(),
        _checkpoint("checkpoint-1", "2026-07-01T00:00:00+00:00"),
        {"writes": {"agent": {}}},
        {},
    )
    await saver.aput(
        _config(),
        _checkpoint("checkpoint-2", "2026-07-01T00:01:00+00:00"),
        {"writes": {"agent": {}}},
        {},
    )

    results = [item async for item in saver.alist(_config())]

    assert [item.checkpoint["id"] for item in results] == ["checkpoint-2", "checkpoint-1"]


@pytest.mark.asyncio
async def test_skip_nodes_are_not_written() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(
        postgres,
        redis,
        ttl_seconds=60,
        skip_nodes={"route_skills"},
    )

    result = await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"writes": {"route_skills": {"selected_skills": []}}},
        {},
    )

    await asyncio.sleep(0)

    assert result["configurable"]["checkpoint_id"] == "checkpoint-1"
    assert redis.ops == []
    assert postgres.aputs == []


@pytest.mark.asyncio
async def test_checkpoint_source_is_not_treated_as_skippable_graph_node() -> None:
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(
        postgres,
        redis,
        ttl_seconds=60,
        skip_nodes={"loop"},
    )

    result = await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"source": "loop"},
        {},
    )

    assert result["configurable"]["checkpoint_id"] == "checkpoint-1"
    assert redis.ops[0][:2] == ("set", "pa:v1:checkpoint:thread-1:checkpoint-1")


@pytest.mark.asyncio
async def test_background_postgres_archive_failures_are_logged(caplog) -> None:
    redis = FakeRedis()
    postgres = FailingArchivePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"writes": {"agent": {"messages": []}}},
        {},
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert any(
        record.message == "Async PostgreSQL checkpoint archive failed"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_redis_sync_write_and_async_postgres_archive_are_logged(caplog) -> None:
    caplog.set_level("INFO", logger="personal_assistant.checkpoint.redis_first")
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput(
        _config(),
        _checkpoint("checkpoint-1"),
        {"writes": {"agent": {"messages": []}}},
        {},
    )
    await saver.drain()

    messages = [record.message for record in caplog.records]
    assert "Redis checkpoint write completed" in messages
    assert "Async PostgreSQL checkpoint archive scheduled" in messages
    assert "Async PostgreSQL checkpoint archive completed" in messages


@pytest.mark.asyncio
async def test_redis_checkpoint_writes_success_is_logged(caplog) -> None:
    caplog.set_level("INFO", logger="personal_assistant.checkpoint.redis_first")
    redis = FakeRedis()
    postgres = FakePostgresSaver()
    saver = RedisFirstCheckpointSaver(postgres, redis, ttl_seconds=60)

    await saver.aput_writes(
        _config(checkpoint_id="checkpoint-1"),
        [("messages", {"content": "hello"})],
        "task-1",
    )
    await saver.drain()

    messages = [record.message for record in caplog.records]
    assert "Redis checkpoint writes completed" in messages
    assert "Async PostgreSQL checkpoint archive scheduled" in messages
    assert "Async PostgreSQL checkpoint archive completed" in messages


class FakeCursor:
    async def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return FakeCursor()


class FakeConnectionContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self):
        self.conn = FakeConnection()

    def connection(self):
        return FakeConnectionContext(self.conn)


@pytest.mark.asyncio
async def test_postgres_memory_cleanup_expired_checkpoints_executes_checkpoint_deletes() -> None:
    memory = PostgresMemory(
        "postgresql://example",
        checkpoint_ttl_seconds=3600,
    )
    pool = FakePool()
    memory.pool = pool

    await memory.cleanup_expired_checkpoints()

    joined_sql = "\n".join(sql for sql, _params in pool.conn.sql)
    assert "checkpoint_writes" in joined_sql
    assert "checkpoint_blobs" in joined_sql
    assert "checkpoints" in joined_sql
    assert pool.conn.sql[0][1] == (3600,)
