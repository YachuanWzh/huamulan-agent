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
  },
}))

const mockApi = vi.mocked(apiModule.api)

describe('Sidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

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
      states: [{ messages: [{ type: 'human', content: 'Hi' }] }],
    })

    const user = userEvent.setup()
    render(<Sidebar threadId="t1" />)

    await user.click(screen.getByRole('tab', { name: /history/i }))

    await waitFor(() => {
      expect(mockApi.replay).toHaveBeenCalledWith('t1')
    })
  })
})
