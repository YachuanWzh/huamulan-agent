import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from './Sidebar'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listSkills: vi.fn(),
    reloadSkills: vi.fn(),
    listThreads: vi.fn(),
    replay: vi.fn(),
    deleteThread: vi.fn(),
    clearThreads: vi.fn(),
    listAuditEvents: vi.fn(),
  },
}))

const mockApi = vi.mocked(apiModule.api)

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  const replayState = {
    checkpoint_id: 'checkpoint-1',
    parent_checkpoint_id: null,
    created_at: '2026-06-29T04:00:00+00:00',
    node: 'agent',
    values: {},
    messages: [{ role: 'user' as const, content: 'Hi' }],
    checkpoint: {},
  }

  it('renders skills tab by default', () => {
    mockApi.listSkills.mockResolvedValue([])
    render(<Sidebar threadId="t1" />)
    expect(screen.getByTestId('sidebar-shell')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /skills/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /history/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /checkpoint/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /audit/i })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /tool errors/i })).not.toBeInTheDocument()
  })

  it('notifies the app when workspace navigation changes', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({ thread_id: 't1', states: [] })
    mockApi.listAuditEvents.mockResolvedValue([])
    const onPanelChange = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onPanelChange={onPanelChange} />)

    await user.click(screen.getByRole('tab', { name: /checkpoint/i }))
    await user.click(screen.getByRole('tab', { name: /audit/i }))
    await user.click(screen.getByRole('tab', { name: /skills/i }))

    expect(onPanelChange).toHaveBeenNthCalledWith(1, 'checkpoint')
    expect(onPanelChange).toHaveBeenNthCalledWith(2, 'audit')
    expect(onPanelChange).toHaveBeenNthCalledWith(3, 'chat')
  })

  it('displays skills after loading', async () => {
    mockApi.listSkills.mockResolvedValue([
      {
        name: 'resolve-time',
        description: 'Resolve current time',
        tool_names: ['resolve_current_time'],
        path: '/skills/resolve-time',
      },
    ])
    render(<Sidebar threadId="t1" />)

    await waitFor(() => {
      expect(screen.getByText('resolve-time')).toBeInTheDocument()
      expect(screen.getByText('Resolve current time')).toBeInTheDocument()
    })
  })

  it('reloads skills when reload button clicked', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.reloadSkills.mockResolvedValue([
      {
        name: 'new-skill',
        description: 'A new skill',
        tool_names: ['tool1'],
        path: '/skills/new-skill',
      },
    ])

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /reload/i }))

    await waitFor(() => {
      expect(mockApi.reloadSkills).toHaveBeenCalled()
    })
  })

  it('switches to checkpoint tab without duplicating replay controls in the sidebar', async () => {
    mockApi.listSkills.mockResolvedValue([])
    const onPanelChange = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onPanelChange={onPanelChange} />)

    await user.click(screen.getByRole('tab', { name: /checkpoint/i }))

    expect(onPanelChange).toHaveBeenCalledWith('checkpoint')
    expect(mockApi.replay).not.toHaveBeenCalledWith('t1')
    expect(screen.queryByRole('button', { name: /replay checkpoint/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete checkpoints/i })).not.toBeInTheDocument()
  })

  it('switches to audit tab without duplicating audit results in the sidebar', async () => {
    mockApi.listSkills.mockResolvedValue([])
    const onPanelChange = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onPanelChange={onPanelChange} />)

    await user.click(screen.getByRole('tab', { name: /audit/i }))

    expect(onPanelChange).toHaveBeenCalledWith('audit')
    expect(mockApi.listAuditEvents).not.toHaveBeenCalledWith('t1')
    expect(screen.queryByText('instruction_override')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /refresh/i })).not.toBeInTheDocument()
  })

  it('shows thread sessions in history and opens the selected conversation', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.listThreads.mockResolvedValue([
      {
        thread_id: 'thread-2',
        updated_at: '2026-06-29T05:00:00+00:00',
      },
      {
        thread_id: 'thread-1',
        updated_at: '2026-06-29T04:00:00+00:00',
      },
    ])
    mockApi.replay.mockResolvedValue({
      thread_id: 'thread-2',
      states: [{ ...replayState, checkpoint_id: 'checkpoint-2' }],
    })
    const onReplayState = vi.fn()
    const onThreadSelected = vi.fn()

    const user = userEvent.setup()
    render(
      <Sidebar
        threadId="t1"
        onReplayState={onReplayState}
        onThreadSelected={onThreadSelected}
      />,
    )

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /open session thread-2/i }))

    expect(mockApi.listThreads).toHaveBeenCalled()
    expect(mockApi.replay).toHaveBeenCalledWith('thread-2')
    expect(onThreadSelected).toHaveBeenCalledWith('thread-2')
    expect(onReplayState).toHaveBeenCalledWith(
      expect.objectContaining({ checkpoint_id: 'checkpoint-2' }),
    )
  })

  it('opens history sessions without restoring active tool approval cards', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.listThreads.mockResolvedValue([
      {
        thread_id: 'thread-2',
        updated_at: '2026-06-29T05:00:00+00:00',
      },
    ])
    mockApi.replay.mockResolvedValue({
      thread_id: 'thread-2',
      states: [
        {
          ...replayState,
          checkpoint_id: 'checkpoint-2',
          values: {
            pending_approvals: [
              {
                approval_id: 'approval-1',
                tool_call_id: 'tool-1',
                name: 'resolve_current_time',
                args: {},
              },
            ],
          },
        },
      ],
    })
    const onReplayState = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="thread-1" onReplayState={onReplayState} />)

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /open session thread-2/i }))

    expect(onReplayState).toHaveBeenCalledWith(
      expect.objectContaining({
        checkpoint_id: 'checkpoint-2',
        values: expect.objectContaining({ pending_approvals: [] }),
      }),
    )
  })

  it('deletes a single conversation from history', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.listThreads.mockResolvedValue([
      {
        thread_id: 'thread-2',
        updated_at: '2026-06-29T05:00:00+00:00',
      },
      {
        thread_id: 'thread-1',
        updated_at: '2026-06-29T04:00:00+00:00',
      },
    ])
    mockApi.deleteThread.mockResolvedValue({ thread_id: 'thread-2', deleted: true })

    const user = userEvent.setup()
    render(<Sidebar threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /delete session thread-2/i }))

    await waitFor(() => {
      expect(mockApi.deleteThread).toHaveBeenCalledWith('thread-2')
      expect(screen.queryByText('thread-2')).not.toBeInTheDocument()
      expect(screen.getByText('thread-1')).toBeInTheDocument()
    })
  })

  it('clears all conversations from history and starts a new thread', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.listThreads.mockResolvedValue([
      {
        thread_id: 'thread-2',
        updated_at: '2026-06-29T05:00:00+00:00',
      },
    ])
    mockApi.clearThreads.mockResolvedValue({ thread_ids: ['thread-2'], deleted: 1 })
    const onThreadCleared = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="thread-2" onThreadCleared={onThreadCleared} />)

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /clear history/i }))

    await waitFor(() => {
      expect(mockApi.clearThreads).toHaveBeenCalled()
      expect(screen.getByText(/no conversation history/i)).toBeInTheDocument()
      expect(onThreadCleared).toHaveBeenCalledOnce()
    })
  })

})

