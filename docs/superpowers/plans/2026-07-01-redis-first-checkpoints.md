# Redis-First Checkpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LangGraph checkpoints write to Redis synchronously, archive to PostgreSQL asynchronously, compress checkpoint payloads, enforce checkpoint TTL, configure Redis LRU, and skip deterministic checkpoint nodes.

**Architecture:** Add a focused checkpoint package containing compressed serde and a Redis-first saver wrapper around `AsyncPostgresSaver`. Wire it through `PostgresMemory.start()` while preserving PostgreSQL-only behavior when Redis is absent. Add config, cleanup helpers, tests, and documentation updates.

**Tech Stack:** Python 3.11, LangGraph checkpoint saver APIs, redis-py asyncio, pytest, Pydantic Settings, PostgreSQL via psycopg.

## Global Constraints

- Redis is the short-term authoritative read source for recent checkpoints when configured.
- Redis writes must happen before PostgreSQL archive writes are scheduled.
- Redis failure must fall back to synchronous PostgreSQL writes.
- PostgreSQL writes after successful Redis writes are asynchronous.
- `CHECKPOINT_TTL_SECONDS` applies to Redis key TTL and PostgreSQL cleanup.
- Redis LRU configuration is best-effort and must not block startup.
- Tests must use fakes and must not require live Redis or PostgreSQL.
- Follow strict TDD: write each failing test, verify it fails, then implement minimal code.

---

## File Structure

- Create `backend/src/personal_assistant/checkpoint/__init__.py`: public checkpoint exports.
- Create `backend/src/personal_assistant/checkpoint/serde.py`: compressed MessagePack-oriented serializer wrapper.
- Create `backend/src/personal_assistant/checkpoint/redis_first.py`: Redis-first saver wrapper and Redis key helpers.
- Modify `backend/src/personal_assistant/config.py`: checkpoint TTL, Redis LRU, and skip-node settings.
- Modify `backend/src/personal_assistant/memory/postgres.py`: instantiate compressed serde, wrap saver, cleanup expired PostgreSQL checkpoints, drain async writes on stop.
- Modify `backend/src/personal_assistant/cache/redis_cache.py`: binary Redis client support and best-effort LRU policy helper.
- Add `backend/tests/test_checkpoint_serde.py`: serializer tests.
- Add `backend/tests/test_redis_first_checkpoint.py`: wrapper behavior tests.
- Extend `backend/tests/test_config.py`: checkpoint config defaults and overrides.
- Extend `backend/tests/test_cache.py`: Redis LRU configuration helper.
- Update `README.md`, `技术方案报告.md`, and `工程化兜底.md`.

## Task 1: Checkpoint Configuration

**Files:**
- Modify: `backend/src/personal_assistant/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.checkpoint_ttl_seconds: int`
- Produces: `Settings.checkpoint_pg_cleanup_enabled: bool`
- Produces: `Settings.checkpoint_pg_cleanup_interval_seconds: int`
- Produces: `Settings.checkpoint_redis_lru_enabled: bool`
- Produces: `Settings.checkpoint_redis_maxmemory_policy: str`
- Produces: `Settings.checkpoint_skip_nodes: list[str]`

- [ ] **Step 1: Write the failing test**

Add tests asserting defaults and environment overrides:

```python
def test_checkpoint_storage_defaults() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        _env_file=None,
    )

    assert settings.checkpoint_ttl_seconds == 604800
    assert settings.checkpoint_pg_cleanup_enabled is True
    assert settings.checkpoint_pg_cleanup_interval_seconds == 3600
    assert settings.checkpoint_redis_lru_enabled is True
    assert settings.checkpoint_redis_maxmemory_policy == "allkeys-lru"
    assert settings.checkpoint_skip_nodes == ["route_skills", "compact_context"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run --extra dev pytest tests/test_config.py::test_checkpoint_storage_defaults -v`
Expected: FAIL with missing settings attributes.

- [ ] **Step 3: Write minimal implementation**

Add the six settings fields and a validator that parses comma-separated skip-node strings
into a stripped list.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run --extra dev pytest tests/test_config.py::test_checkpoint_storage_defaults -v`
Expected: PASS.

## Task 2: Compressed MessagePack Serializer

**Files:**
- Create: `backend/src/personal_assistant/checkpoint/__init__.py`
- Create: `backend/src/personal_assistant/checkpoint/serde.py`
- Test: `backend/tests/test_checkpoint_serde.py`

**Interfaces:**
- Produces: `CompressedJsonPlusSerializer(compress_threshold_bytes: int = 1024)`
- Produces methods: `dumps_typed(obj) -> tuple[str, bytes]`, `loads_typed(data) -> Any`

- [ ] **Step 1: Write the failing tests**

Test round-trip and compression marker behavior for a large payload.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run --extra dev pytest tests/test_checkpoint_serde.py -v`
Expected: FAIL because the checkpoint package does not exist.

- [ ] **Step 3: Write minimal implementation**

