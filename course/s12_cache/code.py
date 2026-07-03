#!/usr/bin/env python3
"""
s12_cache.py — Cache System: AsyncCache protocol + NoopCache + Cache-Aside

缓存是加速层，不是数据层。Redis 不可用时通过 NoopCache 优雅降级。

    check cache → hit? → return (fast, <1ms)
             ↓ miss
    fetch from DB → write cache → return (slow, ~50ms)

Reference: backend/src/personal_assistant/cache/redis_cache.py
           backend/src/personal_assistant/cache/base.py

Usage:
    python s12_cache/code.py
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol


# ── AsyncCache Protocol ───────────────────────────────────────────
# Protocol 是"结构化子类型"：实现了 get/set/delete 就是 AsyncCache，
# 不需要显式继承。这让 NoopCache 完全解耦于 protocol 定义。
class AsyncCache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def close(self) -> None: ...


# ── NoopCache：Graceful Degradation ───────────────────────────────
# 缓存是优化，不是功能。NoopCache 确保 Redis 挂了系统不挂。
class NoopCache:
    async def get(self, key: str) -> Any | None: return None
    async def set(self, key: str, value: Any, ttl: int) -> None: return None
    async def delete(self, key: str) -> None: return None
    async def exists(self, key: str) -> bool: return False
    async def close(self) -> None: return None


# ── SimpleCache：dict 实现，带 TTL ────────────────────────────────
# 生产环境这里换成 RedisCache（redis.asyncio + 连接池 + LRU驱逐）。
# 接口相同，不做任何修改即可替换。
class SimpleCache:
    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]  # 惰性删除
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        _, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]; return False
        return True

    async def close(self) -> None:
        self._store.clear()


# ── TTL 策略 ──────────────────────────────────────────────────────
# 不同类型数据用不同 TTL，避免"一刀切"
MEMORY_TTL  = 60   # 技能清单、工具结果：常读偶写
LOG_TTL     = 5    # 执行日志：频繁写入
DEFAULT_TTL = 10   # 默认


# ── build_cache 工厂 ──────────────────────────────────────────────
# 真实项目：有 REDIS_URL → RedisCache；无 → NoopCache（不抛异常）
def build_cache(url: str | None = None) -> AsyncCache:
    if url:
        print(f"[build_cache] REDIS_URL={url} — 真实项目"
              f"这里连接 Redis；本课程用 SimpleCache 演示")
    else:
        print("[build_cache] 无 REDIS_URL — 使用 SimpleCache")
    return SimpleCache()


# ── Cache-Aside Pattern ───────────────────────────────────────────
# 先查缓存，miss 则计算，再回填。缓存"在旁边"，数据源才是真身。
async def cached_lookup(cache: AsyncCache, skill_name: str) -> dict:
    """用 Cache-Aside 模式获取技能清单。"""
    key = f"skill:{skill_name}"

    # Step 1: 查缓存
    t0 = time.perf_counter()
    cached = await cache.get(key)
    if cached is not None:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  [HIT]  {key} ({elapsed:.1f}ms) → instant return")
        return cached

    # Step 2: Cache miss → 查数据源
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  [MISS] {key} ({elapsed:.1f}ms) → fetching from DB...")
    time.sleep(0.05)  # 模拟 50ms DB I/O
    manifest = {"name": skill_name, "instructions": f"## {skill_name}\n..."}

    # Step 3: 回填缓存
    await cache.set(key, manifest, ttl=MEMORY_TTL)
    print(f"  [SET]  {key} (ttl={MEMORY_TTL}s)")
    return manifest


# ── Demo ──────────────────────────────────────────────────────────
async def main():
    print("s12: Cache System — AsyncCache + NoopCache + Cache-Aside\n")

    # Demo 1: Cache-Aside 加速效果
    print("=" * 55)
    print("Demo 1: 第一次 MISS（走 DB），第二次 HIT（走缓存）")
    print("=" * 55)
    cache = build_cache()
    t0 = time.perf_counter()
    await cached_lookup(cache, "weather")
    print(f"  总耗时: {(time.perf_counter() - t0) * 1000:.0f}ms (MISS path)\n")
    t0 = time.perf_counter()
    await cached_lookup(cache, "weather")
    print(f"  总耗时: {(time.perf_counter() - t0) * 1000:.0f}ms (HIT path)\n")

    # Demo 2: TTL 过期
    print("=" * 55)
    print("Demo 2: TTL 过期 — 写入 1s 后惰性删除")
    print("=" * 55)
    sc = SimpleCache()
    await sc.set("temp:token", "secret-abc", ttl=1)
    print(f"exists(temp:token) = {await sc.exists('temp:token')}  ← True")
    print(f"get(temp:token)   = {await sc.get('temp:token')}")
    print("等待 1.5s ...")
    await asyncio.sleep(1.5)
    print(f"exists(temp:token) = {await sc.exists('temp:token')}  ← False (过期)")
    print(f"get(temp:token)   = {await sc.get('temp:token')}  ← None\n")

    # Demo 3: NoopCache 优雅降级
    print("=" * 55)
    print("Demo 3: NoopCache — Redis 不可用时静默降级")
    print("=" * 55)
    nc = NoopCache()
    await nc.set("x", "y", 60)
    print(f"set('x','y') 后被 get('x') = {await nc.get('x')}  ← 永远 None")
    print(f"exists('x') = {await nc.exists('x')}  ← 永远 False")
    print("系统正常运行，只是没有缓存加速。\n")

    # Demo 4: 不同 TTL 策略
    print("=" * 55)
    print("Demo 4: TTL 策略 — 内存 60s / 日志 5s / 默认 10s")
    print("=" * 55)
    sc2 = SimpleCache()
    await sc2.set("mem:skill", {"name": "weather"}, MEMORY_TTL)
    await sc2.set("log:exec-1", "bash ls", LOG_TTL)
    await sc2.set("cfg:theme", "dark", DEFAULT_TTL)
    for key, expected in [("mem:skill", MEMORY_TTL),
                          ("log:exec-1", LOG_TTL),
                          ("cfg:theme", DEFAULT_TTL)]:
        entry = sc2._store[key]
        remaining = entry[1] - time.monotonic()
        print(f"  {key}: ttl={expected}s, remaining={remaining:.1f}s")
    await sc2.close()
    await cache.close()
    print()


if __name__ == "__main__":
    asyncio.run(main())
