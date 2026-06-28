import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

vi.mock('./components/ChatPanel', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return {
    ChatPanel: ({
      threadId,
      onThreadCreated,
    }: {
      threadId: string | null
      onThreadCreated: () => string
    }) => {
      const [initialThreadId] = React.useState(threadId)
      return (
        <div>
          <div data-testid="chat-thread">{threadId}</div>
          <div data-testid="chat-initial-thread">{initialThreadId}</div>
          <button onClick={onThreadCreated}>Mock create thread</button>
        </div>
      )
    },
  }
})

vi.mock('./components/Sidebar', () => ({
  Sidebar: ({
    threadId,
    onThreadCleared,
  }: {
    threadId: string | null
    onThreadCleared: () => void
  }) => (
    <div>
      <div data-testid="sidebar-thread">{threadId}</div>
      <button onClick={onThreadCleared}>Mock new thread</button>
    </div>
  ),
}))

describe('App thread id', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('uses the stored thread id until history is cleared', async () => {
    localStorage.setItem('threadId', 'stored-thread')
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('new-thread' as `${string}-${string}-${string}-${string}-${string}`)

    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByTestId('chat-thread')).toHaveTextContent('stored-thread')

    await user.click(screen.getByRole('button', { name: /mock new thread/i }))

    expect(screen.getByTestId('chat-thread')).toHaveTextContent('new-thread')
    expect(screen.getByTestId('chat-initial-thread')).toHaveTextContent('new-thread')
    expect(localStorage.getItem('threadId')).toBe('new-thread')
  })

  it('does not create a thread until conversation starts', async () => {
    const randomUUID = vi
      .spyOn(crypto, 'randomUUID')
      .mockReturnValue('created-thread' as `${string}-${string}-${string}-${string}-${string}`)

    const user = userEvent.setup()
    render(<App />)

    expect(randomUUID).not.toHaveBeenCalled()
    expect(localStorage.getItem('threadId')).toBeNull()
    expect(screen.getByTestId('sidebar-thread')).toHaveTextContent('')

    await user.click(screen.getByRole('button', { name: /mock create thread/i }))

    expect(randomUUID).toHaveBeenCalledOnce()
    expect(localStorage.getItem('threadId')).toBe('created-thread')
    expect(screen.getByTestId('sidebar-thread')).toHaveTextContent('created-thread')
  })
})
