# Reasoning Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream model-provided reasoning text to the frontend and show it in a collapsible assistant reasoning card.

**Architecture:** Add a `reasoning` SSE event emitted only when chat model stream chunks contain provider reasoning fields. Extend the frontend stream types, chat state, and message bubble rendering so reasoning appears while streaming and collapses when complete.

**Tech Stack:** FastAPI, LangGraph/LangChain, pytest, React 19, TypeScript, Vitest, Testing Library.

## Global Constraints

- Do not invent reasoning content when the model does not provide it.
- Keep existing `token`, `requires_approval`, `done`, and `error` events compatible.
- Support both `/api/chat/stream` and `/api/approve/stream`.
- No new runtime dependencies.
- Follow strict TDD: write failing tests, watch them fail, then implement.

---

### Task 1: Backend Reasoning SSE

**Files:**
- Modify: `backend/tests/test_stream_error_handling.py`
- Modify: `backend/src/personal_assistant/agent/harness.py`

**Interfaces:**
- Produces: `_extract_reasoning_content(chunk: Any) -> str`
- Produces: stream event `event: reasoning` with payload `{"content": str}`
- Consumes: existing `_sse_event(event: str, data: dict[str, Any]) -> str`

- [ ] **Step 1: Write the failing backend tests**

Add helper fake classes and tests to `backend/tests/test_stream_error_handling.py`:

```python
class FakeChunk:
    def __init__(
        self,
        content: str = "",
        additional_kwargs: dict | None = None,
        response_metadata: dict | None = None,
    ) -> None:
        self.content = content
        self.additional_kwargs = additional_kwargs or {}
        self.response_metadata = response_metadata or {}


class FakeState:
    values = {"messages": []}


class ReasoningStreamApp:
    def __init__(self, chunks: list[FakeChunk]) -> None:
        self.chunks = chunks

    async def astream_events(self, *_args, **_kwargs):
        for chunk in self.chunks:
            yield {"event": "on_chat_model_stream", "data": {"chunk": chunk}}

    async def aget_state(self, *_args, **_kwargs):
        return FakeState()


class ReasoningHarness(AgentHarness):
    def __init__(self, chunks: list[FakeChunk]) -> None:
        self.chunks = chunks

    def _compile(self, _llm_config=None):
        return ReasoningStreamApp(self.chunks)
```

Add the tests:

```python
@pytest.mark.asyncio
async def test_streaming_reasoning_chunks_are_sent_as_sse_events() -> None:
    chunks = [
        chunk
        async for chunk in ReasoningHarness(
            [
                FakeChunk(additional_kwargs={"reasoning_content": "think"}),
                FakeChunk(content="answer"),
            ]
        ).run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks == [
        'event: reasoning\ndata: {"content": "think"}\n\n',
        'event: token\ndata: {"content": "answer"}\n\n',
        'event: done\ndata: {"status": "completed", "message": ""}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_streaming_chunks_without_reasoning_do_not_emit_reasoning_events() -> None:
    chunks = [
        chunk
        async for chunk in ReasoningHarness(
            [FakeChunk(response_metadata={"finish_reason": "stop"}, content="answer")]
        ).run_user_turn_stream("thread-1", "hello")
    ]

    assert chunks == [
        'event: token\ndata: {"content": "answer"}\n\n',
        'event: done\ndata: {"status": "completed", "message": ""}\n\n',
        "data: [DONE]\n\n",
    ]
```

- [ ] **Step 2: Run backend tests to verify RED**

Run:

```powershell
cd backend
python -m pytest tests/test_stream_error_handling.py -q
```

Expected: the new reasoning test fails because `event: reasoning` is missing.

- [ ] **Step 3: Implement minimal backend extraction and streaming**

In `backend/src/personal_assistant/agent/harness.py`, add:

```python
_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")


def _extract_reasoning_content(chunk: Any) -> str:
    for source_name in ("additional_kwargs", "response_metadata"):
        source = getattr(chunk, source_name, None)
        if not isinstance(source, dict):
            continue
        for key in _REASONING_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return ""
```

In both streaming loops, before yielding `token`, add:

```python
reasoning = _extract_reasoning_content(chunk)
if reasoning:
    yield _sse_event("reasoning", {"content": reasoning})
```

- [ ] **Step 4: Run backend tests to verify GREEN**

Run:

