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
        return RedisCache.from_url(redis_url)
    except Exception as exc:
        logger.warning("Redis cache disabled after initialization error: %s", exc)
        return NoopCache()


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
