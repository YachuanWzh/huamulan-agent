import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessageInput } from './MessageInput'

describe('MessageInput', () => {
  it('renders textarea and send button', () => {
    render(<MessageInput onSend={vi.fn()} />)
    expect(screen.getByPlaceholderText(/type your message/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /new conversation/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument()
  })

  it('calls onNewConversation when new conversation is clicked', async () => {
    const onNewConversation = vi.fn()
    const user = userEvent.setup()
    render(<MessageInput onSend={vi.fn()} onNewConversation={onNewConversation} />)

    await user.click(screen.getByRole('button', { name: /new conversation/i }))

    expect(onNewConversation).toHaveBeenCalledOnce()
  })

  it('calls onSend with text and clears input', async () => {
    const onSend = vi.fn()
    const user = userEvent.setup()
    render(<MessageInput onSend={onSend} />)

    const textarea = screen.getByPlaceholderText(/type your message/i)
    await user.type(textarea, 'Hello world')
    await user.click(screen.getByRole('button', { name: /send/i }))

    expect(onSend).toHaveBeenCalledWith('Hello world')
    expect(textarea).toHaveValue('')
  })

  it('sends on Enter key', async () => {
    const onSend = vi.fn()
    const user = userEvent.setup()
    render(<MessageInput onSend={onSend} />)

    const textarea = screen.getByPlaceholderText(/type your message/i)
    await user.type(textarea, 'Hello{enter}')

    expect(onSend).toHaveBeenCalledWith('Hello')
  })

  it('does not send on Shift+Enter', async () => {
    const onSend = vi.fn()
    const user = userEvent.setup()
    render(<MessageInput onSend={onSend} />)

    const textarea = screen.getByPlaceholderText(/type your message/i)
    await user.type(textarea, 'Hello{shift>}{enter}{/shift}')

    expect(onSend).not.toHaveBeenCalled()
  })

  it('does not send empty or whitespace-only text', async () => {
    const onSend = vi.fn()
    const user = userEvent.setup()
    render(<MessageInput onSend={onSend} />)

    await user.click(screen.getByRole('button', { name: /send/i }))
    expect(onSend).not.toHaveBeenCalled()
  })

  it('disables input when loading', () => {
    render(<MessageInput onSend={vi.fn()} disabled />)
    expect(screen.getByPlaceholderText(/type your message/i)).toBeDisabled()
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled()
  })
})
