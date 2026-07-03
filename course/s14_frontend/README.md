# s14: Frontend Chat（前端聊天界面）

`[ s01 > s02 > s03 > s04 > s05 > s06 > s07 > s08 > s09 > s10 > s11 > s12 > s13 ] s14 > s15 > s16`

> *"给 Agent 一张脸"* —— React 19 + TypeScript 单页应用，
> 三栏布局，SSE 流式渲染，工具审批卡片，Markdown 推理展开。
>
> **Harness 层**: UI —— Agent 与用户的交互界面。

## 问题

前 13 章我们构建了一个完整的 Agent 引擎——它能推理、调工具、记忆、自我防护。
但它只有一个终端界面。真正的用户需要的是：

- **实时看到 Agent 的思考过程**：不是等 30 秒后看到一坨结果
- **在工具执行前做出审批**：看到 Agent 想干什么，点同意或拒绝
- **回顾历史对话**：切换线程、回放 checkpoint、查看执行日志
- **管理技能**：安装、卸载、评测技能

终端做不到这些。需要 Web 前端。

## 解决方案

```
┌──────────────────────────────────────────────────────────┐
│  Header: 花木兰 Agent                            [thread]│
├────────────┬────────────────────────┬────────────────────┤
│  Sidebar   │     Chat Panel         │  Workspace Panel   │
│            │                        │                    │
│  📋 Skills │  ┌──────────────────┐  │  📊 Skill Details  │
│  💬 Threads│  │ MessageBubble    │  │  ⏮ Checkpoint     │
│  ⏸ Checkpt│  │ "我来帮你..."    │  │  📝 Audit Log      │
│  📝 Audit  │  │ [thinking▾]     │  │  ⚠ Tool Errors    │
│            │  └──────────────────┘  │  📈 Timeline       │
│            │  ┌──────────────────┐  │                    │
│            │  │ ToolApprovalCard │  │                    │
│            │  │ ⚠ write_file    │  │                    │
│            │  │ [Approve][Deny] │  │                    │
│            │  └──────────────────┘  │                    │
│            │  ┌──────────────────┐  │                    │
│            │  │ MessageInput     │  │                    │
│            │  │ [___________] ⏎ │  │                    │
│            │  └──────────────────┘  │                    │
├────────────┴────────────────────────┴────────────────────┤
│  Status: ✅ Connected  | SSE streaming  | thread_abc123  │
└──────────────────────────────────────────────────────────┘
```

**技术栈**：React 19 + TypeScript 6 + Vite 8 + Vitest + react-markdown + remark-gfm

**核心组件**（源码位置 `frontend/src/`）：

| 组件 | 职责 |
|------|------|
| `App.tsx` | 根组件：三栏布局、面板切换、`conversationKey` 强制重挂载 |
| `ChatPanel.tsx` | 聊天面板：组合 MessageList + MessageInput + ToolApprovalCard |
| `MessageList.tsx` | 消息列表：自动滚动、`scrollKey` 驱动 |
| `MessageBubble.tsx` | 消息气泡：按角色渲染（user/assistant/tool）、reasoning 展开 |
| `MarkdownRenderer.tsx` | Markdown 渲染：react-markdown + remark-gfm |
| `ToolApprovalCard.tsx` | 单个工具审批卡片 |
| `ToolApprovalBatchCard.tsx` | 批量工具审批卡片 |
| `MessageInput.tsx` | 输入框：Enter 发送、Shift+Enter 换行 |
| `Sidebar.tsx` | 左侧栏：技能/线程/Checkpoint/审计四个标签页 |
| `WorkspacePanel.tsx` | 右侧面板：技能详情/Checkpoint 回放/审计日志/时间线 |

## 工作原理

### 1. SSE 流式通信

前端通过 `fetch()` + `ReadableStream` 消费 SSE 事件：

```typescript
// api.ts — streamRequest 核心
async function* streamRequest(url: string, body: any): AsyncGenerator<StreamEvent> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 事件格式: "data: {...}\n\n"
    const lines = buffer.split('\n\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        yield JSON.parse(line.slice(6));
      }
    }
  }
}
```

