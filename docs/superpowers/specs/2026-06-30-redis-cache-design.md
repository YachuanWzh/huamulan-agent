# Redis Cache Design

## Context

The assistant sometimes responds slowly, but the current evidence does not isolate one
single bottleneck. The backend has several likely contributors:

- LangGraph graph compilation and per-turn setup.
- Skill routing and system prompt construction.
- Long-term memory files being read and concatenated on each turn.
- PostgreSQL checkpoint, thread, audit, execution log, and summary queries.
- LLM latency and tool execution latency.

Redis will be introduced as a low-risk acceleration layer and observability point. It
must not replace PostgreSQL as the source of truth, and it must not cache final LLM
answers in the first implementation.

The intended Redis service is reachable at host `192.168.5.7` port `6379`. The backend
configuration should use a Redis URL such as `redis://192.168.5.7:6379/0`.

## Goals

- Reduce repeated PostgreSQL reads for hot operational endpoints.
- Reduce repeated file reads and prompt-fragment construction for long-term memory.
- Keep behavior unchanged when Redis is disabled or unavailable.
- Add cache hit, miss, set, delete, and error logging to make future tuning evidence-based.
- Preserve strong correctness for thread state, approvals, audit data, and execution logs.

## Non-Goals

- Do not cache final chat responses or tool execution results as a substitute for tool calls.
- Do not move checkpoints, audit logs, execution logs, or long-term memory writes out of
  PostgreSQL.
- Do not require Redis for local development or tests.
- Do not introduce a distributed locking system in this phase.

## Recommended Approach

Use a layered cache design:

1. Add a cache abstraction with Redis and no-op implementations.
2. Wrap selected read-heavy `PostgresMemory` methods with cache lookups.
3. Invalidate Redis keys immediately after related writes.
4. Cache long-term memory prompt content using a file-versioned key.
5. Log cache behavior and Redis operation latency.

This is preferred over caching only frontend page queries because it also helps the agent
hot path through long-term memory reads. It is preferred over caching complete agent
answers because the assistant state, approval flow, tools, memory, and security guards
make response-level caching risky.

## Components

### Cache Interface

Create `personal_assistant.cache` with a small asynchronous interface:

- `get_json(key: str) -> Any | None`
- `set_json(key: str, value: Any, ttl_seconds: int) -> None`
- `delete(key: str) -> None`
- `delete_pattern(pattern: str) -> None`
- `close() -> None`

The interface should log namespace, outcome, and duration for each Redis operation.
Serialization should use JSON-compatible values already produced by FastAPI encoders or
Pydantic models.

### RedisCache

`RedisCache` connects to `REDIS_URL`. Redis errors are treated as cache misses for reads
and best-effort failures for writes or deletes. The business method must still complete
through PostgreSQL or the filesystem.

The dependency should be `redis>=5` because it provides an asyncio client through
`redis.asyncio`.

### NoopCache

`NoopCache` implements the same interface and always misses. It is used when
`CACHE_ENABLED=false`, `REDIS_URL` is empty, or Redis setup fails at startup.

### CachedPostgresMemory

Wrap the existing `PostgresMemory` by composition instead of subclassing it. The wrapper
delegates writes to the wrapped memory, then invalidates keys. It caches only selected
read methods:

- `list_threads(limit)`
- `list_execution_logs(thread_id, limit)`
- `execution_log_summary(thread_id)`
- `list_audit_events(thread_id, limit)`
- `list_tool_errors(thread_id, limit)`

Methods not explicitly cached should delegate directly.

### Long-Term Memory Cache

Cache `LongTermMemoryStore.read_all()` output by a versioned key derived from relevant
Markdown file paths, mtimes, and sizes under the memory directory. When files change, the
version changes and the next read misses naturally. This avoids needing manual invalidation
for filesystem writes.

## Configuration

Add backend settings:

- `CACHE_ENABLED`, default `true`.
- `REDIS_URL`, default `None`.
- `CACHE_DEFAULT_TTL_SECONDS`, default `10`.
- `CACHE_LOG_TTL_SECONDS`, default `5`.
- `CACHE_MEMORY_TTL_SECONDS`, default `60`.

For this environment, set:

```ini
CACHE_ENABLED=true
REDIS_URL=redis://192.168.5.7:6379/0
```

If the user supplies `http://192.168.5.7:6379`, startup validation should reject it with
a clear message that `REDIS_URL` must use the `redis://` or `rediss://` scheme. This keeps
configuration errors visible instead of silently connecting to the wrong protocol.

