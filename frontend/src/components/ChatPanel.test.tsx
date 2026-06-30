import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { ChatPanel } from './ChatPanel'
import * as apiModule from '../lib/api'
import type { StreamEvent } from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    chat: vi.fn(),
    chatStream: vi.fn(),
    approve: vi.fn(),
    approveStream: vi.fn(),
    listPendingApprovals: vi.fn(),
  },
}))

const mockApi = vi.mocked(apiModule.api)

/** Helper: create an async generator from an array of StreamEvent */
async function* makeStream(events: StreamEvent[]): AsyncGenerator<StreamEvent> {
  for (const e of events) yield e
}

function renderChatPanel(
  props: Partial<ComponentProps<typeof ChatPanel>> = {},
) {
  return render(
    <ChatPanel
      threadId="t1"
      onThreadCreated={() => 't1'}
      onNewConversation={vi.fn()}
      {...props}
    />,
  )
}

describe('ChatPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listPendingApprovals.mockResolvedValue([])
  })

  it('renders message input and empty state', () => {
    renderChatPanel()
    expect(screen.getByPlaceholderText(/type your message/i)).toBeInTheDocument()
    expect(screen.getByText(/send a message to start/i)).toBeInTheDocument()
  })

  it('sends message and displays assistant response', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'token', content: 'Hello!' },
        { type: 'token', content: ' How can I help?' },
        { type: 'done', status: 'completed', message: 'Hello! How can I help?' },
      ]),
    )

    const user = userEvent.setup()
    renderChatPanel()

    const input = screen.getByPlaceholderText(/type your message/i)
    await user.type(input, 'Hi there')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText('Hi there')).toBeInTheDocument()
      expect(screen.getByText('Hello! How can I help?')).toBeInTheDocument()
    })
  })

  it('creates a thread when sending the first message', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([{ type: 'done', status: 'completed', message: 'Hello!' }]),
    )
    const onThreadCreated = vi.fn(() => 'new-thread')

    const user = userEvent.setup()
    renderChatPanel({ threadId: null, onThreadCreated })

    await user.type(screen.getByPlaceholderText(/type your message/i), 'Hi')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(onThreadCreated).toHaveBeenCalledOnce()
      expect(mockApi.chatStream).toHaveBeenCalledWith({
        thread_id: 'new-thread',
        message: 'Hi',
      })
    })
  })

  it('starts a new conversation from the input action', async () => {
    const onNewConversation = vi.fn()
    const user = userEvent.setup()
    renderChatPanel({ onNewConversation })

    await user.click(screen.getByRole('button', { name: /new conversation/i }))

    expect(onNewConversation).toHaveBeenCalledOnce()
  })

  it('shows approval cards when status is requires_approval', async () => {
    mockApi.chatStream.mockReturnValue(
      makeStream([
        { type: 'requires_approval', approvals: [
          {
            approval_id: 'a1',
            tool_call_id: 'tc1',
            name: 'resolve_current_time',
            args: {},
          },
        ]},
      ]),
    )

    const user = userEvent.setup()
    renderChatPanel()

    await user.type(screen.getByPlaceholderText(/type your message/i), 'What time?')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getAllByText('resolve_current_time').length).toBeGreaterThanOrEqual(1)
      expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /deny/i })).toBeInTheDocument()
    })
  })

  it('shows error message on API failure', async () => {
    mockApi.chatStream.mockImplementation(() => {
      throw new Error('Server error')
    })

    const user = userEvent.setup()
    renderChatPanel()

    await user.type(screen.getByPlaceholderText(/type your message/i), 'Hi')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText('Server error')).toBeInTheDocument()
    })
  })
})