Wrap `langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer`; compress bytes returned
from `dumps_typed` when they exceed the threshold and decompress in `loads_typed`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; uv run --extra dev pytest tests/test_checkpoint_serde.py -v`
Expected: PASS.

## Task 3: Redis-First Saver

**Files:**
- Create: `backend/src/personal_assistant/checkpoint/redis_first.py`
- Test: `backend/tests/test_redis_first_checkpoint.py`

**Interfaces:**
- Consumes: Redis-like async client with `set`, `get`, `zadd`, `zrevrange`, `delete`, `scan_iter`.
- Consumes: PostgreSQL saver-like object with `aput`, `aput_writes`, `aget_tuple`, `alist`, `adelete_thread`, `setup`.
- Produces: `RedisFirstCheckpointSaver(postgres_saver, redis_client, ttl_seconds, skip_nodes, serde=None)`
- Produces methods compatible with LangGraph saver calls.

- [ ] **Step 1: Write failing tests**

Cover Redis-before-PostgreSQL ordering, synchronous PostgreSQL fallback on Redis error,
Redis read preference, PostgreSQL read fallback, TTL use, skip nodes, and drain behavior.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend; uv run --extra dev pytest tests/test_redis_first_checkpoint.py -v`
Expected: FAIL because `redis_first.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

Store checkpoint envelopes in Redis using binary values and per-thread sorted sets.
Schedule PostgreSQL writes with `asyncio.create_task` only after Redis writes succeed.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd backend; uv run --extra dev pytest tests/test_redis_first_checkpoint.py -v`
Expected: PASS.

## Task 4: Wire PostgresMemory And Redis LRU

**Files:**
- Modify: `backend/src/personal_assistant/memory/postgres.py`
- Modify: `backend/src/personal_assistant/cache/redis_cache.py`
- Test: `backend/tests/test_cache.py`
- Test: `backend/tests/test_redis_first_checkpoint.py`

**Interfaces:**
- Produces: `configure_redis_lru(client, policy: str) -> None`
- Produces: `PostgresMemory(..., redis_url=None, checkpoint_ttl_seconds=604800, checkpoint_skip_nodes=None, ...)`
- Produces: `PostgresMemory.cleanup_expired_checkpoints() -> None`

- [ ] **Step 1: Write failing tests**

Add tests for best-effort Redis LRU config and PostgreSQL cleanup SQL invocation.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend; uv run --extra dev pytest tests/test_cache.py tests/test_redis_first_checkpoint.py -v`
Expected: FAIL because the helper and cleanup hook are missing.

- [ ] **Step 3: Write minimal implementation**

Create binary Redis clients with `decode_responses=False` for checkpoint storage, wrap the
PostgreSQL saver when Redis is configured, pass compressed serde to `AsyncPostgresSaver`,
run cleanup at startup, and drain pending archive tasks on stop.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd backend; uv run --extra dev pytest tests/test_cache.py tests/test_redis_first_checkpoint.py -v`
Expected: PASS.

## Task 5: Server Wiring And Documentation

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `README.md`
- Modify: `技术方案报告.md`
- Modify: `工程化兜底.md`
- Test: `backend/tests/test_config.py`
- Test: `backend/tests/test_checkpoint_replay.py`

**Interfaces:**
- Consumes: new `Settings` checkpoint fields.
- Produces: server-created `PostgresMemory` configured for Redis-first checkpoints.

- [ ] **Step 1: Write or update failing test**

Extend existing tests where constructor expectations need new config coverage. Keep
existing replay serialization tests unchanged.

- [ ] **Step 2: Run focused tests**

Run: `cd backend; uv run --extra dev pytest tests/test_config.py tests/test_checkpoint_replay.py -v`
Expected: PASS after wiring or clear failure identifying missing constructor integration.

- [ ] **Step 3: Implement server and docs updates**

Pass checkpoint settings into `PostgresMemory` in `server.py`. Update the three requested
documents with Redis-first checkpoint behavior, TTL, LRU, compression, async PostgreSQL
archive, and skipped deterministic nodes.

- [ ] **Step 4: Run focused tests again**

Run: `cd backend; uv run --extra dev pytest tests/test_config.py tests/test_checkpoint_replay.py -v`
Expected: PASS.

## Task 6: Final Verification

**Files:**
- Verify all touched backend and documentation files.

**Interfaces:**
- Produces verified Redis-first checkpoint feature.

- [ ] **Step 1: Run focused checkpoint tests**

Run: `cd backend; uv run --extra dev pytest tests/test_checkpoint_serde.py tests/test_redis_first_checkpoint.py tests/test_cache.py tests/test_config.py tests/test_checkpoint_replay.py -v`
Expected: PASS.

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend; uv run --extra dev pytest -v`
Expected: PASS.

- [ ] **Step 3: Check diff hygiene**

Run: `git diff --check`
Expected: no whitespace errors.

- [ ] **Step 4: Review changed files**

Run: `git status --short`
Expected: only intended checkpoint, config, memory, cache, test, plan/spec, and requested
documentation changes.