```powershell
cd backend
python -m pytest tests/test_stream_error_handling.py -q
```

Expected: all tests in the file pass.

### Task 2: Frontend Stream State

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/hooks/useChat.ts`
- Modify: `frontend/src/hooks/useChat.test.ts`

**Interfaces:**
- Produces: `StreamReasoning { type: 'reasoning'; content: string }`
- Produces: `Message.reasoning`, `Message.reasoningStreaming`, `Message.reasoningCollapsed`
- Produces: `toggleReasoning(messageId: string) => void`

- [ ] **Step 1: Write failing API parsing test**

In `frontend/src/lib/api.test.ts`, add a `chatStream` test:

```typescript
it('yields reasoning events from SSE stream', async () => {
  const events: StreamEvent[] = [
    { type: 'reasoning', content: 'thinking' },
    { type: 'token', content: 'answer' },
    { type: 'done', status: 'completed', message: 'answer' },
  ]
  server.use(
    http.post(`${BASE}/api/chat/stream`, () =>
      new HttpResponse(sseBody(events), {
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    ),
  )
  const results: StreamEvent[] = []
  for await (const e of api.chatStream({ thread_id: 't1', message: 'Hi' })) {
    results.push(e)
  }
  expect(results[0]).toEqual({ type: 'reasoning', content: 'thinking' })
})
```

- [ ] **Step 2: Write failing hook tests**

In `frontend/src/hooks/useChat.test.ts`, add:

```typescript
it('streams reasoning into the assistant message and collapses it when answer starts', async () => {
  mockApi.chatStream.mockReturnValue(
    makeStream([
      { type: 'reasoning', content: 'first ' },
      { type: 'reasoning', content: 'second' },
      { type: 'token', content: 'Answer' },
      { type: 'done', status: 'completed', message: 'Answer' },
    ]),
  )

  const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

  await act(async () => {
    await result.current.send('Hi')
  })

  const assistant = result.current.messages.find((m) => m.role === 'assistant')!
  expect(assistant.reasoning).toBe('first second')
  expect(assistant.reasoningStreaming).toBe(false)
  expect(assistant.reasoningCollapsed).toBe(true)
  expect(assistant.content).toBe('Answer')
})

it('toggles a completed reasoning card', async () => {
  mockApi.chatStream.mockReturnValue(
    makeStream([
      { type: 'reasoning', content: 'thinking' },
      { type: 'done', status: 'completed', message: 'Answer' },
    ]),
  )

  const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

  await act(async () => {
    await result.current.send('Hi')
  })

  const assistant = result.current.messages.find((m) => m.role === 'assistant')!
  expect(assistant.reasoningCollapsed).toBe(true)

  act(() => {
    result.current.toggleReasoning(assistant.id)
  })

  expect(result.current.messages.find((m) => m.id === assistant.id)?.reasoningCollapsed).toBe(false)
})
```

- [ ] **Step 3: Run frontend tests to verify RED**

Run:

```powershell
cd frontend
npm test -- src/lib/api.test.ts src/hooks/useChat.test.ts
```

Expected: TypeScript or assertions fail because `reasoning` and `toggleReasoning` do not exist.

- [ ] **Step 4: Implement frontend stream state**

In `frontend/src/lib/api.ts`, add:

```typescript
export interface StreamReasoning {
  type: 'reasoning'
  content: string
}
```

Change:

```typescript
export type StreamEvent = StreamToken | StreamReasoning | StreamRequiresApproval | StreamDone | StreamError
```

In `frontend/src/hooks/useChat.ts`, extend `Message`:

```typescript
reasoning?: string
reasoningStreaming?: boolean
reasoningCollapsed?: boolean
```

Inside `processStream`, add an `ensureAssistantMessage` helper local to the callback and handle `reasoning` by appending to `reasoning`, setting `reasoningStreaming: true`, and `reasoningCollapsed: false`. Before handling `token`, `done`, or `requires_approval`, mark reasoning complete with `reasoningStreaming: false` and `reasoningCollapsed: true`.

Add:

```typescript
const toggleReasoning = useCallback((messageId: string) => {
  setMessages((prev) =>
    prev.map((m) =>
      m.id === messageId && m.reasoning
        ? { ...m, reasoningCollapsed: !m.reasoningCollapsed }
        : m,
    ),
  )
}, [])
```

Return `toggleReasoning` from the hook.

- [ ] **Step 5: Run frontend stream state tests to verify GREEN**

Run:

```powershell
cd frontend
npm test -- src/lib/api.test.ts src/hooks/useChat.test.ts
```

Expected: both files pass.

### Task 3: Frontend Reasoning Card UI

**Files:**
- Modify: `frontend/src/components/MessageBubble.tsx`
- Modify: `frontend/src/components/MessageBubble.test.tsx`
- Modify: `frontend/src/components/MessageList.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: `Message.reasoning`, `Message.reasoningStreaming`, `Message.reasoningCollapsed`
- Consumes: `toggleReasoning(messageId: string) => void`
- Produces: a clickable reasoning card header in assistant messages with reasoning content

- [ ] **Step 1: Write failing component tests**

In `frontend/src/components/MessageBubble.test.tsx`, import `userEvent` and add:

```typescript
it('renders completed reasoning collapsed by default and expands on click', async () => {
  const user = userEvent.setup()
  render(
    <MessageBubble
      id="m1"
      role="assistant"
      content="Answer"
      reasoning="Hidden thought"
      reasoningCollapsed={true}
      onToggleReasoning={() => {}}
    />,
  )

  expect(screen.getByRole('button', { name: /thinking complete/i })).toBeInTheDocument()
  expect(screen.queryByText('Hidden thought')).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /thinking complete/i }))
})

it('renders streaming reasoning expanded', () => {
  render(
    <MessageBubble
      id="m1"
      role="assistant"
      content=""
      reasoning="Working it out"
      reasoningStreaming={true}
      reasoningCollapsed={false}
      onToggleReasoning={() => {}}
    />,
  )

  expect(screen.getByText('Working it out')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /thinking/i })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run component test to verify RED**

Run:

```powershell
cd frontend
npm test -- src/components/MessageBubble.test.tsx
```

Expected: TypeScript fails because props do not exist.

- [ ] **Step 3: Implement reasoning card UI**

In `MessageBubble.tsx`, add props:

```typescript
id: string
reasoning?: string
reasoningStreaming?: boolean
reasoningCollapsed?: boolean
onToggleReasoning?: (messageId: string) => void
```

Render a button for assistant messages with reasoning:

```tsx
{role === 'assistant' && reasoning && (
  <div className={`reasoning-card ${reasoningCollapsed ? 'collapsed' : ''}`}>
    <button
      type="button"
      className="reasoning-header"
      onClick={() => onToggleReasoning?.(id)}
      aria-expanded={!reasoningCollapsed}
    >
      <span>{reasoningStreaming ? 'Thinking' : 'Thinking complete'}</span>
      <span className="reasoning-toggle">{reasoningCollapsed ? 'Show' : 'Hide'}</span>
    </button>
    {!reasoningCollapsed && (
      <div className="reasoning-content">{reasoning}</div>
    )}
  </div>
)}
```

In `MessageList.tsx`, pass `id`, reasoning fields, and `onToggleReasoning` through to `MessageBubble`.

In `App.css`, add styles for `.reasoning-card`, `.reasoning-header`, `.reasoning-toggle`, and `.reasoning-content` using the existing assistant bubble colors and compact spacing.

- [ ] **Step 4: Run component tests to verify GREEN**

Run:

```powershell
cd frontend
npm test -- src/components/MessageBubble.test.tsx src/components/MessageList.test.tsx
```

Expected: component tests pass.

### Task 4: Full Verification

**Files:**
- No additional code files.

**Interfaces:**
- Verifies all earlier tasks together.

- [ ] **Step 1: Run backend targeted tests**

```powershell
cd backend
python -m pytest tests/test_stream_error_handling.py -q
```

Expected: pass.

- [ ] **Step 2: Run frontend targeted tests**

```powershell
cd frontend
npm test -- src/lib/api.test.ts src/hooks/useChat.test.ts src/components/MessageBubble.test.tsx src/components/MessageList.test.tsx
```

Expected: pass.

- [ ] **Step 3: Run frontend build**

```powershell
cd frontend
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Review diff**

```powershell
git diff -- backend/src/personal_assistant/agent/harness.py backend/tests/test_stream_error_handling.py frontend/src/lib/api.ts frontend/src/hooks/useChat.ts frontend/src/components/MessageBubble.tsx frontend/src/components/MessageList.tsx frontend/src/App.css
```

Expected: diff only contains reasoning streaming feature changes.