7 种 SSE 事件类型：
- `token` — LLM 逐 token 输出
- `tool_start` — 工具开始执行
- `tool_end` — 工具执行完成
- `requires_approval` — 需要用户审批
- `approval_resolved` — 审批已处理
- `error` — 错误
- `done` — 对话完成

### 2. useChat 状态机

```typescript
// hooks/useChat.ts — 核心状态机
type ChatState = 'idle' | 'streaming' | 'waiting_for_approval' | 'error';

function useChat() {
  const [state, setState] = useState<ChatState>('idle');
  const [messages, setMessages] = useState<Message[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<ToolCall[]>([]);

  async function send(userMessage: string) {
    setState('streaming');
    for await (const event of streamRequest('/api/chat/stream', { message: userMessage })) {
      switch (event.type) {
        case 'token':
          appendToken(event.data);          // 逐字追加到当前消息
          break;
        case 'tool_start':
          addToolMessage(event.data);       // 显示工具调用
          break;
        case 'tool_end':
          updateToolResult(event.data);     // 显示工具结果
          break;
        case 'requires_approval':
          setPendingApprovals(event.data);   // 暂停，等待审批
          setState('waiting_for_approval');
          break;
        case 'done':
          setState('idle');                  // 完成
          break;
      }
    }
  }

  async function approve(approvalId: string, approved: boolean) {
    await fetch('/api/approve', {
      method: 'POST',
      body: JSON.stringify({ approval_id: approvalId, approved }),
    });
    setState('streaming');  // 恢复流式接收
  }
}
```

状态转换：
```
idle ──send()──→ streaming ──requires_approval──→ waiting_for_approval
  ↑                 │                                    │
  └──done───────────┘              approve() ────────────┘
```

### 3. 工具审批 UI

当 Agent 尝试写文件（`write_file`）时，后端返回 `requires_approval` 事件：

```
┌─────────────────────────────────────┐
│ ⚠ Agent wants to use 2 tools       │
│                                     │
│ 📝 write_file                       │
│   path: /config.json                │
│   content: {"version": "2.0", ...}  │
│                                     │
│ 💻 bash                             │
│   command: npm install              │
│                                     │
│ [Approve All]  [Deny All]          │
└─────────────────────────────────────┘
```

用户点 Approve → API `POST /api/approve/batch` → 后端执行工具 → SSE 流继续。

### 4. Markdown 渲染 + 推理展开

```tsx
// MarkdownRenderer.tsx
function MarkdownRenderer({ content }: { content: string }) {
  // 将 <thinking> 块替换为可折叠的 details
  const processed = content.replace(
    /<thinking>([\s\S]*?)<\/thinking>/g,
    (_, text) => `<details><summary>🤔 Reasoning</summary>\n\n${text}\n\n</details>`
  );

  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {processed}
    </ReactMarkdown>
  );
}
```

### 5. 线程管理

- **新建线程**：前端生成 `thread_id` = `crypto.randomUUID()`
- **切换线程**：`key={conversationKey}` 强制 React 重新挂载 ChatPanel
- **线程持久化**：后端 LangGraph checkpoint 按 `thread_id` 隔离
- **线程回放**：WorkspacePanel 展示 checkpoint 历史，可回放到任意状态

## 变更内容

| 组件 | 之前 | 之后 |
|------|------|------|
| 用户界面 | 终端 REPL | React SPA (三栏) |
| 消息展示 | print() | 流式逐字渲染 + Markdown |
| 工具审批 | stdin y/n | 审批卡片 + 批量操作 |
| 历史对话 | 无 | 线程列表 + 切换 + 回放 |
| 执行追踪 | 无 GUI | 审计面板 + 时间线 + 重试链 |

## 试一试

```sh
cd frontend
npm install
npm run dev     # http://localhost:5173
```

然后在另一个终端启动后端：
```sh
cd backend
uvicorn personal_assistant.api.server:app --reload
```

## 真实项目对比

本章是纯文档章节（无 code.py），因为前端不适用单文件 Python 模式。真实前端的完整源码在
`frontend/src/` 目录下，约 15 个组件文件，总计约 3000 行 TypeScript/TSX。

## 下一步

[s15: Tracing](../s15_tracing/) —— 可观测性：Langfuse 分布式追踪 + 执行日志审计。
