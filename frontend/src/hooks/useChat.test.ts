import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useChat } from './useChat'
import * as apiModule from '../lib/api'
import type { ReplayState, StreamEvent } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    chat: vi.fn(),
    chatStream: vi.fn(),
    approve: vi.fn(),
    approveStream: vi.fn(),
  },
}))

const mockApi = vi.mocked(apiModule.api)

/** Helper: create an async generator from an array of StreamEvent */
async function* makeStream(events: StreamEvent[]): AsyncGenerator<StreamEvent> {
  for (const e of events) yield e
}

describe('useChat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('starts with empty messages and no approvals', () => {
    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))
    expect(result.current.messages).toEqual([])
    expect(result.current.pendingApprovals).toEqual([])
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('adds user message and assistant response via streaming', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'token', content: 'Hello' },
        { type: 'token', content: ' there!' },
        { type: 'done', status: 'completed', message: 'Hello there!' },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('Hi')
    })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0]!).toEqual({
      id: '1',
      role: 'user',
      content: 'Hi',
    })
    expect(result.current.messages[1]!).toMatchObject({
      role: 'assistant',
      content: 'Hello there!',
      streaming: false,
    })
    expect(result.current.pendingApprovals).toEqual([])
  })

  it('sets streaming true during token streaming', async () => {
    let resolveStream!: () => void
    const streamPromise = new Promise<void>((resolve) => {
      resolveStream = resolve
    })

    // Create a stream that doesn't complete until we say so
    async function* slowStream(): AsyncGenerator<StreamEvent> {
      yield { type: 'token', content: 'Hello' }
      await streamPromise
      yield { type: 'done', status: 'completed', message: 'Hello' }
    }

    mockApi.chatStream.mockReturnValue(slowStream())

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    let sendDone = false
    act(() => {
      result.current.send('Hi').then(() => {
        sendDone = true
      })
    })

    // After the first token, we should have an assistant message with streaming: true
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10))
    })

    const assistantMsg = result.current.messages.find((m) => m.role === 'assistant')
    expect(assistantMsg?.streaming).toBe(true)
    expect(assistantMsg?.content).toBe('Hello')

    // Resolve the stream
    resolveStream!()

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10))
    })

    expect(sendDone).toBe(true)
    const finalMsg = result.current.messages.find((m) => m.role === 'assistant')
    expect(finalMsg?.streaming).toBe(false)
  })

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

  it('sets pending approvals on requires_approval', async () => {
    const approvals = [
      { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
    ]
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('What time is it?')
    })

    expect(result.current.pendingApprovals).toEqual(approvals)
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0]!).toMatchObject({
      role: 'user',
      content: 'What time is it?',
    })
  })

  it('approve continues the conversation via streaming', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
        ]},
      ]),
    )
    mockApi.approveStream.mockReturnValue(
      makeStream([
        { type: 'token', content: 'It is 3pm.' },
        { type: 'done', status: 'completed', message: 'It is 3pm.' },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('What time?')
    })

    expect(result.current.pendingApprovals).toHaveLength(1)

    await act(async () => {
      await result.current.approve('a1')
    })

    expect(result.current.pendingApprovals).toEqual([])
    expect(mockApi.approveStream).toHaveBeenCalledWith({
      thread_id: 'thread-1',
      approval_id: 'a1',
      approved: true,
    })
    const last = result.current.messages[result.current.messages.length - 1]!
    expect(last).toMatchObject({ role: 'assistant', content: 'It is 3pm.' })
  })

  it('shows tool execution results from the stream', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
        ]},
      ]),
    )
    mockApi.approveStream.mockReturnValue(
      makeStream([
        { type: 'tool_result', name: 'get_time', content: '3pm' },
        { type: 'done', status: 'completed', message: 'It is 3pm.' },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('What time?')
    })

    await act(async () => {
      await result.current.approve('a1')
    })

    expect(result.current.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: 'tool_call',
          content: 'get_time: 3pm',
          approvalStatus: 'approved',
        }),
      ]),
    )
  })

  it('deny rejects the tool call', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
        ]},
      ]),
    )
    mockApi.approveStream.mockReturnValue(
      makeStream([
        { type: 'done', status: 'completed', message: 'I cannot check the time without permission.' },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('What time?')
    })

    await act(async () => {
      await result.current.deny('a1')
    })

    expect(mockApi.approveStream).toHaveBeenCalledWith({
      thread_id: 'thread-1',
      approval_id: 'a1',
      approved: false,
    })
  })

  it('sets loading during send', async () => {
    let resolveStream!: () => void
    const streamPromise = new Promise<void>((resolve) => {
      resolveStream = resolve
    })

    async function* slowStream(): AsyncGenerator<StreamEvent> {
      await streamPromise
      yield { type: 'done', status: 'completed', message: 'ok' }
    }

    mockApi.chatStream.mockReturnValue(slowStream())

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    let sendDone = false
    act(() => {
      result.current.send('Hi').then(() => {
        sendDone = true
      })
    })

    expect(result.current.loading).toBe(true)

    resolveStream!()

    await act(async () => {
      await new Promise((r) => setTimeout(r, 10))
    })

    expect(sendDone).toBe(true)
    expect(result.current.loading).toBe(false)
  })

  it('sets error on API failure', async () => {
    mockApi.chatStream.mockImplementation(() => {
      throw new Error('Network error')
    })

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('Hi')
    })

    expect(result.current.error).toBe('Network error')
    expect(result.current.loading).toBe(false)
  })

  it('replaces conversation with replayed checkpoint messages', () => {
    const { result, rerender } = renderHook(
      ({ replayState }: { replayState: ReplayState | null }) =>
        useChat('thread-1', () => 'thread-1', replayState),
      { initialProps: { replayState: null as ReplayState | null } },
    )

    rerender({
      replayState: {
        checkpoint_id: 'checkpoint-1',
        parent_checkpoint_id: null,
        created_at: '2026-06-29T04:00:00+00:00',
        node: 'agent',
        values: {
          selected_skills: ['resolve-time'],
          pending_approvals: [],
        },
        messages: [
          { role: 'user', content: 'Hi' },
          { role: 'assistant', content: 'Hello from history' },
        ],
        checkpoint: {},
      },
    })

    expect(result.current.messages).toMatchObject([
      { id: 'replay-checkpoint-1-0', role: 'user', content: 'Hi' },
      {
        id: 'replay-checkpoint-1-1',
        role: 'assistant',
        content: 'Hello from history',
      },
    ])
    expect(result.current.pendingApprovals).toEqual([])
  })

  it('keeps replayed reasoning collapsed', () => {
    const { result, rerender } = renderHook(
      ({ replayState }: { replayState: ReplayState | null }) =>
        useChat('thread-1', () => 'thread-1', replayState),
      { initialProps: { replayState: null as ReplayState | null } },
    )

    rerender({
      replayState: {
        checkpoint_id: 'checkpoint-1',
        parent_checkpoint_id: null,
        created_at: '2026-06-29T04:00:00+00:00',
        node: 'agent',
        values: {},
        messages: [
          {
            role: 'assistant',
            content: 'Final answer',
            reasoning: 'private thinking',
          },
        ],
        checkpoint: {},
      },
    })

    expect(result.current.messages[0]).toMatchObject({
      role: 'assistant',
      content: 'Final answer',
      reasoning: 'private thinking',
      reasoningCollapsed: true,
      reasoningStreaming: false,
    })
  })

  it('sets error when the stream yields an error event', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'error', message: 'LLM connection failed' },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('Hi')
    })

    expect(result.current.error).toBe('LLM connection failed')
    expect(result.current.loading).toBe(false)
  })

  it('approve does not create a duplicate pending tool_call message', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
        ]},
      ]),
    )
    mockApi.approveStream.mockReturnValue(
      makeStream([
        { type: 'done', status: 'completed', message: 'Done.' },
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('What time?')
    })

    await act(async () => {
      await result.current.approve('a1')
    })

    expect(result.current.messages.filter((m) => m.role === 'tool_call')).toEqual([])
  })

  it('does not append duplicate pending tool_call messages when approvals refresh', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
          { approval_id: 'a2', tool_call_id: 'tc2', name: 'get_weather', args: {} },
        ]},
      ]),
    )
    mockApi.approveStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a2', tool_call_id: 'tc2', name: 'get_weather', args: {} },
        ]},
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('Time and weather?')
    })

    await act(async () => {
      await result.current.deny('a1')
    })

    expect(result.current.pendingApprovals).toEqual([
      { approval_id: 'a2', tool_call_id: 'tc2', name: 'get_weather', args: {} },
    ])
    expect(result.current.messages.filter((m) => m.role === 'tool_call')).toEqual([])
  })

  it('clearError resets error state', async () => {
    mockApi.chatStream.mockImplementation(() => {
      throw new Error('Network error')
    })

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('Hi')
    })

    expect(result.current.error).toBe('Network error')

    act(() => {
      result.current.clearError()
    })

    expect(result.current.error).toBeNull()
  })

  it('dismissApproval removes the approval', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
        ]},
      ]),
    )

    const { result } = renderHook(() => useChat('thread-1', () => 'thread-1'))

    await act(async () => {
      await result.current.send('What time?')
    })

    act(() => {
      result.current.dismissApproval('a1')
    })

    expect(result.current.pendingApprovals).toEqual([])
  })
})
