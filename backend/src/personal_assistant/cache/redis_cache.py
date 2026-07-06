import json
import logging
import time
from typing import Any

from personal_assistant.cache.base import NoopCache

logger = logging.getLogger(__name__)


class RedisCache:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_url(cls, url: str) -> "RedisCache":
        from redis.asyncio import Redis

        return cls(Redis.from_url(url, decode_responses=True))

    async def get_json(self, key: str) -> Any | None:
        started = time.perf_counter()
        try:
            raw = await self.client.get(key)
            if raw is None:
                _log("cache_miss", key, started)
                return None
            _log("cache_hit", key, started)
            return json.loads(raw)
        except Exception as exc:
            _log("cache_error", key, started, exc)
            return None

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        started = time.perf_counter()
        try:
            await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
            # Track thread-scoped cache keys for fast O(K) deletion
            scope = _thread_scope_from_key(key)
            if scope is not None:
                tracking_key = _cache_tracking_key(scope)
                await self.client.sadd(tracking_key, key)
                await self.client.expire(tracking_key, max(ttl_seconds, 60))
            _log("cache_set", key, started)
        except Exception as exc:
            _log("cache_error", key, started, exc)

    async def delete(self, key: str) -> None:
        started = time.perf_counter()
        try:
            await self.client.delete(key)
            _log("cache_delete", key, started)
        except Exception as exc:
            _log("cache_error", key, started, exc)

    async def delete_pattern(self, pattern: str) -> None:
        started = time.perf_counter()
        try:
            keys = [key async for key in self.client.scan_iter(match=pattern)]
            if keys:
                await self.client.delete(*keys)
            _log("cache_delete", pattern, started)
        except Exception as exc:
            _log("cache_error", pattern, started, exc)

    async def delete_thread_scope(self, safe_thread: str) -> None:
        """Delete all cache keys for a thread scope in O(K) via tracking SET.

        Replaces multiple O(N) ``delete_pattern`` SCAN calls with a single
        O(K) ``SMEMBERS`` where K is the number of cache keys for this thread.
        """
        started = time.perf_counter()
        try:
            tracking_key = _cache_tracking_key(safe_thread)
            raw_members = await self.client.smembers(tracking_key)
            if raw_members:
                keys = list(raw_members)
                keys.append(tracking_key)
                await self.client.delete(*keys)
            else:
                await self.client.delete(tracking_key)
            _log("cache_delete", tracking_key, started)
        except Exception as exc:
            _log("cache_error", tracking_key, started, exc)

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            return None


def build_cache(settings) -> RedisCache | NoopCache:
    if not getattr(settings, "cache_enabled", True):
        return NoopCache()
    redis_url = getattr(settings, "redis_url", None)
    if not redis_url:
        return NoopCache()
    try:
        cache = RedisCache.from_url(redis_url)
        logger.info("Redis cache connected — %s", redis_url)
        return cache
    except Exception as exc:
        logger.warning("Redis cache disabled after initialization error: %s", exc)
        return NoopCache()


async def configure_redis_lru(client: Any, policy: str) -> None:
    try:
        await client.config_set("maxmemory-policy", policy)
    except Exception as exc:
        logger.warning("Redis LRU configuration skipped: %s", exc)


def _log(event: str, key: str, started: float, exc: Exception | None = None) -> None:
    duration_ms = int((time.perf_counter() - started) * 1000)
    extra = {
        "event": event,
        "namespace": _namespace(key),
        "duration_ms": duration_ms,
    }
    if exc is None:
        logger.debug("cache event", extra=extra)
    else:
        logger.warning(
            "cache error: %s",
            exc,
            extra={**extra, "error_type": exc.__class__.__name__},
        )


def _namespace(key: str) -> str:
    parts = key.split(":")
    if len(parts) >= 3 and parts[0] == "pa":
        return parts[2]
    return "cache"


# Namespaces whose cache keys are scoped to a specific thread.
# Key format: pa:v1:{namespace}:{safe_thread_id}[:{suffix}]
_THREAD_SCOPED_NAMESPACES = frozenset({
    "execution_summary",
    "execution_logs",
    "audit_events",
    "tool_errors",
})


def _thread_scope_from_key(key: str) -> str | None:
    """Extract the thread-scope identifier from a cache key, if any.

    Returns the safe-thread-id (``_key_part(thread_id)``) for thread-scoped
    namespaces, or ``None`` for global / unscoped keys.
    """
    parts = key.split(":")
    # pa:v1:execution_logs:thread-abc-123:500
    if len(parts) >= 4 and parts[0] == "pa" and parts[1] == "v1":
        if parts[2] in _THREAD_SCOPED_NAMESPACES:
            return parts[3]
    return None


def _cache_tracking_key(scope: str) -> str:
    """Per-thread Redis SET that tracks all cache keys for a thread scope."""
    return f"pa:v1:cache_keys:{scope}"
