# s12: Cache System（缓存系统）

`[ s12 ] s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"缓存是加速层，不是数据层。"* —— 用 Redis 缓存加速读操作，用 NoopCache 保证 Redis 挂了三系统照样跑。
>
> **Harness 层**: 性能 —— 在数据访问路径上加一层透明加速。

## 问题

Agent 运行期间有大量重复的读操作：

- **技能清单**：同一个 weather skill 的 SKILL.md 可能在一轮对话中被读取多次
- **Memory 查找**：反复查询同一线程的对话历史
- **工具结果**：同一个文件内容被多个 tool call 反复读取
- **执行日志**：频繁写入但偶尔查询的日志记录

每一次都是数据库请求、文件 I/O、或网络调用。读操作本身不贵，但**重复的读操
作**是浪费的根源——同样的请求发 10 次，9 次的结果和第一次一模一样。

缓存就是用来解决这个问题的：第一次算完存起来，后面 9 次直接从内存返回。
这就是 **Cache-Aside 模式**的本质。

## 解决方案

在数据访问路径上加一层透明的缓存抽象（`AsyncCache` protocol），业务代码按
protocol 编程，不依赖具体实现：

```
                     +-- HIT (1ms) → return
    cache.get(key) --+
                     +-- MISS → fetch from DB (50ms) → cache.set() → return

    没有 Redis？→ NoopCache（永远返回 MISS，系统功能完整，只是慢一点）
    有 Redis？ → RedisCache（连接池 + LRU + TTL，真正的加速）
```

**核心原则**：缓存是优化，不是功能。去掉缓存，系统应该**只变慢，不崩溃**。
这就是 NoopCache 存在的意义——它把缓存层做成透明的、可移除的。

## 工作原理

### 1. AsyncCache Protocol：定义接口，不绑定实现

```python
class AsyncCache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def close(self) -> None: ...
```

使用 `Protocol` 而非 ABC 的好处：
- **结构化子类型**（structural subtyping）：实现了同名方法就是合法的 AsyncCache
- **不需要显式依赖**：业务代码标注为 `cache: AsyncCache`，类型检查器判定
- **松耦合**：NoopCache 不 import AsyncCache 也能通过类型检查

真实项目的 protocol（`cache/base.py`）使用 `get_json`/`set_json`，因为 Redis 存
储的都是 JSON 字符串——用 json.dumps/json.loads 做透明的序列化/反序列化。

### 2. NoopCache：优雅降级

```python
class NoopCache:
    async def get(self, key: str) -> Any | None:
        return None  # 永远 MISS

    async def set(self, key: str, value: Any, ttl: int) -> None:
        return None  # 写入被丢弃

    async def exists(self, key: str) -> bool:
        return False  # 永远 False
```

NoopCache 不是"坏了才用"的 fallback，它是**架构中的一等公民**：

- 启动时：如果 Redis 不可达，`build_cache()` 自动返回 NoopCache，系统不报错
- 运行时：业务代码不需要 `if cache is not None` 检查——NoopCache 实现了完整接口
- 测试中：单元测试可以直接注入 NoopCache，不依赖 Redis 基础设施

### 3. SimpleCache：带 TTL 的 dict 实现

```python
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
```

课程中用 SimpleCache 作为 Redis 的 stand-in（不必启动 Redis 就能跑），它与
AsyncCache protocol 完全兼容。真实项目的 `RedisCache`（`cache/redis_cache.py`）
在接口上完全一致——只是底层从 `dict` 变成了 `redis.asyncio.Redis` 客户端。

### 4. TTL 策略：不同数据不同过期时间

| 数据类型 | TTL | 原因 |
|---------|-----|------|
| Memory（技能清单、工具结果） | 60s | 经常读取、偶尔写入；短暂一致性窗口可接受 |
| Logs（执行日志） | 5s | 频繁写入（每次 tool call 都写）、偶尔查询；短 TTL 避免读到过期数据 |
| Default | 10s | 折中默认值，适用于没有特殊要求的数据 |

TTL 本质上是**一致性与性能的权衡**：

- TTL 越长 → 命中率越高（越快），但可能返回过期数据（越不一致）
- TTL 越短 → 数据越新鲜（越一致），但命中率降低（越慢）

原则：**用最短的 TTL 满足业务的一致性要求**。

### 5. Cache-Aside Pattern：业务代码的标准写法

```python
async def get_skill_manifest(cache: AsyncCache, skill_name: str) -> dict:
    cache_key = f"skill:{skill_name}"

    # Step 1: 查缓存
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached  # 快路径：~1ms

    # Step 2: 缓存 miss，从数据源取
    manifest = db.query(f"SELECT * FROM skills WHERE name = ?", skill_name)

    # Step 3: 回填缓存
    await cache.set(cache_key, manifest, ttl=60)
    return manifest
