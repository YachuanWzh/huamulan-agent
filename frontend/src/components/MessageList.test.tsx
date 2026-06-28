import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessageList } from './MessageList'
import type { Message } from '../hooks/useChat'

const messages: Message[] = [
  { id: '1', role: 'user', content: 'Hello' },
  { id: '2', role: 'assistant', content: 'Hi!' },
]

describe('MessageList', () => {
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
    render(<MessageList messages={messages} loading={true} />)
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument()
  })
})
