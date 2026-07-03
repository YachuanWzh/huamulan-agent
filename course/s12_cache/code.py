#!/usr/bin/env python3
"""
s12_cache.py — Cache System: AsyncCache protocol + NoopCache fallback + SimpleCache

缓存层 = 性能加速器，不是数据源。
    check cache → hit? → return (fast path, ~1ms)
             ↓ miss
    fetch from source → write cache → return (slow path, ~50ms+)

Cache-aside 模式在每个读操作中内联缓存的查-写逻辑。
NoopCache 保证了 graceful degradation —— Redis 挂了系统照样跑。

Usage:
    python s12_cache/code.py

Reference source: backend/src/personal_assistant/cache/redis_cache.py
                  backend/src/personal_assistant/cache/base.py
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, runtime_checkable


# ── AsyncCache Protocol ───────────────────────────────────────────
# 缓存抽象：任何实现这组方法的类都是合法的 AsyncCache。
# Protocol 不需要显式继承——只要实现了同名方法即可通过类型检查。
@runtime_checkable
class AsyncCache(Protocol):
    """异步缓存协议。定义缓存的最小接口，让 RedisCache / NoopCache
    可以在不改业务代码的前提下互相替换。"""

    async def get(self, key: str) -> Any | None:
        """读取缓存值。key 不存在时返回 None。"""
        ...

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """写入缓存，ttl 单位为秒。"""
        ...

    async def delete(self, key: str) -> None:
        """删除指定 key。"""
        ...

    async def exists(self, key: str) -> bool:
        """检查 key 是否存在（且未过期）。"""
        ...

    async def close(self) -> None:
        """释放连接等资源。"""
        ...


# ── NoopCache ─────────────────────────────────────────────────────
# Graceful Degradation：当 Redis 不可用时，缓存层退化为"不做任何事"。
# 核心原则：缓存是加速层，不是数据层。缓存挂了，系统慢一点但不能挂。
class NoopCache:
    """无操作缓存实现。所有方法都是空操作，保证系统在 Redis
    不可用时继续正常运行——只是没有缓存加速而已。"""

    async def get(self, key: str) -> Any | None:
        return None  # 永远 miss，业务层走慢路径

    async def set(self, key: str, value: Any, ttl: int) -> None:
        return None  # 写入被忽略

    async def delete(self, key: str) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return False  # 永远不存在

    async def close(self) -> None:
        return None


# ── SimpleCache ───────────────────────────────────────────────────
# 基于 dict 的内存缓存，带 TTL 支持。用于演示和测试。
# 在真实项目中这里是 RedisCache（基于 redis.asyncio，支持连接池、
# LRU 驱逐、持久化）。SimpleCache 保留了相同的 AsyncCache 接口，
# 让代码可以不做任何修改地从 SimpleCache 迁移到 RedisCache。
class SimpleCache:
    """基于 Python dict 的简单内存缓存实现。每个 key 附带过期时间戳，
    读取时检查是否过期。"""

    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            # TTL 到期，惰性删除
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        expires_at = time.monotonic() + ttl
        self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        _, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return False
        return True

    async def close(self) -> None:
        self._store.clear()


# ── TTL 策略 ──────────────────────────────────────────────────────
# 不同数据类型的 TTL 不同，避免"一刀切"：
#   MEMORY_TTL  = 60s  # 经常读取、偶尔写入（tool call results, skill manifests）
#   LOG_TTL     = 5s   # 频繁写入（execution logs）
#   DEFAULT_TTL = 10s  # 折中默认值
MEMORY_TTL = 60
LOG_TTL = 5
DEFAULT_TTL = 10


# ── build_cache 工厂 ──────────────────────────────────────────────
def build_cache(url: str | None = None) -> AsyncCache:
    """创建缓存实例的工厂函数。

    如果提供了 redis URL，尝试连接 Redis；否则返回 SimpleCache。
    真实项目中这里返回 RedisCache 或 NoopCache：
      - 有 REDIS_URL → RedisCache.from_url(url)
      - 无 REDIS_URL 或连接失败 → NoopCache()
    """
    if url:
        # 在真实项目中：return RedisCache.from_url(url)
        # 本课程用 SimpleCache 作为 Redis 的 stand-in
        print(f"[build_cache] Redis URL provided ({url}), "
              f"使用 SimpleCache 作为 stand-in")
    else:
        print("[build_cache] No URL — 使用 SimpleCache")
    return SimpleCache()


# ── Cache-Aside Pattern ───────────────────────────────────────────
# 这是缓存最常见的使用模式：先查缓存，miss 则计算并回填。
#
#   async def cached_operation(cache: AsyncCache, key: str) -> Any:
#       # Step 1: try cache
#       value = await cache.get(key)
#       if value is not None:
#           return value  # < 1ms, fast path
#
#       # Step 2: cache miss → fetch from source
#       value = expensive_computation(key)
#
#       # Step 3: write back to cache
#       await cache.set(key, value, ttl=DEFAULT_TTL)
#       return value
#


# ── 模拟数据源 ────────────────────────────────────────────────────
def _expensive_db_lookup(skill_name: str) -> dict:
    """模拟昂贵的数据库查询——读取技能清单。
    每次调用 sleep 50ms，模拟一次 I/O 操作。"""
    time.sleep(0.05)  # 模拟 50ms I/O
    return {
        "name": skill_name,
        "loaded": True,
        "instructions": f"## {skill_name}\n\nSkill instructions here...",
        "triggers": [skill_name.lower()],
    }


async def get_skill_manifest(cache: AsyncCache, skill_name: str) -> dict:
    """Cache-aside 模式获取技能清单。先查缓存，miss 再查 DB。"""
    cache_key = f"skill:{skill_name}"

    # Step 1: 尝试从缓存读取
    t0 = time.perf_counter()
    cached = await cache.get(cache_key)
    if cached is not None:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"  [HIT]  {cache_key}  ({elapsed_ms:.1f}ms)")
        return cached

    # Step 2: Cache miss → 从数据源获取
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  [MISS] {cache_key}  ({elapsed_ms:.1f}ms) → fetching from DB...")
    manifest = _expensive_db_lookup(skill_name)

    # Step 3: 回填缓存（不同类型用不同 TTL）
    ttl = MEMORY_TTL  # 技能清单属于 memory 类型，TTL 60s
    await cache.set(cache_key, manifest, ttl)
    print(f"  [SET]  {cache_key}  (ttl={ttl}s)")
    return manifest


# ── Demo：缓存加速效果 ────────────────────────────────────────────
async def demo_cache_acceleration():
    """演示缓存的加速效果：第一次访问走慢路径（查 DB），
    后续访问走快路径（命中缓存）。"""
    print("=" * 60)
    print("Demo 1: Cache-Aside 加速效果")
    print("=" * 60)

    cache = build_cache()
    skill = "weather"

    print("\n--- First call (cache miss → DB lookup) ---")
    t0 = time.perf_counter()
    result = await get_skill_manifest(cache, skill)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  Result: {result['name']} (总耗时 {elapsed:.1f}ms)\n")

    print("--- Second call (cache hit → instant) ---")
    t0 = time.perf_counter()
    result = await get_skill_manifest(cache, skill)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  Result: {result['name']} (总耗时 {elapsed:.1f}ms)\n")


# ── Demo：TTL 过期 ────────────────────────────────────────────────
async def demo_ttl_expiration():
    """演示 TTL 过期：写入一个只有 1 秒 TTL 的值，然后看到它过期。"""
    print("=" * 60)
    print("Demo 2: TTL 过期机制")
    print("=" * 60)

    cache = SimpleCache()
    key = "temp:token"

    await cache.set(key, "secret-token-value", ttl=1)
    print(f"SET {key} = 'secret-token-value' (ttl=1s)")

    exists = await cache.exists(key)
    print(f"exists({key}) = {exists}  ← 刚写入，应该 True")

    value = await cache.get(key)
    print(f"get({key})   = {value}")

    print("Waiting 1.5s for TTL to expire...")
    await asyncio.sleep(1.5)

    exists = await cache.exists(key)
    print(f"exists({key}) = {exists}  ← TTL 到期，惰性删除后为 False")

    value = await cache.get(key)
    print(f"get({key})   = {value}  ← 过期返回 None\n")


# ── Demo: NoopCache graceful degradation ──────────────────────────
async def demo_noop_graceful():
    """演示 NoopCache 的优雅降级：所有操作静默返回空值，
    系统正常运行，只是没有缓存加速。"""
    print("=" * 60)
    print("Demo 3: NoopCache Graceful Degradation")
    print("=" * 60)

    cache = NoopCache()

    await cache.set("any:key", "some value", ttl=60)
    print(f"set('any:key', 'some value') → 被忽略")

    value = await cache.get("any:key")
    print(f"get('any:key') → {value}  ← 永远返回 None（cache miss）")

    exists = await cache.exists("any:key")
    print(f"exists('any:key') → {exists}  ← 永远 False")

    print("\nNoopCache 语义：缓存层完全透明，业务代码不需要 if cache is None 检查。\n")


# ── Demo: 不同 TTL 策略 ───────────────────────────────────────────
async def demo_ttl_strategies():
    """演示不同数据类型的 TTL 策略。"""
    print("=" * 60)
    print("Demo 4: TTL 策略 — 不同数据不同 TTL")
    print("=" * 60)

    cache = SimpleCache()

    # Memory 类型（技能清单）：60s TTL
    await cache.set("mem:skill", {"name": "weather"}, ttl=MEMORY_TTL)
    # Log 类型（执行日志）：5s TTL
    await cache.set("log:exec-1", "tool call: bash ls", ttl=LOG_TTL)
    # Default 类型：10s TTL
    await cache.set("default:config", {"theme": "dark"}, ttl=DEFAULT_TTL)

    for key, expected_ttl in [
        ("mem:skill", MEMORY_TTL),
        ("log:exec-1", LOG_TTL),
        ("default:config", DEFAULT_TTL),
    ]:
        entry = cache._store[key]
        remaining = entry[1] - time.monotonic()
        print(f"  {key}: value={entry[0]}, ttl={expected_ttl}s, remaining={remaining:.1f}s")

    print("\n原则：用最短的 TTL 满足一致性要求，用长 TTL 时接受最终一致性。\n")
    await cache.close()


# ── Entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("s12: Cache System — AsyncCache protocol + NoopCache + Cache-Aside\n")
    asyncio.run(demo_cache_acceleration())
    asyncio.run(demo_ttl_expiration())
    asyncio.run(demo_noop_graceful())
    asyncio.run(demo_ttl_strategies())
    print("All demos complete.")
