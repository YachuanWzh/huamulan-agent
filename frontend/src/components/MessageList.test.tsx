import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MessageList } from './MessageList'
import type { Message } from '../hooks/useChat'

const messages: Message[] = [
  { id: '1', role: 'user', content: 'Hello' },
  { id: '2', role: 'assistant', content: 'Hi!' },
]

describe('MessageList', () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn()
  })

  it('renders all messages', () => {
    render(<MessageList messages={messages} />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
    expect(screen.getByText('Hi!')).toBeInTheDocument()
  })

  it('shows empty state when no messages', () => {
    render(<MessageList messages={[]} />)
    expect(screen.getByText(/send a message to start/i)).toBeInTheDocument()
  })

  it('shows loading indicator when loading', () => {
    render(<MessageList messages={[{ id: '1', role: 'user', content: 'Hello' }]} loading={true} />)
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument()
  })

  it('keeps thinking status inside the assistant card while reasoning streams', () => {
    render(
      <MessageList
        messages={[
          {
            id: '1',
            role: 'assistant',
            content: '',
            reasoning: 'Reading file',
            reasoningStreaming: true,
            reasoningCollapsed: false,
          },
        ]}
        loading={true}
      />,
    )

    expect(screen.getByRole('button', { name: /thinking/i })).toBeInTheDocument()
    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument()
  })

  it('does not show loading below an assistant card after reasoning completes', () => {
    render(
      <MessageList
        messages={[
          {
            id: '1',
            role: 'assistant',
            content: 'Answer text',
            reasoning: 'Read the file',
            reasoningStreaming: false,
            reasoningCollapsed: true,
          },
        ]}
        loading={true}
      />,
    )

    expect(screen.getByRole('button', { name: /completed/i })).toBeInTheDocument()
    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument()
  })

  it('does not auto-scroll when only reasoning collapse state changes', async () => {
    const baseMessage: Message = {
      id: '1',
      role: 'assistant',
      content: 'Answer text',
      reasoning: 'Read the file',
      reasoningStreaming: false,
      reasoningCollapsed: true,
    }
    const { rerender } = render(<MessageList messages={[baseMessage]} />)

    await waitFor(() => {
      expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1)
    })
    vi.mocked(Element.prototype.scrollIntoView).mockClear()

    rerender(
      <MessageList
        messages={[
          {
            ...baseMessage,
            reasoningCollapsed: false,
          },
        ]}
      />,
    )

    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled()
  })
})
