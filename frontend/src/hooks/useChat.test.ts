import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useChat } from './useChat'
import * as apiModule from '../lib/api'
import type { StreamEvent } from '../lib/api'

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
    // User message + tool call message
    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[1]!).toMatchObject({
      role: 'tool_call',
      content: 'get_time',
      approvalStatus: 'pending',
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

  it('approve updates the specific tool_call message to approved', async () => {
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

    const toolCallMsg = result.current.messages.find(
      (m) => m.role === 'tool_call',
    )
    expect(toolCallMsg?.approvalStatus).toBe('approved')
  })

  it('deny only marks the targeted tool_call as denied, not all', async () => {
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

    // Two tool_call messages should exist
    const toolCalls = result.current.messages.filter((m) => m.role === 'tool_call')
    expect(toolCalls).toHaveLength(2)

    await act(async () => {
      await result.current.deny('a1')
    })

    const updatedToolCalls = result.current.messages.filter((m) => m.role === 'tool_call')
    const denied = updatedToolCalls.find((m) => m.approvalId === 'a1')
    const stillPending = updatedToolCalls.find((m) => m.approvalId === 'a2')
    expect(denied?.approvalStatus).toBe('denied')
    expect(stillPending?.approvalStatus).toBe('pending')
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
