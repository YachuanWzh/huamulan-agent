import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

vi.mock('./components/ChatPanel', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return {
    ChatPanel: ({
      threadId,
      replayState,
      onThreadCreated,
    }: {
      threadId: string | null
      replayState?: { checkpoint_id: string } | null
      onThreadCreated: () => string
    }) => {
      const [initialThreadId] = React.useState(threadId)
      return (
        <div>
          <div data-testid="chat-thread">{threadId}</div>
          <div data-testid="chat-initial-thread">{initialThreadId}</div>
          <div data-testid="chat-replay">{replayState?.checkpoint_id ?? ''}</div>
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
    onThreadSelected,
    onReplayState,
  }: {
    threadId: string | null
    onThreadCleared: () => void
    onThreadSelected: (threadId: string) => void
    onReplayState: (state: { checkpoint_id: string }) => void
  }) => (
    <div data-testid="sidebar-shell">
      <div data-testid="sidebar-thread">{threadId}</div>
      <button onClick={onThreadCleared}>Mock new thread</button>
      <button
        onClick={() => {
          onThreadSelected('history-thread')
          onReplayState({ checkpoint_id: 'history-checkpoint' })
        }}
      >
        Mock select history
      </button>
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

  it('switches to a selected history thread', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /mock select history/i }))

    expect(screen.getByTestId('chat-thread')).toHaveTextContent('history-thread')
    expect(screen.getByTestId('sidebar-thread')).toHaveTextContent('history-thread')
    expect(screen.getByTestId('chat-replay')).toHaveTextContent('history-checkpoint')
    expect(localStorage.getItem('threadId')).toBe('history-thread')
  })

  it('exposes the redesigned console shell without changing core controls', () => {
    render(<App />)

    expect(
      screen.getByRole('banner', { name: /assistant console/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('main', { name: /conversation workspace/i }),
    ).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-shell')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: /langgraph assistant/i }),
    ).toBeInTheDocument()
  })
})
