# Redis-First Checkpoints Design

## Context

LangGraph checkpoints currently write directly to PostgreSQL through
`AsyncPostgresSaver`. This makes PostgreSQL the synchronous hot path for every graph
state save, including large message payloads and deterministic intermediate nodes. As
conversation threads grow, checkpoint tables expand quickly and each turn pays the cost
of durable database writes.

The desired behavior is to make Redis the short-term authoritative checkpoint read
source while PostgreSQL becomes the asynchronous durable archive. Redis must be written
synchronously first. PostgreSQL writes should happen in the background after Redis
accepts the checkpoint. Checkpoint data should also use MessagePack-oriented compressed
serialization, have a configurable TTL in both Redis and PostgreSQL, and avoid saving
deterministic graph nodes that can be recomputed.

## Goals

- Store hot checkpoints in Redis synchronously before returning from LangGraph saver
  writes.
- Persist accepted Redis checkpoints to PostgreSQL asynchronously for replay and
  durability.
- Use compressed MessagePack checkpoint serialization for Redis and PostgreSQL saver
  payloads.
- Apply one checkpoint TTL configuration to both Redis keys and PostgreSQL checkpoint
  cleanup.
- Configure Redis with an LRU eviction policy on a best-effort basis.
- Reduce checkpoint volume by skipping deterministic nodes.
- Preserve chat correctness when Redis is unavailable by falling back to synchronous
  PostgreSQL writes.

## Non-Goals

- Do not rewrite LangGraph execution or fork `AsyncPostgresSaver`.
- Do not make Redis mandatory for local development or unit tests.
- Do not drop checkpoint replay support.
- Do not cache final assistant answers.
- Do not change audit logs, execution logs, tool results, or long-term memory write
  semantics.

## Architecture

Introduce a Redis-first checkpointer that implements the same methods LangGraph uses from
`AsyncPostgresSaver`: `aput`, `aput_writes`, `aget_tuple`, `alist`, `adelete_thread`, and
`setup`. `PostgresMemory.start()` will still create the underlying `AsyncPostgresSaver`.
When `REDIS_URL` is configured, it wraps the saver with `RedisFirstCheckpointSaver`.

The wrapper keeps LangGraph integration unchanged:

```python
return graph.compile(checkpointer=memory.checkpointer)
```

### Write Path

1. `aput` inspects checkpoint metadata to identify the graph node.
2. If the node is deterministic and skipped, the wrapper returns a config compatible with
   LangGraph without writing Redis or PostgreSQL.
3. Otherwise it serializes the checkpoint tuple to a compressed MessagePack binary blob
   and writes it to Redis with `EX=CHECKPOINT_TTL_SECONDS`.
4. After Redis accepts the write, it schedules the underlying PostgreSQL `aput` in an
   `asyncio` task.
5. If Redis write fails, the wrapper logs the error and performs the PostgreSQL write
   synchronously so checkpoint state is not lost.

`aput_writes` follows the same rule for pending channel writes. Redis gets the write
payload first and PostgreSQL receives it in the background. The Redis payload is retained
primarily to make `aget_tuple` and `alist` reconstruct recent checkpoints without a
PostgreSQL hit.

### Read Path

`aget_tuple(config)` reads the latest matching checkpoint from Redis first. If Redis has
no matching checkpoint or the payload cannot be decoded, it falls back to PostgreSQL.

`alist(config)` returns Redis checkpoints for the thread ordered newest-first when Redis
has data. If Redis has no data for the thread, it delegates to PostgreSQL. This keeps
recent replay fast while preserving older replay through PostgreSQL after Redis TTL or
eviction.

### Async PostgreSQL Drain

The wrapper tracks pending PostgreSQL write tasks. `PostgresMemory.stop()` calls a
`drain()` method before closing the pool. Drain waits up to a configurable timeout and
logs unfinished tasks rather than hiding shutdown issues.

### Serialization

Add a small compressed serializer wrapper around LangGraph's `JsonPlusSerializer`.
`JsonPlusSerializer` already uses MessagePack for checkpoint serde payloads. The wrapper
compresses the serialized bytes with zlib when they exceed a small threshold and prefixes
the encoded payload with a versioned marker. Deserialization handles both compressed and
uncompressed values.

