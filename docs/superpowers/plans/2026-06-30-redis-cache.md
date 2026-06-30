# Redis Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Redis-backed optional caching for read-heavy backend paths and long-term memory prompt content.

**Architecture:** Add a small cache package with `NoopCache`, optional `RedisCache`, and focused helper functions. Wrap `PostgresMemory` with `CachedPostgresMemory` so PostgreSQL remains authoritative while selected reads are cached and related writes invalidate keys. Add a cached long-term memory reader used by the agent prompt path.

**Tech Stack:** Python 3.11, FastAPI, Pydantic Settings, pytest, optional `redis.asyncio` via `redis>=5`.

## Global Constraints

- Redis URL for this environment is `redis://192.168.5.7:6379/0`.
- Redis is optional; missing or unavailable Redis must not break chat, replay, audit, or thread APIs.
- PostgreSQL remains the source of truth for checkpoints, audit events, execution logs, tool results, and long-term memory writes.
- Do not cache final chat responses or substitute cached data for tool execution.
- Tests must use fake cache/memory objects and must not require a real Redis server.
- Follow strict TDD: write each failing test, verify it fails, then implement minimal code.

---

## File Structure

- Create `backend/src/personal_assistant/cache/__init__.py`: public cache exports.
- Create `backend/src/personal_assistant/cache/base.py`: async cache protocol, `NoopCache`, JSON helpers, logging helper.
- Create `backend/src/personal_assistant/cache/redis_cache.py`: optional Redis implementation and factory.
- Create `backend/src/personal_assistant/memory/cached.py`: `CachedPostgresMemory` wrapper and cache-key helpers.
- Modify `backend/src/personal_assistant/memory/long_term.py`: add versioned cached `read_all` helper without changing writes.
- Modify `backend/src/personal_assistant/agent/agent.py`: pass optional cache into long-term memory prompt reads.
- Modify `backend/src/personal_assistant/agent/router.py`: use cached memory text helper when cache is provided.
- Modify `backend/src/personal_assistant/agent/harness.py`: carry optional cache through compile calls and background reflection.
- Modify `backend/src/personal_assistant/api/server.py`: build cache at startup and wrap memory.
- Modify `backend/src/personal_assistant/config.py`: add cache settings and Redis URL validation.
- Modify `backend/pyproject.toml`: add `redis>=5`.
- Add tests in `backend/tests/test_cache.py`, `backend/tests/test_cached_memory.py`, and extend existing config/long-term-memory tests.

## Task 1: Cache Settings And Noop Cache

**Files:**
- Create: `backend/src/personal_assistant/cache/__init__.py`
- Create: `backend/src/personal_assistant/cache/base.py`
- Modify: `backend/src/personal_assistant/config.py`
- Test: `backend/tests/test_cache.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `AsyncCache` protocol with `get_json`, `set_json`, `delete`, `delete_pattern`, `close`.
- Produces: `NoopCache` class.
- Produces settings fields: `cache_enabled`, `redis_url`, `cache_default_ttl_seconds`, `cache_log_ttl_seconds`, `cache_memory_ttl_seconds`.

- [ ] **Step 1: Write failing tests**

Add tests that assert `NoopCache` always misses and cache settings validate Redis URL schemes.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend; uv run pytest tests/test_cache.py tests/test_config.py -v`
Expected: FAIL because `personal_assistant.cache` and cache settings do not exist.

- [ ] **Step 3: Implement minimal code**

Create the cache package, add `NoopCache`, and add config fields with validation accepting only `redis://` and `rediss://`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd backend; uv run pytest tests/test_cache.py tests/test_config.py -v`
Expected: PASS.

## Task 2: CachedPostgresMemory Wrapper

**Files:**
- Create: `backend/src/personal_assistant/memory/cached.py`
- Test: `backend/tests/test_cached_memory.py`

**Interfaces:**
- Consumes: `AsyncCache`.
- Produces: `CachedPostgresMemory(inner, cache, default_ttl_seconds=10, log_ttl_seconds=5)`.
- Produces cached read methods and delegating write methods matching `PostgresMemory`.

- [ ] **Step 1: Write failing tests**

Add fake cache and fake memory tests for cached summary reads, invalidation after execution log writes, list thread invalidation, audit/tool error caching, and cache-error fallback.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend; uv run pytest tests/test_cached_memory.py -v`
Expected: FAIL because `personal_assistant.memory.cached` does not exist.