```

这个模式之所以叫 "Cache-Aside"，是因为**缓存不是主角，数据源才是**。代码先
访问缓存，miss 时才走到数据源——缓存"在旁边"（aside），不取代数据源的位置。

### 6. build_cache() 工厂：根据环境自动选择实现

```python
def build_cache(url: str | None = None) -> AsyncCache:
    if url:
        return RedisCache.from_url(url)  # 连接成功 → 加速
    return NoopCache()                   # 不可用 → 降级
```

真实项目 `cache/redis_cache.py` 的 `build_cache(settings)` 还加了：
- `cache_enabled` 开关：可以在配置中全局关闭缓存（调试时用）
- try/except 保护：Redis 连接失败时不抛异常，返回 NoopCache
- LRU 配置：通过 `config_set("maxmemory-policy", ...)` 设置驱逐策略

### 7. SimpleCache vs RedisCache vs NoopCache

| | SimpleCache（本课程） | RedisCache（真实项目） | NoopCache |
|---|---|---|---|
| 存储 | Python dict | Redis 服务端 | 无 |
| 持久化 | 无（进程重启丢失） | 有（AOF/RDB） | 无 |
| 过期策略 | 惰性删除（get 时检查） | 惰性 + 定期 + 主动 expire | 无 |
| 连接池 | 不需要 | redis.asyncio 连接池 | 不需要 |
| 性能 | ~0.01ms（内存） | ~0.5ms（网络 + 序列化） | ~0ms（什么都不做） |
| 适用场景 | 本地开发、测试、演示 | 生产环境 | 降级模式、无 Redis 环境 |

## 变更内容

| 组件 | 之前（s11） | 之后（s12） |
|------|-----------|------------|
| 数据访问 | 每次都查 DB / 读文件 | Cache-Aside：先查缓存 |
| 读延迟 | 50-200ms（I/O） | 命中时 < 1ms |
| 依赖 | 直接依赖数据源 | 依赖 AsyncCache protocol |
| Redis 不可用时 | 无此概念 | NoopCache 自动降级 |
| TTL | 无 | 按数据类型分三档 |

## 试一试

```sh
cd course
python s12_cache/code.py
```

观察四个 Demo 的输出：

1. **Cache-Aside 加速**：第一次调用走慢路径（~50ms），第二次命中缓存（< 1ms）
2. **TTL 过期**：写入 1 秒 TTL 的值，等 1.5 秒后读取——返回 None（惰性删除）
3. **NoopCache 降级**：所有操作静默返回空值，系统不报错
4. **TTL 策略**：三种数据类型分别设置 60s / 5s / 10s 的过期时间

## 扩展思考

**In-Process vs Out-of-Process 缓存**：

本课程的 SimpleCache 是 in-process（dict 在进程内存中）。真实项目的 RedisCache
是 out-of-process（Redis 是独立服务）。

in-process 的优势：零网络延迟，极快。劣势：多进程/多实例不共享，重启丢失。
out-of-process 的优势：多实例共享，数据持久。劣势：网络延迟 ~0.5ms。

生产环境通常两者都用：进程内 LRU 做 L1 缓存（热点数据），Redis 做 L2 缓存
（共享数据，跨实例一致）。

## 与前后章节的关系

- **s11（Checkpoint）**：LangGraph checkpoint 本身的序列化/持久化；s12 缓存的是
  agent 层面的业务数据（技能清单、记忆、工具结果）
- **s15（Observability）**：Redis cache hit/miss rate 是重要的观测指标——
  高 miss rate 说明 TTL 过短或 key pattern 有问题

## 下一步

[s13: Agent Memory](../s13_agent_memory/) —— 从缓存加速到持久化记忆。记忆系统内部
也使用 s12 的缓存层：频繁访问的会话历史从 Redis 缓存读取，miss 时才查 PostgreSQL。

---

**源码参考**:
`backend/src/personal_assistant/cache/base.py`（36 行）—— AsyncCache protocol + NoopCache
`backend/src/personal_assistant/cache/redis_cache.py`（111 行）—— RedisCache + build_cache 工厂
