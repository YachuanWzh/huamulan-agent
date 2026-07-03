# s11: Checkpoint（状态持久化）

`[ s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 ] s12 > s13 > s14 > s15 > s16`

> *"Every superstep leaves a footprint"* -- LangGraph 在每个节点后自动快照状态，让 agent 可以暂停、恢复、回放。
>
> **Harness 层**: 状态持久化 -- agent 可靠性的基石。

## 问题

Agent 执行可能横跨数十个工具调用和 LLM 轮次，耗时数分钟。如果中途崩溃（网络断开、
进程 OOM、API 限流），没有 checkpoint 就意味着所有上下文丢失，用户必须从头开始。

更进一步的场景：
- **审批暂停**: agent 在执行危险操作前需要等待人工批准，此时必须挂起并持久化状态
- **审计回放**: 需要追溯"agent 在某一刻看到了什么、决定了什么"
- **会话恢复**: 用户关闭浏览器后重新打开，对话应该从断点继续

手写 while 循环需要自己实现所有这些 -- 序列化状态、存数据库、恢复逻辑。
LangGraph 内置的 checkpoint 体系让这些成为标准功能。

## 解决方案

```
每个 superstep 后:
┌──────────────────────────────────────────────────────────┐
│  [agent_node] ──► [checkpoint save] ──► [tools_node]     │
│       ▲                                        │          │
│       └──────── tool_result ──────────────────┘          │
│                                                          │
│  快照内容:                                                │
│  - 完整的 AgentState（messages, turn, ...）               │
│  - 当前节点位置 + 待处理的 channel 写入                    │
│  - checkpoint_id（UUID）+ parent_checkpoint_id（链表）     │
│  - metadata（source, step, writes）                      │
└──────────────────────────────────────────────────────────┘
```

LangGraph 在 `app.compile(checkpointer=...)` 时接收一个 checkpointer，
之后每次 `app.invoke(state, config)` 自动完成：

1. **读取**: 用 `config["configurable"]["thread_id"]` 查找该会话的最新快照
2. **恢复**: 若存在快照，从断点继续执行（消息历史自动拼接）
3. **写入**: 每个 superstep 结束后自动保存新快照
4. **链接**: 新快照的 `parent_checkpoint_id` 指向上一个快照，形成历史链

### Checkpointer 后端

| 后端 | 场景 | 持久化 | 性能 |
|------|------|--------|------|
| `MemorySaver` | 开发/测试 | 进程重启丢失 | 最快 |
| `SqliteSaver` | 本地单机 | 文件持久化 | 快 |
| `PostgresSaver` | 生产环境 | 数据库持久化 | 中等 |
| `RedisSaver` | 加速层 | TTL 过期 | 极快 |

### Redis-first 模式（langgraph-claw 生产方案）

```
[Agent 执行完成]
       │
       ▼
┌──────────┐    同步写入 (~1ms)                    ┌────────────┐
│  Redis   │ ◄── 关键路径（阻塞 agent）             │ PostgreSQL │
│ (cache)  │                                       │ (durable)  │
└────┬─────┘    异步归档 (background task)          └─────┬──────┘
     │                                                   │
     │  TTL 7 天自动过期                                  │  永久存储
     │  读取优先命中 Redis                                 │  审计查询
     │                                                   │
     └───────────────────────────────────────────────────┘
```

**为什么需要 Redis-first？**

Checkpoint 写入在 agent 的关键路径上 -- 每次 LLM 调用后，agent 必须等
checkpoint 写完才能继续执行下一个节点。PostgreSQL 写入延迟通常 10-50ms，
而 Redis 仅约 1ms。在包含数十个工具调用的长对话中，这个差异会被放大。

langgraph-claw 的 `RedisFirstCheckpointSaver` 实现：
- `aput()`: 同步写入 Redis（快速），然后 `asyncio.create_task()` 异步归档到 PG
- `aget_tuple()`: 优先从 Redis 读取，miss 时回退 PG
- `alist()`: Redis sorted set 按时间排序，支持分页
- `drain()`: shutdown 时等待未完成的归档任务
- TTL 自动清理：Redis 中的旧快照 7 天自动过期，PG 侧也有定时清理

参考实现: `backend/src/personal_assistant/checkpoint/redis_first.py`
调用入口: `backend/src/personal_assistant/memory/postgres.py` (`PostgresMemory._build_checkpointer`)

## 工作原理

### 1. thread_id: 会话标识

```python
config = {"configurable": {"thread_id": "session-123"}}
app.invoke({"messages": [HumanMessage(content="Hello")]}, config)
# ... 稍后 ...
app.invoke({"messages": [HumanMessage(content="What did I just say?")]}, config)
# ↑ 同一个 thread_id → LangGraph 自动从上次的快照恢复
```

`thread_id` 是 checkpoint 体系的核心。它决定了"哪一段对话"。
在 langgraph-claw 中，`PostgresMemory` 管理所有 thread 的生命周期：
`list_threads()`, `delete_thread()`, `replay()`.

### 2. 自动快照

每次 `app.invoke()` 完成后，LangGraph 自动调用 checkpointer 的 `aput()` 方法。
这个过程对业务代码完全透明 -- 你不需要手动保存任何东西。

### 3. 查看历史

```python
# 列出某个 thread 的所有 checkpoint
for snapshot in app.get_state_history(config):
    print(snapshot.config["configurable"]["checkpoint_id"])
    print(snapshot.values["turn"])
```

### 4. 回放到任意点

```python
# 回到第一个 checkpoint
replay_config = {
    "configurable": {
        "thread_id": "session-123",
        "checkpoint_id": first_checkpoint_id,
    }
}
state = app.get_state(replay_config)
```

## 变更内容

| 组件 | 之前 (s10) | 之后 (s11) |
|------|-----------|-----------|
| Graph 编译 | `graph.compile()` | `graph.compile(checkpointer=MemorySaver())` |
| 调用方式 | `app.invoke(state)` | `app.invoke(state, config)` |
| 状态持久化 | 无 | 每个 superstep 后自动快照 |
| 会话隔离 | 无 | `thread_id` 区分不同对话 |
| 历史回放 | 无 | `get_state_history()` + `get_state()` |
| 崩溃恢复 | 不支持 | 从最新 checkpoint 自动恢复 |

## 试一试

```sh
cd course
python s11_checkpoint/code.py
```

观察输出中的 checkpoint 列表和 thread 隔离效果。

交互模式中可以：
1. 发送几条消息，累积 checkpoint
2. 输入 `h` 查看所有 checkpoint 历史
3. 体验同一 thread 的自动恢复

关键观察点：
- 同一个 `thread_id` 的多轮对话，消息历史自动拼接
- 不同 `thread_id` 之间完全隔离
- 每个 superstep（每次 LLM 调用）都产生一个新的 checkpoint

## 下一步

[s12: Interrupt & Approval](../s12_interrupt_approval/) -- 基于 checkpoint 机制实现
人工审批：在危险操作前暂停 agent，等待用户确认后从断点恢复执行。
