import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IncidentPanel } from './IncidentPanel'
import * as apiModule from '../lib/api'
import type { Incident } from '../lib/api'

vi.mock('../lib/api', () => ({ api: { listIncidents: vi.fn(), updateIncident: vi.fn(), addIncidentAction: vi.fn(), updateIncidentAction: vi.fn() } }))
const api = vi.mocked(apiModule.api)

const incident: Incident = {
  id: 'incident-1', alert_id: 'alert-1', severity: 'critical', title: 'Checkout errors',
  service: 'checkout', status: 'open', owner: null, created_at: '2026-07-10T00:00:00Z',
  updated_at: '2026-07-10T00:00:00Z', timeline: [], actions: [],
}

describe('IncidentPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows active incidents and updates their status', async () => {
    api.listIncidents.mockResolvedValue([incident])
    api.updateIncident.mockResolvedValue({ ...incident, status: 'investigating' })
    render(<IncidentPanel />)

    expect(await screen.findByText('Checkout errors')).toBeInTheDocument()
    expect(screen.getByTestId('incident-command-list')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Status for Checkout errors'), { target: { value: 'investigating' } })
    await waitFor(() => expect(api.updateIncident).toHaveBeenCalledWith('incident-1', { status: 'investigating' }))
    expect(screen.getByText('investigating')).toBeInTheDocument()
  })

  it('creates and completes a recovery action', async () => {
    const action = { id: 'action-1', description: 'Rollback', completed: false, created_at: '2026-07-10T00:00:00Z' }
    const withAction: Incident = { ...incident, actions: [action] }
    api.listIncidents.mockResolvedValue([incident])
    api.addIncidentAction.mockResolvedValue(withAction)
    api.updateIncidentAction.mockResolvedValue({ ...withAction, actions: [{ ...action, completed: true }] })
    render(<IncidentPanel />)
    await screen.findByText('Checkout errors')
    fireEvent.change(screen.getByLabelText('New action for Checkout errors'), { target: { value: 'Rollback' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add action' }))
    await screen.findByText('Rollback')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Complete Rollback' }))
    await waitFor(() => expect(api.updateIncidentAction).toHaveBeenCalledWith('incident-1', 'action-1', true))
  })
})
