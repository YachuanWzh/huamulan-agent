# s13: API Server（API 服务）

`[ s13 ] s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 > s14 > s15 > s16`

> *"Agent 不再活在终端里"* —— 用 FastAPI 把 Agent 引擎包装成 Web 服务，
> 让前端、移动端、第三方系统都能调用。
>
> **Harness 层**: API —— Agent 引擎与 UI 的解耦桥梁。

## 问题

s01 到 s12 的 Agent 一直在命令行里运行——`python code.py` 启动，
`input()` 读用户指令，`print()` 输出结果。这在小规模实验和调试中没问题，
但离真实产品还有关键的距离：

1. **前端需要 HTTP 接口**：Web 页面不能调用 `input()`，它只能发 HTTP 请求
2. **多前端复用**：同一个 Agent 引擎要同时服务 Web、CLI、IDE 插件、Slack bot
3. **流式体验**：用户不想等 Agent 全部执行完才看到结果——需要逐 token 推送
4. **会话管理**：多个用户、多个对话并发，每个对话独立状态

你需要一层 HTTP 壳把 Agent 引擎包起来，暴露标准的 REST + SSE 接口。
这就是 API Server 的职责。

## 解决方案

在 Agent 引擎外面套一层 FastAPI，物理架构如下：

```
┌──────────┐    HTTP/SSE     ┌──────────────┐    Python call   ┌──────────────┐
│  Browser │ <─────────────> │  API Server  │ <──────────────> │ Agent Engine │
│ (React)  │                 │  (FastAPI)   │                  │ (LangGraph)  │
└──────────┘                 └──────────────┘                  └──────────────┘
  前端只懂 HTTP                 API 层做协议转换                 引擎只管图执行
```

API Server 的三件事：

1. **协议转换**：HTTP request → `agent.invoke()` / `agent.astream_events()`
2. **流式推送**：Graph 的每个事件 → 一条 SSE 消息 → 前端实时渲染
3. **会话隔离**：每个 `thread_id` 对应一个独立的 LangGraph 对话

注意：API Server **不包含任何业务逻辑**。它不调 LLM、不执行工具、不做路由。
它只是一个薄薄的外壳——把 HTTP 世界和 Graph 世界连接起来。

## 端点设计

| 方法 | 路径 | 用途 | 响应类型 |
|------|------|------|---------|
| `GET` | `/health` | K8s/Docker 健康探活 | JSON |
| `POST` | `/chat` | 非流式聊天（一次请求一次响应） | JSON |
| `POST` | `/chat/stream` | SSE 流式聊天（逐 token 推送） | SSE |
| `POST` | `/approve` | 批准/拒绝待审工具调用 | JSON |
| `GET` | `/threads` | 列出所有会话 | JSON |
| `DELETE` | `/threads/{id}` | 删除指定会话 | JSON |

真实项目 `api/server.py` 有约 14 个端点（技能管理、评估、审计……），
本章只展示核心的 3 个。另外 11 个是前三个模式的重复应用——理解了核心模式，
其余的只是加一行路由。

### code.py 中的三个端点

- **`POST /chat`**（非流式）：`app_graph.invoke()` 同步执行整个 agent loop，
  返回最终响应。等价于 s01 中 `app.invoke()` 的用法。
- **`POST /chat/stream`**（流式）：`app_graph.astream_events()` 遍历事件，
  每个事件转换为一条 SSE 消息推送给客户端。
- **`GET /health`**：返回 `{"status": "ok"}`，K8s liveness probe 使用。

## SSE 流式机制（本章核心）

### 什么是 SSE

Server-Sent Events 是 HTTP 的长连接推送协议——比 WebSocket 更简单：

- **单向**：服务器推 → 客户端收（不需要双向，聊天场景够用）
- **文本**：纯文本协议，基于 HTTP，不需要升级连接
- **自动重连**：浏览器 `EventSource` API 内置断线重连
- **防火墙友好**：就是普通 HTTP，不需要特殊端口

### 一条 SSE 消息的格式

```
event: token
data: {"content": "Hello"}

event: tool_call
data: {"name": "bash", "status": "started", "input": {"command": "ls"}}

event: done
data: {"status": "completed"}
```

规则：
- `event:` 行是可选的，标识事件类型
- `data:` 行是 JSON 载荷
- 每条消息以空行 `\n\n` 结尾（协议规定的分隔符）
- 客户端用 `EventSource.addEventListener('token', ...)` 按事件类型订阅