- [ ] **Step 3: Implement minimal code**

Implement cache keys, JSON-compatible serialization, selected cached read methods, write invalidation, and `__getattr__` delegation for uncached methods.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd backend; uv run pytest tests/test_cached_memory.py -v`
Expected: PASS.

## Task 3: Long-Term Memory Cache

**Files:**
- Modify: `backend/src/personal_assistant/memory/long_term.py`
- Modify: `backend/src/personal_assistant/agent/router.py`
- Modify: `backend/src/personal_assistant/agent/agent.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Test: `backend/tests/test_long_term_memory.py`
- Test: existing agent/router tests if required by signature changes.

**Interfaces:**
- Consumes: `AsyncCache | None`.
- Produces: `LongTermMemoryStore.read_all_cached(cache, ttl_seconds=60) -> str`.
- Produces `build_skill_router(..., cache=None, memory_cache_ttl_seconds=60)`.
- Produces `compile_agent(..., cache=None)`.

- [ ] **Step 1: Write failing tests**

Extend long-term memory tests to verify cache hit when files are unchanged and miss when a Markdown file changes.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend; uv run pytest tests/test_long_term_memory.py -v`
Expected: FAIL because `read_all_cached` does not exist.

- [ ] **Step 3: Implement minimal code**

Add file-version hashing and cached read helper. Thread the optional cache through router, agent compile, harness compile, and background reflection.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd backend; uv run pytest tests/test_long_term_memory.py tests/test_agent_split.py tests/test_hooks.py -v`
Expected: PASS.

## Task 4: RedisCache And Server Wiring

**Files:**
- Create: `backend/src/personal_assistant/cache/redis_cache.py`
- Modify: `backend/src/personal_assistant/api/server.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/test_cache.py`

**Interfaces:**
- Consumes: settings cache fields.
- Produces: `build_cache(settings) -> AsyncCache`.
- Produces `RedisCache.from_url(url: str)`.

- [ ] **Step 1: Write failing tests**

Add tests that `build_cache` returns `NoopCache` when disabled or URL is absent, and that Redis client errors are swallowed by cache methods.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd backend; uv run pytest tests/test_cache.py -v`
Expected: FAIL because `redis_cache.py` and `build_cache` do not exist.

- [ ] **Step 3: Implement minimal code**

Add `redis>=5`, implement Redis-backed JSON get/set/delete/delete_pattern with safe fallbacks, and update `server.py` lifespan to start/close cache while wrapping `PostgresMemory`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `cd backend; uv run pytest tests/test_cache.py tests/test_config.py -v`
Expected: PASS.

## Task 5: Full Verification

**Files:**
- Verify all touched backend files.

**Interfaces:**
- Consumes all prior tasks.
- Produces verified Redis cache feature.

- [ ] **Step 1: Run focused backend tests**

Run: `cd backend; uv run pytest tests/test_cache.py tests/test_cached_memory.py tests/test_long_term_memory.py tests/test_config.py tests/test_agent_split.py tests/test_hooks.py -v`
Expected: PASS.

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend; uv run pytest -v`
Expected: PASS.

- [ ] **Step 3: Review diff**

Run: `git diff --stat` and `git diff --check`
Expected: only intended cache/config/memory/agent/server/test changes and no whitespace errors.

- [ ] **Step 4: Commit**

Run:

```powershell
git add backend/pyproject.toml backend/src/personal_assistant backend/tests docs/superpowers/plans/2026-06-30-redis-cache.md
git commit -m "feat(cache): add optional redis caching"
```