Redis stores a single binary envelope containing:

- checkpoint config
- checkpoint payload
- metadata
- new versions
- parent config
- created timestamp
- node name

The envelope is MessagePack encoded and zlib-compressed. PostgreSQL uses the same serde
instance through `AsyncPostgresSaver(..., serde=compressed_serde)`.

## TTL

Add backend settings:

- `CHECKPOINT_TTL_SECONDS`, default `604800` seconds.
- `CHECKPOINT_PG_CLEANUP_ENABLED`, default `true`.
- `CHECKPOINT_PG_CLEANUP_INTERVAL_SECONDS`, default `3600`.
- `CHECKPOINT_REDIS_LRU_ENABLED`, default `true`.
- `CHECKPOINT_REDIS_MAXMEMORY_POLICY`, default `allkeys-lru`.
- `CHECKPOINT_SKIP_NODES`, default `route_skills,compact_context`.

Redis keys use the TTL directly. PostgreSQL cleanup deletes checkpoint rows older than
the same TTL based on checkpoint timestamps and related writes/blobs for the same thread
and checkpoint namespace.

## Redis LRU

At startup, when Redis is used for checkpoints, the backend attempts:

```text
CONFIG SET maxmemory-policy allkeys-lru
```

Some hosted Redis services block `CONFIG SET`; failure is logged as a warning and does
not block startup. The configured policy string must be non-empty so operators can switch
to `volatile-lru` or another Redis-supported policy if their environment requires it.

## Selective Checkpointing

Skip deterministic nodes:

- `route_skills`: skill selection can be recalculated from registry, memory, and current
  message state.
- `compact_context` when it does not write a changed message state. The first
  implementation uses node-level skip configured through `CHECKPOINT_SKIP_NODES`; if
  compaction output becomes necessary for precise replay, operators can remove
  `compact_context` from the skip list.

Retain non-deterministic or externally meaningful nodes:

- `agent`
- `tools`
- `approval`
- `memory_reflection`

Filtering is implemented in the checkpointer wrapper by reading `metadata["writes"]`.
This avoids changing graph topology or node behavior.

## Error Handling

- Redis write failure: log and synchronously write PostgreSQL.
- PostgreSQL background failure: log with thread/checkpoint metadata; do not fail the
  already completed Redis-first turn.
- Redis decode failure: delete the corrupt key best-effort and fall back to PostgreSQL.
- PostgreSQL cleanup failure: log and continue startup/request processing.
- Redis LRU configuration failure: warning only.

## Testing

Unit tests use fake Redis and fake PostgreSQL saver objects. They must not require live
Redis or PostgreSQL.

Required tests:

- Compressed serializer round-trips a checkpoint payload and produces compressed bytes
  for large payloads.
- Redis-first `aput` writes Redis before scheduling PostgreSQL.
- Redis-first `aput` falls back to synchronous PostgreSQL when Redis fails.
- `aget_tuple` prefers Redis and falls back to PostgreSQL on miss.
- `alist` returns Redis checkpoints newest-first and falls back to PostgreSQL when Redis
  has no thread entries.
- TTL is passed to Redis writes.
- Deterministic nodes in `CHECKPOINT_SKIP_NODES` are not written.
- Pending PostgreSQL writes are drained on stop.
- Config defaults and environment overrides are validated.
- PostgreSQL cleanup SQL is invoked when TTL cleanup is enabled.

## Documentation Updates

After implementation, update:

- `技术方案报告.md`
- `工程化兜底.md`
- `README.md`

The documents must describe Redis-first checkpoint storage, compressed MessagePack serde,
checkpoint TTL, Redis LRU configuration, PostgreSQL async archival, and selective
checkpointing.

## Acceptance Criteria

- Existing behavior works without `REDIS_URL`; PostgreSQL remains usable directly.
- With Redis configured, LangGraph checkpoint writes synchronously hit Redis first and
  PostgreSQL writes run asynchronously.
- Redis reads serve recent checkpoints and replay; PostgreSQL replay remains available
  after Redis miss.
- Checkpoint TTL affects Redis keys and PostgreSQL cleanup.
- Redis LRU setup is attempted and non-fatal on managed Redis.
- Deterministic nodes are skipped by default.
- Focused and full backend tests pass after the change.
