import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GovernancePanel } from './GovernancePanel'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', () => ({ api: { getBudget: vi.fn(), listGovernancePolicies: vi.fn(), createGovernancePolicy: vi.fn() } }))
const api = vi.mocked(apiModule.api)

const policy = { version: 1, is_active: true, created_at: '2026-07-10T00:00:00Z', document: { max_global_tokens: 1000, auto_rca_levels: ['P0'], auto_approved_tools: [] } }

describe('GovernancePanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows global and thread budget totals', async () => {
    api.getBudget.mockResolvedValue({ policy, totals: { total_tokens: 42, estimated_cost_usd: '0.0123' } })
    api.listGovernancePolicies.mockResolvedValue([policy])
    render(<GovernancePanel threadId="thread-1" />)
    expect(await screen.findByText('当前消耗')).toBeInTheDocument()
    expect(screen.getByText('$0.0123')).toBeInTheDocument()
    expect(screen.getByTestId('governance-status-strip')).toBeInTheDocument()
    expect(screen.getByText('策略历史')).toBeInTheDocument()
  })

  it('saves a new version from the budget controls', async () => {
    api.getBudget.mockResolvedValue({ policy, totals: { total_tokens: 0, estimated_cost_usd: '0' } })
    api.listGovernancePolicies.mockResolvedValue([policy])
    api.createGovernancePolicy.mockResolvedValue({ ...policy, version: 2, document: { ...policy.document, max_global_tokens: 2000 } })
    render(<GovernancePanel threadId={null} />)
    const limit = await screen.findByLabelText('Global token limit')
    fireEvent.change(limit, { target: { value: '2000' } })
    fireEvent.click(screen.getByRole('button', { name: '保存为新策略' }))
    await waitFor(() => expect(api.createGovernancePolicy).toHaveBeenCalledWith(expect.objectContaining({ max_global_tokens: 2000 })))
  })
})
