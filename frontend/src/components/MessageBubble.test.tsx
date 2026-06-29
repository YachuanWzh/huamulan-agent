import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('shows typewriter cursor when streaming', () => {
    render(<MessageBubble role="assistant" content="typing..." streaming={true} />)
    expect(screen.getByTestId('typewriter-cursor')).toBeInTheDocument()
    expect(screen.getAllByText('typing...').length).toBeGreaterThan(0)
  })

  it('does not show typewriter cursor when not streaming', () => {
    render(<MessageBubble role="assistant" content="done" />)
    expect(screen.queryByTestId('typewriter-cursor')).not.toBeInTheDocument()
  })

  it('does not show typewriter cursor when streaming is false', () => {
    render(<MessageBubble role="assistant" content="done" streaming={false} />)
    expect(screen.queryByTestId('typewriter-cursor')).not.toBeInTheDocument()
  })

  it('renders completed reasoning collapsed by default and expands on click', async () => {
    const user = userEvent.setup()
    let toggled = false
    render(
      <MessageBubble
        id="m1"
        role="assistant"
        content="Answer"
        reasoning="Hidden thought"
        reasoningCollapsed={true}
        onToggleReasoning={() => {
          toggled = true
        }}
      />,
    )

    expect(screen.getByRole('button', { name: /completed/i })).toBeInTheDocument()
    expect(screen.queryByText('Hidden thought')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /completed/i }))

    expect(toggled).toBe(true)
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
})
