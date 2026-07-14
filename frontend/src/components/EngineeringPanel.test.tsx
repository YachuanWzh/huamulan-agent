import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EngineeringPanel } from './EngineeringPanel'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listThreadTraces: vi.fn(), getTrace: vi.fn(), listEvaluationRuns: vi.fn(),
    compareEvaluationRuns: vi.fn(), diffReplay: vi.fn(), createReplayFork: vi.fn(),
    listSBSTasks: vi.fn(), getSBSTask: vi.fn(), submitSBSReview: vi.fn(), exportSBS: vi.fn(),
  },
}))

const api = vi.mocked(apiModule.api)

describe('EngineeringPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listThreadTraces.mockResolvedValue([{
      trace_id: 'trace-1', total_spans: 2, total_tokens: 42,
      error_count: 0, retry_count: 0, tool_calls: 1, duration_ms: 120,
    }])
    api.getTrace.mockResolvedValue({
      summary: { trace_id: 'trace-1', total_spans: 2, total_tokens: 42, error_count: 0, retry_count: 0, tool_calls: 1, duration_ms: 120 },
      spans: [],
      roots: [{
        orphaned: false,
        span: {
          id: 1, trace_id: 'trace-1', span_id: 'root', thread_id: 'thread-1',
          kind: 'turn', status: 'completed', name: 'user_turn',
          created_at: '2026-07-14T00:00:00Z', duration_ms: 120,
          token_usage: { total_tokens: 42 }, input: { message: 'hello' },
          output: { answer: 'world' }, error: {}, metadata: { agent_mode: 'single' },
        },
        children: [],
      }],
    })
    api.listEvaluationRuns.mockResolvedValue([])
    api.listSBSTasks.mockResolvedValue([])
  })

  it('renders the four tools and drills into a trace tree', async () => {
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    expect(screen.getByRole('tab', { name: 'Trace' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Regression' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Replay diff' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'SBS review' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /trace-1/ }))
    expect(await screen.findByText('user_turn')).toBeInTheDocument()
    expect(screen.getAllByText('120 ms')).toHaveLength(2)
    expect(screen.getByText('1 tools')).toBeInTheDocument()
    expect(screen.getByText('0 retries')).toBeInTheDocument()

    await user.click(screen.getByText('user_turn'))
    expect(screen.getByText('Span ID')).toBeInTheDocument()
    expect(screen.getByText('root')).toBeInTheDocument()
    expect(screen.getByText('Tokens')).toBeInTheDocument()
    expect(screen.getByText('Input')).toBeInTheDocument()
    expect(screen.getByText('Output')).toBeInTheDocument()
    expect(screen.getByText(/hello/)).toBeInTheDocument()
    expect(screen.getByText(/world/)).toBeInTheDocument()
  })

  it('requires a reason before submitting both-bad SBS review', async () => {
    api.listSBSTasks.mockResolvedValue([{ task_id: 'sbs-1', prompt: '诊断超时', status: 'pending', provenance: {} }])
    api.getSBSTask.mockResolvedValue({
      task_id: 'sbs-1', prompt: '诊断超时',
      candidates: [{ label: 'A', output: 'answer A' }, { label: 'B', output: 'answer B' }],
    })
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: 'SBS review' }))
    await user.click(await screen.findByRole('button', { name: /诊断超时/ }))
    await user.selectOptions(await screen.findByLabelText('Winner'), 'both_bad')

    expect(screen.getByRole('button', { name: 'Save review' })).toBeDisabled()
    await user.type(screen.getByLabelText('Reviewer'), 'alice')
    await user.type(screen.getByLabelText('Reason'), '两边都缺少证据')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save review' })).toBeEnabled())
  })
})