### 流式数据流

```
客户端                          API Server                     LangGraph
  │                                │                              │
  │  POST /chat/stream             │                              │
  │  {thread_id, message}          │                              │
  │ ─────────────────────────────> │                              │
  │                                │  app_graph.astream_events()  │
  │                                │ ───────────────────────────> │
  │                                │                              │
  │                                │    on_chat_model_stream     │
  │                                │ <── token: "Hello" ──────────│
  │  event: token                  │                              │
  │  data: {"content":"Hello"}     │                              │
  │ <───────────────────────────── │                              │
  │                                │                              │
  │                                │    on_chat_model_stream     │
  │                                │ <── token: " world" ─────────│
  │  event: token                  │                              │
  │  data: {"content":" world"}    │                              │
  │ <───────────────────────────── │                              │
  │                                │                              │
  │                                │    on_tool_start            │
  │                                │ <── name: "bash" ────────────│
  │  event: tool_call              │                              │
  │  data: {"name":"bash",...}     │                              │
  │ <───────────────────────────── │                              │
  │                                │                              │
  │                                │    on_tool_end              │
  │                                │ <── output: "..." ───────────│
  │  event: tool_call              │                              │
  │  data: {"name":"bash",...}     │                              │
  │ <───────────────────────────── │                              │
  │                                │                              │
  │                                │    stream ends              │
  │  event: done                   │                              │
  │  data: {"status":"completed"}  │                              │
  │ <───────────────────────────── │                              │
```

### 核心代码：从 Graph 事件到 SSE 消息

```python
async def event_stream():
    config = {"configurable": {"thread_id": request.thread_id}}
    async for event in app_graph.astream_events(
        {"messages": [HumanMessage(content=request.message)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]

        if kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if chunk.content:
                yield _sse_event("token", {"content": chunk.content})

        elif kind == "on_tool_start":
            yield _sse_event("tool_call", {
                "name": event.get("name", "unknown"),
                "status": "started",
            })

        elif kind == "on_tool_end":
            yield _sse_event("tool_call", {
                "name": event.get("name", "unknown"),
                "status": "completed",
            })

    yield _sse_event("done", {"status": "completed"})
```

关键设计：
- `astream_events(version="v2")` 是 LangGraph 的流式 API——每发生一个事件就 yield 一次
- 生成器函数本身是异步的，服务可以同时处理多个流式连接
- 真实项目还在 `on_chain_start/end` 上发了 `compacting` 事件（s09 上下文压缩）
- `StreamingResponse` 的三个 HTTP 头：
  - `Cache-Control: no-cache`：告诉代理不要缓存 SSE 流
  - `Connection: keep-alive`：维持长连接
  - `X-Accel-Buffering: no`：禁用 Nginx 缓冲（避免事件被延迟）

### SSE vs WebSocket

| | SSE | WebSocket |
|---|---|---|
| 方向 | 服务器 → 客户端 | 双向 |
| 协议 | HTTP | ws://（需升级） |
| 自动重连 | 内置 | 需自己实现 |
| 客户端 API | `EventSource` | 需要额外库 |
| 代理兼容 | 好（普通 HTTP） | 部分代理不支持 |
| 适合场景 | 单向推送（聊天流） | 双向实时（游戏、协作） |

聊天场景选 SSE 而不是 WebSocket 的理由：**Agent 响应是单向的**——客户端发送
用户消息到 `/chat/stream` 后，接下来的数据流完全是服务端 → 客户端。不需要双向
通信。SSE 更简单，代理兼容性更好。

## 会话管理

LangGraph 的内置 checkpoint 机制天然支持会话隔离。每个请求携带 `thread_id`，
LangGraph 用 `configurable.thread_id` 查找该会话的状态快照：

```
POST /chat/stream  {thread_id: "conv-1", message: "Hello"}
  → LangGraph 查找 checkpoint for "conv-1"
  → 如果有历史状态，从上次节点继续
  → 执行新的 agent loop
  → 保存新状态到 "conv-1" 的 checkpoint
```

这意味着同一个 `thread_id` 的多次请求共享上下文——第二次请求能"看到"第一次
请求做了什么事。不同 `thread_id` 完全隔离。

真实项目通过 `api/server.py` 的 `GET /threads` 和 `DELETE /threads/{id}`
管理会话列表和清理。Redis checkpoint（s11）让这场会话管理即便在高并发下
也能保持高性能。

