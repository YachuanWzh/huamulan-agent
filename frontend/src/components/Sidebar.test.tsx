import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from './Sidebar'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listSkills: vi.fn(),
    reloadSkills: vi.fn(),
    replay: vi.fn(),
    deleteThread: vi.fn(),
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
    expect(screen.getByRole('tab', { name: /skills/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /history/i })).toBeInTheDocument()
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

  it('switches to history tab and shows replay', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [replayState],
    })

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /history/i }))

    await waitFor(() => {
      expect(mockApi.replay).toHaveBeenCalledWith('t1')
    })
  })

  it('replays a checkpoint from history', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [replayState],
    })
    const onReplayState = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onReplayState={onReplayState} />)

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /replay checkpoint 1/i }))

    expect(onReplayState).toHaveBeenCalledWith(replayState)
  })

  it('deletes thread history from the history tab', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [replayState],
    })
    mockApi.deleteThread.mockResolvedValue({ thread_id: 't1', deleted: true })

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /delete history/i }))

    await waitFor(() => {
      expect(mockApi.deleteThread).toHaveBeenCalledWith('t1')
      expect(screen.getByText(/no history for this thread/i)).toBeInTheDocument()
    })
  })

  it('clears history and starts a new thread', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.replay.mockResolvedValue({ thread_id: 't1', states: [] })
    mockApi.deleteThread.mockResolvedValue({ thread_id: 't1', deleted: true })
    const onThreadCleared = vi.fn()

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" onThreadCleared={onThreadCleared} />)

    await user.click(screen.getByRole('tab', { name: /history/i }))
    await user.click(await screen.findByRole('button', { name: /clear and new thread/i }))

    await waitFor(() => {
      expect(mockApi.deleteThread).toHaveBeenCalledWith('t1')
      expect(onThreadCleared).toHaveBeenCalledOnce()
    })
  })
})