## Cache Keys And TTLs

Use a versioned prefix so future key migrations are easy:

- `pa:v1:threads:list:{limit}` with TTL `10s`.
- `pa:v1:execution_logs:{thread_id}:{limit}` with TTL `5s`.
- `pa:v1:execution_summary:{thread_id}` with TTL `10s`.
- `pa:v1:audit_events:{thread_id_or_all}:{limit}` with TTL `10s`.
- `pa:v1:tool_errors:{thread_id_or_all}:{limit}` with TTL `10s`.
- `pa:v1:long_term_memory:{memory_dir_hash}:{files_version_hash}` with TTL `60s`.

Thread IDs and other dynamic parts should be escaped or hashed when needed so keys remain
short and safe.

## Invalidation

Use explicit invalidation after successful writes:

- `record_execution_log(thread_id=...)`
  deletes `pa:v1:execution_summary:{thread_id}` and
  `pa:v1:execution_logs:{thread_id}:*`, plus `pa:v1:threads:list:*` so sidebar thread
  ordering and summaries remain fresh after each turn.
- `record_audit_event(thread_id=...)`
  deletes `pa:v1:audit_events:all:*` and `pa:v1:audit_events:{thread_id}:*`.
- `record_tool_error(thread_id=...)`
  deletes `pa:v1:tool_errors:all:*` and `pa:v1:tool_errors:{thread_id}:*`.
- `delete_thread(thread_id)`
  deletes that thread's execution, audit, and tool error caches, plus
  `pa:v1:threads:list:*`.
- `clear_threads()`
  deletes all thread, execution, audit, and tool error cache namespaces.

`record_tool_result` is not cached in the first phase.

Thread list caching uses conservative invalidation. The first implementation deletes
`pa:v1:threads:list:*` after any execution log write, `delete_thread`, and
`clear_threads`. This lowers thread-list hit rate during active conversations, but keeps
the UI fresh and avoids subtle stale-order bugs.

## Observability

Log cache events with structured metadata:

- `event`: `cache_hit`, `cache_miss`, `cache_set`, `cache_delete`, `cache_error`.
- `namespace`: for example `execution_summary`, `threads_list`, `long_term_memory`.
- `duration_ms`.
- `key_hash` rather than full key when the key may include user-controlled content.
- `error_type` and `error_message` for Redis failures.

Do not write cache telemetry into `agent_execution_logs` in the first phase because that
would add PostgreSQL writes to the same hot path being optimized.

## Error Handling

Redis is an optional accelerator. If Redis is down, slow, or returns malformed data:

- Reads fall back to PostgreSQL or filesystem data.
- Writes and invalidations log errors but do not fail the request.
- Cached JSON decode errors are treated as misses and the bad key is deleted best-effort.
- Startup chooses `NoopCache` if Redis initialization fails.

## Testing

Use fake cache implementations in unit tests; do not require a real Redis server.

Required tests:

- Redis disabled keeps current behavior for cached memory methods.
- `execution_log_summary` caches the first PostgreSQL result and returns the cached result
  on the second read.
- `record_execution_log` invalidates execution summary and execution logs for the thread.
- `list_threads` uses cache and `delete_thread` plus `clear_threads` invalidate list keys.
- `list_audit_events` and `list_tool_errors` cache separately for all threads and a single
  thread.
- Redis/cache exceptions are swallowed and the underlying PostgreSQL result is returned.
- Long-term memory cache hits when files are unchanged and misses when a memory Markdown
  file mtime or size changes.

Integration smoke testing can optionally run against `redis://192.168.5.7:6379/0`, but
unit tests must remain deterministic without network access.

## Rollout Plan

1. Add cache configuration, cache interface, `NoopCache`, and `RedisCache`.
2. Wrap `PostgresMemory` with `CachedPostgresMemory` in `server.py`.
3. Add cached reads and explicit invalidation for operational APIs.
4. Add long-term memory prompt cache.
5. Enable Redis with `REDIS_URL=redis://192.168.5.7:6379/0`.
6. Compare logs for hit rate and request latency before deciding whether to expand caching.

## Acceptance Criteria

- The backend starts and all existing tests pass with no Redis configuration.
- With Redis configured, cached read methods produce the same API responses as PostgreSQL.
- Cache invalidation prevents stale execution summaries, execution logs, audit events,
  tool errors, and deleted thread list entries after writes.
- Redis outages do not break chat, replay, audit, or thread APIs.
- Cache logs show hit/miss/set/delete/error events with operation duration.
