import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolApprovalCard } from './ToolApprovalCard'
import type { ToolCallApproval } from '../lib/api'

const approval: ToolCallApproval = {
  approval_id: 'a1',
  tool_call_id: 'tc1',
  name: 'resolve_current_time',
  args: { timezone: 'UTC' },
}

describe('ToolApprovalCard', () => {
  it('renders tool name and args', () => {
    render(
      <ToolApprovalCard approval={approval} onApprove={vi.fn()} onDeny={vi.fn()} />,
    )
    expect(screen.getByText('resolve_current_time')).toBeInTheDocument()
    expect(screen.getByText(/timezone/)).toBeInTheDocument()
    expect(screen.getByText(/UTC/)).toBeInTheDocument()
  })

  it('renders approve and deny buttons', () => {
    render(
      <ToolApprovalCard approval={approval} onApprove={vi.fn()} onDeny={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /deny/i })).toBeInTheDocument()
  })

  it('calls onApprove with approval_id when approve clicked', async () => {
    const onApprove = vi.fn()
    const user = userEvent.setup()
    render(
      <ToolApprovalCard approval={approval} onApprove={onApprove} onDeny={vi.fn()} />,
    )

    await user.click(screen.getByRole('button', { name: /approve/i }))
    expect(onApprove).toHaveBeenCalledWith('a1')
  })

  it('calls onDeny with approval_id when deny clicked', async () => {
    const onDeny = vi.fn()
    const user = userEvent.setup()
    render(
      <ToolApprovalCard approval={approval} onApprove={vi.fn()} onDeny={onDeny} />,
    )

    await user.click(screen.getByRole('button', { name: /deny/i }))
    expect(onDeny).toHaveBeenCalledWith('a1')
  })

  it('shows empty object when args is empty', () => {
    const noArgs: ToolCallApproval = {
      ...approval,
      args: {},
    }
    render(
      <ToolApprovalCard approval={noArgs} onApprove={vi.fn()} onDeny={vi.fn()} />,
    )
    expect(screen.getByText('{}')).toBeInTheDocument()
  })
})
