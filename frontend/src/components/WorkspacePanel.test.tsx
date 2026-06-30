import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkspacePanel } from './WorkspacePanel'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    replay: vi.fn(),
    deleteThread: vi.fn(),
    listAuditEvents: vi.fn(),
    listToolErrors: vi.fn(),
  },
}))

const mockApi = vi.mocked(apiModule.api)

describe('WorkspacePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uses the main workspace for tool error lookup', async () => {
    mockApi.listAuditEvents.mockResolvedValue([])
    mockApi.listToolErrors.mockResolvedValue([
      {
        id: 12,
        created_at: '2026-06-30T01:00:00+00:00',
        thread_id: 't1',
        tool_call_id: 'call-1',
        tool_name: 'lookup',
        tool_args: { query: 'alpha' },
        attempt: 3,
        max_attempts: 4,
        error_type: 'ValueError',
        error_message: 'bad query',
        will_retry: false,
      },
    ])

    const user = userEvent.setup()
    render(<WorkspacePanel panel="audit" threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /tool errors/i }))

    await waitFor(() => {
      expect(mockApi.listToolErrors).toHaveBeenCalledWith('t1')
      expect(screen.getByRole('region', { name: /operations workspace/i })).toBeInTheDocument()
      expect(screen.getByText('lookup')).toBeInTheDocument()
      expect(screen.getByText('ValueError: bad query')).toBeInTheDocument()
      expect(screen.getByText(/attempt 3 \/ 4/i)).toBeInTheDocument()
      expect(screen.getByText(/"query": "alpha"/i)).toBeInTheDocument()
    })
  })

  it('uses the main workspace for checkpoint replay', async () => {
    mockApi.replay.mockResolvedValue({
      thread_id: 't1',
      states: [
        {
          checkpoint_id: 'checkpoint-1',
          parent_checkpoint_id: null,
          created_at: '2026-06-30T01:00:00+00:00',
          node: 'agent',
          values: {},
          messages: [{ role: 'assistant', content: 'hello' }],
          checkpoint: {},
        },
      ],
    })
    const onReplayState = vi.fn()

    render(
      <WorkspacePanel
        panel="checkpoint"
        threadId="t1"
        onReplayState={onReplayState}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: /replay checkpoint 1/i }))

    expect(mockApi.replay).toHaveBeenCalledWith('t1')
    expect(onReplayState).toHaveBeenCalledWith(
      expect.objectContaining({ checkpoint_id: 'checkpoint-1' }),
    )
  })
})
