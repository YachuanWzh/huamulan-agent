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

  it('switches to checkpoint tab and shows replay states', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [replayState],
    })

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /checkpoint/i }))

    await waitFor(() => {
      expect(mockApi.replay).toHaveBeenCalledWith('t1')
      expect(screen.getByText(/thread replay/i)).toBeInTheDocument()
    })
  })

  it('replays a checkpoint from the checkpoint tab', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [replayState],
    })
    const onReplayState = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onReplayState={onReplayState} />)

    await user.click(screen.getByRole('tab', { name: /checkpoint/i }))
    await user.click(await screen.findByRole('button', { name: /replay checkpoint 1/i }))

    expect(onReplayState).toHaveBeenCalledWith(replayState)
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

  it('deletes thread checkpoints from the checkpoint tab', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [replayState],
    })
    mockApi.deleteThread.mockResolvedValue({ thread_id: 't1', deleted: true })

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /checkpoint/i }))
    await user.click(await screen.findByRole('button', { name: /delete checkpoints/i }))

    await waitFor(() => {
      expect(mockApi.deleteThread).toHaveBeenCalledWith('t1')
      expect(screen.getByText(/no checkpoints for this thread/i)).toBeInTheDocument()
    })
  })

  it('clears history and starts a new thread', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({ thread_id: 't1', states: [] })
    mockApi.deleteThread.mockResolvedValue({ thread_id: 't1', deleted: true })
    const onThreadCleared = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onThreadCleared={onThreadCleared} />)

    await user.click(screen.getByRole('tab', { name: /checkpoint/i }))
    await user.click(await screen.findByRole('button', { name: /clear and new thread/i }))

    await waitFor(() => {
      expect(mockApi.deleteThread).toHaveBeenCalledWith('t1')
      expect(onThreadCleared).toHaveBeenCalledOnce()
    })
  })

  it('shows audit events for the current thread', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.listAuditEvents.mockResolvedValue([
      {
        id: 7,
        created_at: '2026-06-29T04:00:00+00:00',
        thread_id: 't1',
        source: 'prompt',
        category: 'instruction_override',
        severity: 'HIGH',
        reason: 'User message attempts to override prior or system instructions.',
        subject: 'ignore previous instructions',
        metadata: { prompt_guard_blocked: true },
      },
    ])

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /audit/i }))

    await waitFor(() => {
      expect(mockApi.listAuditEvents).toHaveBeenCalledWith('t1')
      expect(screen.getByText('instruction_override')).toBeInTheDocument()
      expect(screen.getByText('HIGH')).toBeInTheDocument()
    })
  })
})

