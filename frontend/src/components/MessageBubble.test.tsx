import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'

describe('MessageBubble', () => {
  it('renders user message', () => {
    render(<MessageBubble role="user" content="Hello" />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
    expect(screen.getByTestId('message-bubble')).toHaveClass('user')
  })

  it('renders assistant message', () => {
    render(<MessageBubble role="assistant" content="Hi there!" />)
    expect(screen.getByText('Hi there!')).toBeInTheDocument()
    expect(screen.getByTestId('message-bubble')).toHaveClass('assistant')
  })

  it('renders tool call with pending badge', () => {
    render(<MessageBubble role="tool_call" content="get_time" approvalStatus="pending" />)
    expect(screen.getByText('get_time')).toBeInTheDocument()
    expect(screen.getByText('Pending')).toBeInTheDocument()
  })

  it('renders tool call with approved badge', () => {
    render(<MessageBubble role="tool_call" content="get_time" approvalStatus="approved" />)
    expect(screen.getByText('Approved')).toBeInTheDocument()
  })

  it('renders tool call with denied badge', () => {
    render(<MessageBubble role="tool_call" content="get_time" approvalStatus="denied" />)
    expect(screen.getByText('Denied')).toBeInTheDocument()
  })

  it('renders role label', () => {
    render(<MessageBubble role="user" content="test" />)
    expect(screen.getByText('You')).toBeInTheDocument()
  })

  it('renders assistant role label', () => {
    render(<MessageBubble role="assistant" content="test" />)
    expect(screen.getByText('Assistant')).toBeInTheDocument()
  })
})
