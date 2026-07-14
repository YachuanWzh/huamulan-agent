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
      agentMode,
      onThreadCreated,
    }: {
      threadId: string | null
      replayState?: { checkpoint_id: string } | null
      agentMode: 'single' | 'multi'
      onThreadCreated: () => string
    }) => {
      const [initialThreadId] = React.useState(threadId)
      const [mountId] = React.useState(() => String(++mountCounter))
      return (
        <div>
          <div data-testid="chat-thread">{threadId}</div>
          <div data-testid="chat-agent-mode">{agentMode}</div>
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
    onPanelChange: (panel: 'chat' | 'checkpoint' | 'audit' | 'engineering') => void
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
      <button onClick={() => onPanelChange('engineering')}>Mock engineering panel</button>
    </div>
  ),
}))

vi.mock('./components/WorkspacePanel', () => ({
  WorkspacePanel: ({
    panel,
    threadId,
    agentMode,
  }: {
    panel: 'checkpoint' | 'audit'
    threadId: string | null
    agentMode: 'single' | 'multi'
  }) => (
    <div data-testid="workspace-panel">
      Workspace {panel} {threadId} {agentMode}
    </div>
  ),
}))

vi.mock('./components/EngineeringPanel', () => ({
  EngineeringPanel: ({ agentMode }: { agentMode: 'single' | 'multi' }) => (
    <div data-testid="engineering-panel">Engineering {agentMode}</div>
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
    expect(screen.getByText(/军令:\s*未出征/i)).toBeInTheDocument()

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

  it('passes the global agent mode into Agent Engineering', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'Multi agent' }))
    await user.click(screen.getByRole('button', { name: 'Mock engineering panel' }))

    expect(screen.getByTestId('engineering-panel')).toHaveTextContent('Engineering multi')
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

  it('does not render a top header audit button', () => {
    render(<App />)

    expect(
      screen.queryByRole('button', { name: /open audit workspace/i }),
    ).not.toBeInTheDocument()
  })

  it('exposes the redesigned console shell without changing core controls', () => {
    render(<App />)

    expect(
      screen.getByRole('banner', { name: /木兰控制台/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('main', { name: /对话案台/i }),
    ).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-shell')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: /huamulan-agent/i }),
    ).toBeInTheDocument()
  })

  it('keeps agent mode global for chat and workspace panels', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByTestId('chat-agent-mode')).toHaveTextContent('single')

    await user.click(screen.getByRole('button', { name: /multi agent/i }))

    expect(screen.getByTestId('chat-agent-mode')).toHaveTextContent('multi')

    await user.click(screen.getByRole('button', { name: /mock audit panel/i }))

    expect(screen.getByTestId('workspace-panel')).toHaveTextContent('multi')
  })

  it('presents the four-market kit rail in the header', () => {
    render(<App />)

    expect(screen.getByText(/东市\s*骏马/)).toBeInTheDocument()
    expect(screen.getByText(/西市\s*鞍鞯/)).toBeInTheDocument()
    expect(screen.getByText(/南市\s*辔头/)).toBeInTheDocument()
    expect(screen.getByText(/北市\s*长鞭/)).toBeInTheDocument()
  })
})
