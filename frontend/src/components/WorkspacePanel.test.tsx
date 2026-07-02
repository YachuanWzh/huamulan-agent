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
    listExecutionLogs: vi.fn(),
    getExecutionSummary: vi.fn(),
  },
}))

const mockApi = vi.mocked(apiModule.api)

describe('WorkspacePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders audit summary, token usage, and grouped retry chain', async () => {
    mockApi.getExecutionSummary.mockResolvedValue({
      thread_id: 't1',
      total_events: 4,
      total_tokens: 120,
      prompt_tokens: 80,
      completion_tokens: 40,
      tool_calls: 1,
      tool_errors: 2,
      tool_retries: 2,
      security_events: 0,
      total_duration_ms: 320,
    })
    mockApi.listExecutionLogs.mockResolvedValue([
      {
        id: 1,
        created_at: '2026-06-30T01:00:00Z',
        thread_id: 't1',
        event_type: 'llm',
        status: 'completed',
        name: 'agent',
        input: {},
        output: { content: 'thinking' },
        error: {},
        duration_ms: 90,
        token_usage: { prompt_tokens: 80, completion_tokens: 40, total_tokens: 120 },
        metadata: {},
      },
      {
        id: 2,
        created_at: '2026-06-30T01:00:01Z',
        thread_id: 't1',
        event_type: 'tool_retry',
        status: 'retrying',
        name: 'lookup',
        input: { query: 'alpha' },
        output: {},
        error: { type: 'ValueError', message: 'bad query 1' },
        duration_ms: 20,
        token_usage: {},
        metadata: { tool_call_id: 'call-1', attempt: 1, max_attempts: 3, will_retry: true },
      },
      {
        id: 3,
        created_at: '2026-06-30T01:00:02Z',
        thread_id: 't1',
        event_type: 'tool_retry',
        status: 'retrying',
        name: 'lookup',
        input: { query: 'alpha' },
        output: {},
        error: { type: 'ValueError', message: 'bad query 2' },
        duration_ms: 25,
        token_usage: {},
        metadata: { tool_call_id: 'call-1', attempt: 2, max_attempts: 3, will_retry: true },
      },
      {
        id: 4,
        created_at: '2026-06-30T01:00:03Z',
        thread_id: 't1',
        event_type: 'tool',
        status: 'completed',
        name: 'lookup',
        input: { query: 'alpha' },
        output: { content: '{"answer":"ok"}' },
        error: {},
        duration_ms: 40,
        token_usage: {},
        metadata: { tool_call_id: 'call-1', attempt: 3 },
      },
    ])

    render(<WorkspacePanel panel="audit" threadId="t1" />)

    expect(await screen.findByText('120')).toBeInTheDocument()
    expect(screen.getByText(/Token 总量/i)).toBeInTheDocument()
    expect(screen.getByText(/Prompt 80 \/ Completion 40/i)).toBeInTheDocument()
    expect(screen.getByText(/lookup 重试链/i)).toBeInTheDocument()
    expect(screen.getByText(/第 1 次失败/i)).toBeInTheDocument()
    expect(screen.getByText(/第 2 次失败/i)).toBeInTheDocument()
    expect(screen.getByText(/第 3 次完成/i)).toBeInTheDocument()
  })

  it('uses the main workspace for execution audit lookup', async () => {
    mockApi.getExecutionSummary.mockResolvedValue({
      thread_id: 't1',
      total_events: 0,
      total_tokens: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      tool_calls: 0,
      tool_errors: 0,
      tool_retries: 0,
      security_events: 0,
      total_duration_ms: 0,
    })
    mockApi.listExecutionLogs.mockResolvedValue([])

    render(<WorkspacePanel panel="audit" threadId="t1" />)

    await waitFor(() => {
      expect(mockApi.getExecutionSummary).toHaveBeenCalledWith('t1')
      expect(mockApi.listExecutionLogs).toHaveBeenCalledWith('t1')
      expect(screen.getByRole('region', { name: /行军案台/i })).toBeInTheDocument()
      expect(screen.getByText(/当前军令暂无执行日志/i)).toBeInTheDocument()
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

    await userEvent.click(await screen.findByRole('button', { name: /回放检查点 1/i }))

    expect(mockApi.replay).toHaveBeenCalledWith('t1')
    expect(onReplayState).toHaveBeenCalledWith(
      expect.objectContaining({ checkpoint_id: 'checkpoint-1' }),
    )
  })
})
