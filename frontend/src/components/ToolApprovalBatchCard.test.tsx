import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolApprovalBatchCard } from './ToolApprovalBatchCard'
import type { ToolCallApproval } from '../lib/api'

const approvals: ToolCallApproval[] = [
  {
    approval_id: 'a1',
    tool_call_id: 'tc1',
    name: 'resolve_current_time',
    args: {},
  },
  {
    approval_id: 'a2',
    tool_call_id: 'tc2',
    name: 'get_weather',
    args: { city: 'Shanghai' },
  },
]

describe('ToolApprovalBatchCard', () => {
  it('renders a single batch summary for multiple approvals', () => {
    render(<ToolApprovalBatchCard approvals={approvals} onSubmit={vi.fn()} />)

    expect(screen.getByText(/2 tool approvals required/i)).toBeInTheDocument()
    expect(screen.getByText('resolve_current_time')).toBeInTheDocument()
    expect(screen.getByText('get_weather')).toBeInTheDocument()
  })

  it('submits all approvals as approved by default', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ToolApprovalBatchCard approvals={approvals} onSubmit={onSubmit} />)

    await user.click(screen.getByRole('button', { name: /submit approvals/i }))

    expect(onSubmit).toHaveBeenCalledWith([
      { approval_id: 'a1', approved: true },
      { approval_id: 'a2', approved: true },
    ])
  })

  it('allows denying an individual approval before submitting', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<ToolApprovalBatchCard approvals={approvals} onSubmit={onSubmit} />)

    await user.click(screen.getByRole('button', { name: /deny get_weather/i }))
    await user.click(screen.getByRole('button', { name: /submit approvals/i }))

    expect(onSubmit).toHaveBeenCalledWith([
      { approval_id: 'a1', approved: true },
      { approval_id: 'a2', approved: false },
    ])
  })
})
