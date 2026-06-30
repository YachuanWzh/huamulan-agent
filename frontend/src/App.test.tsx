import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'

vi.mock('./components/ChatPanel', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  let mountCounter = 0
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
      const [mountId] = React.useState(() => String(++mountCounter))
      return (
        <div>
          <div data-testid="chat-thread">{threadId}</div>
          <div data-testid="chat-initial-thread">{initialThreadId}</div>
          <div data-testid="chat-mount-id">{mountId}</div>
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
    onPanelChange,
  }: {
    threadId: string | null
    onThreadCleared: () => void
    onThreadSelected: (threadId: string) => void
    onReplayState: (state: { checkpoint_id: string }) => void
    onPanelChange: (panel: 'chat' | 'checkpoint' | 'audit') => void
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
      <button onClick={() => onPanelChange('audit')}>Mock audit panel</button>
      <button onClick={() => onPanelChange('checkpoint')}>Mock checkpoint panel</button>
    </div>
  ),
}))

vi.mock('./components/WorkspacePanel', () => ({
  WorkspacePanel: ({
    panel,
    threadId,
  }: {
    panel: 'checkpoint' | 'audit'
    threadId: string | null
  }) => (
    <div data-testid="workspace-panel">
      Workspace {panel} {threadId}
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

  it('starts unbound even when a previous thread id is stored', async () => {
    localStorage.setItem('threadId', 'stored-thread')
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('new-thread' as `${string}-${string}-${string}-${string}-${string}`)

    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByTestId('chat-thread')).toBeEmptyDOMElement()
    expect(screen.getByTestId('sidebar-thread')).toBeEmptyDOMElement()
    expect(screen.getByText(/thread: not started/i)).toBeInTheDocument()

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

  it('keeps the chat panel mounted when the first message creates a thread', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('created-thread' as `${string}-${string}-${string}-${string}-${string}`)

    const user = userEvent.setup()
    render(<App />)

    const mountIdBefore = screen.getByTestId('chat-mount-id').textContent

    await user.click(screen.getByRole('button', { name: /mock create thread/i }))

    expect(screen.getByTestId('chat-mount-id')).toHaveTextContent(mountIdBefore!)
    expect(screen.getByTestId('chat-thread')).toHaveTextContent('created-thread')
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

  it('uses the main workspace for operational panels instead of the chat blank state', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /mock audit panel/i }))

    expect(screen.getByTestId('workspace-panel')).toHaveTextContent('Workspace audit')
    expect(screen.queryByTestId('chat-thread')).not.toBeInTheDocument()
  })

  it('opens the audit workspace from the top header audit button', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /open audit workspace/i }))

    expect(screen.getByTestId('workspace-panel')).toHaveTextContent('Workspace audit')
    expect(screen.queryByTestId('chat-thread')).not.toBeInTheDocument()
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