## 前端如何使用

```javascript
// 连接 SSE 流
const response = await fetch('/chat/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({thread_id: 'conv-1', message: 'List all python files'}),
});

// 读取流
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
    const {done, value} = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, {stream: true});

    // 按 \n\n 分割 SSE 消息
    const parts = buffer.split('\n\n');
    buffer = parts.pop();  // 最后一个片段可能不完整

    for (const part of parts) {
        const lines = part.split('\n');
        const eventLine = lines.find(l => l.startsWith('event:'));
        const dataLine = lines.find(l => l.startsWith('data:'));

        if (dataLine) {
            const eventType = eventLine?.replace('event: ', '') || 'message';
            const data = JSON.parse(dataLine.replace('data: ', ''));
            console.log(`[${eventType}]`, data);
        }
    }
}
```

真实项目用 React 的 `fetch` + Streams API 消费 SSE 流，而不是浏览器的
`EventSource`（因为 `EventSource` 只支持 GET，不支持带 Body 的 POST）。

## 变更内容

| 组件 | 之前（s12） | 之后（s13） |
|------|-----------|------------|
| 入口 | `input(">> ")` 命令行交互 | `POST /chat/stream` HTTP 端点 |
| 输出 | `print()` 终端打印 | SSE 流式推送 |
| 响应模式 | `app.invoke()` 同步阻塞 | `app.astream_events()` 流式迭代 |
| 并发 | 单用户 | 多 thread_id 并发 |
| 前端 | 无 | Swagger UI (`/docs`)、React、任意 HTTP 客户端 |
| 部署 | `python code.py` | `uvicorn api:app` |

## 试一试

```sh
cd course
pip install fastapi uvicorn langgraph langchain-openai python-dotenv
OPENAI_API_KEY=... python s13_api_server/code.py
```

### 1. 测试健康检查

```sh
curl http://localhost:8000/health
# {"status":"ok"}
```

### 2. 测试非流式聊天

```sh
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "test-1", "message": "What is 2+2?"}'
```

### 3. 测试流式聊天

```sh
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "test-2", "message": "List Python files in course/"}' \
  --no-buffer
```

观察 SSE 事件流：
```
event: token
data: {"content": "Let"}

event: token
data: {"content": " me"}

event: tool_call
data: {"name": "bash", "status": "started", ...}

event: token
data: {"content": "Found"}

event: done
data: {"status": "completed"}
```

### 4. 打开 Swagger UI

浏览器访问 http://localhost:8000/docs —— 可以直接在网页上测试端点。

## 真实项目对比

| 特性 | code.py（本章） | api/server.py（真实） |
|------|----------------|----------------------|
| 端点 | 3 个 | ~14 个 |
| 流式 | `astream_events()` 直接处理 | 通过 `harness.run_user_turn_stream()` 包装 |
| LLM 配置 | 环境变量 | `LLMConfig` 可运行时动态传入 |
| 审批 | 无 | `POST /approve` + 流式版本 |
| 技能管理 | 无 | `GET /skills`, `POST /skills/reload` |
| 技能评估 | 无 | 完整的评估管线 + 流式进度 |
| CORS | 无 | 根据 `cors_origins` 配置 |
| 日志 | 无 | 自定义 Formatter + 缓存/checkpoint 日志 |
| Lifespan | 无 | 异步启动 Postgres/Redis + 技能预热 |
| 错误处理 | 基础 try/except | Guard 检查 + 审计记录 |

真实项目的函数签名：
```python
# backend/src/personal_assistant/api/server.py
@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        harness.run_user_turn_stream(request.thread_id, request.message, request.llm),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
```

和本章 code.py 的区别是真实项目把流式生成器封装在 `harness.run_user_turn_stream()`
里，这个封装负责 prompt guard、compaction 检测、审批状态检查。本章为了教学清晰度
把这些都折叠了，直接在路由里写 astream_events。

## 下一步

[s14: Task Orchestrator](../s14_task_orchestrator/) —— 多个 agent 如何协作？
主 Agent 把子任务分发给专项 agent，汇总结果，像项目经理一样调度整个执行计划。

---

**源码参考**: `backend/src/personal_assistant/api/server.py`（897 行，14 个端点）
`backend/src/personal_assistant/api/schemas.py`（200 行，19 个 Pydantic 模型）
