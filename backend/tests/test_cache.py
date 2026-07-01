from personal_assistant.cache import NoopCache
from personal_assistant.cache.redis_cache import RedisCache, build_cache, configure_redis_lru
from personal_assistant.config import Settings


async def test_noop_cache_always_misses_and_ignores_writes() -> None:
    cache = NoopCache()

    await cache.set_json("key", {"value": 1}, ttl_seconds=10)

    assert await cache.get_json("key") is None
    await cache.delete("key")
    await cache.delete_pattern("key:*")
    await cache.close()


def test_build_cache_returns_noop_when_cache_disabled() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        CACHE_ENABLED=False,
        REDIS_URL="redis://redis.example.local:6379/0",
        _env_file=None,
    )

    assert isinstance(build_cache(settings), NoopCache)


def test_build_cache_returns_noop_without_redis_url() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        CACHE_ENABLED=True,
        _env_file=None,
    )

    assert isinstance(build_cache(settings), NoopCache)


class FailingRedisClient:
    async def get(self, key):
        raise RuntimeError("redis down")

    async def set(self, key, value, ex=None):
        raise RuntimeError("redis down")

    async def delete(self, *keys):
        raise RuntimeError("redis down")

    async def scan_iter(self, match):
        raise RuntimeError("redis down")
        yield ""

    async def aclose(self):
        raise RuntimeError("redis down")


async def test_redis_cache_methods_swallow_client_errors() -> None:
    cache = RedisCache(FailingRedisClient())

    assert await cache.get_json("key") is None
    await cache.set_json("key", {"value": 1}, ttl_seconds=10)
    await cache.delete("key")
    await cache.delete_pattern("key:*")
    await cache.close()


class ConfigurableRedisClient:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.config_calls = []

    async def config_set(self, name, value):
        self.config_calls.append((name, value))
        if self.fail:
            raise RuntimeError("config disabled")


async def test_configure_redis_lru_sets_policy_best_effort() -> None:
    client = ConfigurableRedisClient()

    await configure_redis_lru(client, "allkeys-lru")

    assert client.config_calls == [("maxmemory-policy", "allkeys-lru")]


async def test_configure_redis_lru_swallows_provider_errors() -> None:
    client = ConfigurableRedisClient(fail=True)

    await configure_redis_lru(client, "allkeys-lru")

    assert client.config_calls == [("maxmemory-policy", "allkeys-lru")]
