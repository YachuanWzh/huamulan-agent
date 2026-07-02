import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
    listSkills: vi.fn(),
    listSkillEvaluationDatasets: vi.fn(),
    runSkillEvaluation: vi.fn(),
    runSkillEvaluationStream: vi.fn(),
    resetSkillEvaluations: vi.fn(),
  },
}))

const mockApi = vi.mocked(apiModule.api)

describe('WorkspacePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listSkillEvaluationDatasets.mockResolvedValue([
      { name: 'claw_eval_smoke', path: 'claw_eval_smoke', label: 'ClawEval smoke' },
      { name: 'golden_dataset', path: 'golden_dataset', label: 'Golden dataset' },
      { name: 'e2e_dateset', path: 'e2e_dateset', label: 'E2E dataset' },
    ])
  })

  afterEach(() => {
    vi.restoreAllMocks()
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

  it('renders skill evaluation scorecards in the main workspace', async () => {
    mockApi.listSkills.mockResolvedValue([
      {
        name: 'resolve-time',
        description: 'Resolve current time',
        tool_names: ['resolve_current_time'],
        path: '/skills/resolve-time',
        loaded: false,
        evaluation: {
          overall_score: 0.91,
          description_tokens: 12,
          skill_md_lines: 35,
          python_lines: 80,
          max_cyclomatic_complexity: 4,
          tool_count: 1,
        },
        latest_evaluation: {
          id: 3,
          created_at: '2026-07-02T01:00:00Z',
          skill_name: 'resolve-time',
          overall_score: 0.72,
          routing_score: 0.8,
          runtime_score: null,
          usage_score: null,
          static_score: 0.6,
          source: 'golden:golden.jsonl',
          report: {},
        },
      },
    ])

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    expect(await screen.findByText('Skill Evaluation')).toBeInTheDocument()
    expect(screen.getByLabelText('Golden dataset')).toHaveValue('claw_eval_smoke')
    expect(screen.getByRole('option', { name: 'E2E dataset' })).toBeInTheDocument()
    expect(screen.getByText('resolve-time')).toBeInTheDocument()
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('golden:golden.jsonl')).toBeInTheDocument()
    expect(screen.getByText('Complexity')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
    expect(screen.getByText('80 lines')).toBeInTheDocument()
  })

  it('exposes the full skill description on hover while the card preview stays compact', async () => {
    const description =
      'A very long skill description that needs to be clamped in the scorecard preview but remain available as hover detail.'
    mockApi.listSkills.mockResolvedValue([
      {
        name: 'long-skill',
        description,
        tool_names: [],
        path: '/skills/long-skill',
        loaded: false,
        evaluation: {
          overall_score: 0.8,
          description_tokens: 32,
          skill_md_lines: 20,
          python_lines: 0,
          max_cyclomatic_complexity: 0,
          tool_count: 0,
        },
      },
    ])

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    expect(await screen.findByText(description)).toHaveAttribute('title', description)
  })

  it('runs a golden dataset evaluation and refreshes skill scores', async () => {
    mockApi.listSkills
      .mockResolvedValueOnce([
        {
          name: 'resolve-time',
          description: 'Resolve current time',
          tool_names: ['resolve_current_time'],
          path: '/skills/resolve-time',
          loaded: false,
          evaluation: {
            overall_score: 0.91,
            description_tokens: 12,
            skill_md_lines: 35,
            python_lines: 80,
            max_cyclomatic_complexity: 4,
            tool_count: 1,
          },
        },
      ])
      .mockResolvedValueOnce([
        {
          name: 'resolve-time',
          description: 'Resolve current time',
          tool_names: ['resolve_current_time'],
          path: '/skills/resolve-time',
          loaded: false,
          evaluation: {
            overall_score: 0.91,
            description_tokens: 12,
            skill_md_lines: 35,
            python_lines: 80,
            max_cyclomatic_complexity: 4,
            tool_count: 1,
          },
          latest_evaluation: {
            id: 4,
            created_at: '2026-07-02T01:10:00Z',
            skill_name: 'resolve-time',
            overall_score: 0.88,
            routing_score: 1,
            runtime_score: null,
            usage_score: null,
            static_score: 0.7,
            source: 'golden:new.jsonl',
            report: {},
          },
        },
      ])
    mockApi.runSkillEvaluationStream.mockImplementation(async function* () {
      yield {
        type: 'done',
        mode: 'quick',
        source: 'golden:new.jsonl',
        total: 1,
        completed: 1,
        percent: 100,
        results: [
          {
            id: 4,
            created_at: '2026-07-02T01:10:00Z',
            skill_name: 'resolve-time',
            overall_score: 0.88,
            routing_score: 1,
            runtime_score: null,
            usage_score: null,
            static_score: 0.7,
            source: 'golden:new.jsonl',
            report: {},
          },
        ],
      }
    })
    const user = userEvent.setup()

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    await user.selectOptions(await screen.findByLabelText('Golden dataset'), 'golden_dataset')
    await user.click(screen.getByRole('button', { name: '快速巡检' }))

    expect(mockApi.runSkillEvaluationStream).toHaveBeenCalledWith({
      golden_path: 'golden_dataset',
      evaluation_mode: 'quick',
    })
    expect(await screen.findByText('88%')).toBeInTheDocument()
    expect(screen.getAllByText('golden:new.jsonl').length).toBeGreaterThan(0)
  })

  it('shows case progress and starts end-to-end evaluation from the second button', async () => {
    mockApi.listSkills.mockResolvedValue([
      {
        name: 'resolve-time',
        description: 'Resolve current time',
        tool_names: ['resolve_current_time'],
        path: '/skills/resolve-time',
        loaded: false,
        evaluation: {
          overall_score: 0.91,
          description_tokens: 12,
          skill_md_lines: 35,
          python_lines: 80,
          max_cyclomatic_complexity: 4,
          tool_count: 1,
        },
      },
      {
        name: 'weather',
        description: 'Weather lookup',
        tool_names: ['weather'],
        path: '/skills/weather',
        loaded: false,
        evaluation: {
          overall_score: 0.76,
          description_tokens: 18,
          skill_md_lines: 45,
          python_lines: 100,
          max_cyclomatic_complexity: 5,
          tool_count: 1,
        },
      },
    ])
    mockApi.runSkillEvaluationStream.mockImplementation(async function* () {
      yield {
        type: 'started',
        mode: 'e2e',
        source: 'golden:new.jsonl',
        total: 2,
        completed: 0,
      }
      yield {
        type: 'case_progress',
        mode: 'e2e',
        source: 'golden:new.jsonl',
        total: 2,
        completed: 1,
        percent: 50,
        case_id: 'rt-001',
        expected_skills: ['resolve-time'],
        selected_skills: ['resolve-time'],
        tool_completed: true,
        tool_failed: false,
        detail: {
          case_id: 'rt-001',
          mode: 'e2e',
          query: 'resolve current time',
          turns: [],
          expected_skills: ['resolve-time'],
          selected_skills: ['resolve-time'],
          expected_tool_calls: [{ tool: 'resolve_current_time', args_contains: {} }],
          actual_tool_calls: [{ name: 'resolve_current_time', args: { timezone: 'Asia/Shanghai' } }],
          final_answer: '现在是 2026-07-02。',
          checks: [
            {
              name: 'tool_arguments',
              stage: 'tool',
              passed: false,
              expected: [{ tool: 'resolve_current_time', args_contains: { timezone: 'UTC' } }],
              actual: [{ name: 'resolve_current_time', args: { timezone: 'Asia/Shanghai' } }],
              reason: '工具参数不符合期望',
            },
          ],
          diagnosis: {
            stage: 'tool',
            severity: 'medium',
            summary: '工具调用或参数环节可能出错：工具参数不符合期望',
            signals: ['tool.tool_arguments: 工具参数不符合期望'],
            recommendation: '检查工具选择 prompt、工具 schema、参数抽取和工具执行日志。',
          },
          judge: {
            score: 0.42,
            passed: false,
            failure_stage: 'prompt_or_reasoning',
            reason: '回答没有解释工具参数偏差',
            evidence: ['timezone mismatch'],
            recommendation: '强化最终回答 prompt',
            model: 'deepseek-v4-pro',
            available: true,
          },
          log_summary: [
            {
              event_type: 'tool',
              status: 'completed',
              name: 'resolve_current_time',
              input: { timezone: 'Asia/Shanghai' },
              output: {},
              error: {},
              metadata: {},
            },
          ],
        },
      }
      yield {
        type: 'done',
        mode: 'e2e',
        source: 'golden:new.jsonl',
        total: 2,
        completed: 2,
        percent: 100,
        report: {
          skills: [],
          safety: {
            total_cases: 2,
            attack_block_rate: 1,
            unsafe_tool_call_rate: 0,
            secret_leak_rate: 0,
            security_event_precision: 1,
          },
          tools: {
            total_cases: 2,
            tool_selection_accuracy: 1,
            argument_fidelity: 0.5,
            forbidden_tool_violation_rate: 0,
          },
          answers: {
            total_cases: 2,
            answer_contains_rate: 1,
            forbidden_answer_violation_rate: 0,
          },
          case_details: [
            {
              case_id: 'rt-001',
              mode: 'e2e',
              query: 'resolve current time',
              turns: [],
              expected_skills: ['resolve-time'],
              selected_skills: ['resolve-time'],
              expected_tool_calls: [{ tool: 'resolve_current_time', args_contains: {} }],
              actual_tool_calls: [
                { name: 'resolve_current_time', args: { timezone: 'Asia/Shanghai' } },
              ],
              final_answer: '现在是 2026-07-02。',
              checks: [
                {
                  name: 'tool_arguments',
                  stage: 'tool',
                  passed: false,
                  expected: [{ tool: 'resolve_current_time', args_contains: { timezone: 'UTC' } }],
                  actual: [
                    { name: 'resolve_current_time', args: { timezone: 'Asia/Shanghai' } },
                  ],
                  reason: '工具参数不符合期望',
                },
              ],
              diagnosis: {
                stage: 'tool',
                severity: 'medium',
                summary: '工具调用或参数环节可能出错：工具参数不符合期望',
                signals: ['tool.tool_arguments: 工具参数不符合期望'],
                recommendation: '检查工具选择 prompt、工具 schema、参数抽取和工具执行日志。',
              },
              judge: {
                score: 0.42,
                passed: false,
                failure_stage: 'prompt_or_reasoning',
                reason: '回答没有解释工具参数偏差',
                evidence: ['timezone mismatch'],
                recommendation: '强化最终回答 prompt',
                model: 'deepseek-v4-pro',
                available: true,
              },
              log_summary: [
                {
                  event_type: 'tool',
                  status: 'completed',
                  name: 'resolve_current_time',
                  input: { timezone: 'Asia/Shanghai' },
                  output: {},
                  error: {},
                  metadata: {},
                },
              ],
            },
          ],
        },
        results: [
          {
            id: 4,
            created_at: '2026-07-02T01:10:00Z',
            skill_name: 'resolve-time',
            overall_score: 0.88,
            routing_score: 1,
            runtime_score: 1,
            usage_score: null,
            static_score: 0.7,
            source: 'golden:new.jsonl',
            report: {},
          },
        ],
      }
    })
    const user = userEvent.setup()

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    await user.selectOptions(await screen.findByLabelText('Golden dataset'), 'golden_dataset')
    await user.click(screen.getByRole('button', { name: '实战测评' }))

    expect(mockApi.runSkillEvaluationStream).toHaveBeenCalledWith({
      golden_path: 'golden_dataset',
      evaluation_mode: 'e2e',
    })
    expect(await screen.findByRole('progressbar', { name: /Skill evaluation progress/i }))
      .toHaveAttribute('aria-valuenow', '100')
    expect(screen.getByText(/2 \/ 2/)).toBeInTheDocument()
    expect(screen.getAllByText(/实战测评/).length).toBeGreaterThan(0)
    expect(screen.getByText('88%')).toBeInTheDocument()
    expect(screen.getAllByText('golden:new.jsonl').length).toBeGreaterThan(0)
    expect(screen.getByText('ClawEval')).toBeInTheDocument()
    expect(screen.getByText('Attack Block')).toBeInTheDocument()
    expect(screen.getAllByText('100%').length).toBeGreaterThan(0)
    expect(screen.getByText('Argument Fidelity')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('Evaluation Details')).toBeInTheDocument()
    expect(screen.getByText(/rt-001/)).toBeInTheDocument()
    expect(screen.getAllByText('tool').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/工具参数不符合期望/).length).toBeGreaterThan(0)
    expect(screen.getByText(/deepseek-v4-pro/)).toBeInTheDocument()
    expect(screen.getByText(/强化最终回答 prompt/)).toBeInTheDocument()
  })

  it('shows skill evaluation errors when the golden dataset cannot be loaded', async () => {
    mockApi.listSkills.mockResolvedValue([])
    mockApi.runSkillEvaluationStream.mockImplementation(async function* () {
      yield {
        type: 'started',
        mode: 'quick',
        source: 'golden:missing.jsonl',
        total: 0,
        completed: 0,
      }
      throw new Error('API error 404: {"detail":"Golden dataset not found: missing"}')
    })
    const user = userEvent.setup()

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    await user.selectOptions(await screen.findByLabelText('Golden dataset'), '__custom__')
    await user.type(screen.getByLabelText('Custom dataset path'), 'missing')
    await user.click(screen.getByRole('button', { name: '快速巡检' }))

    expect(await screen.findByText(/Golden dataset not found: missing/)).toBeInTheDocument()
  })

  it('keeps custom dataset controls inside the aligned evaluation control group', async () => {
    mockApi.listSkills.mockResolvedValue([])
    const user = userEvent.setup()

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    await user.selectOptions(await screen.findByLabelText('Golden dataset'), '__custom__')

    const customInput = screen.getByLabelText('Custom dataset path')
    const controls = customInput.closest('.skill-evaluation-controls')

    expect(controls).not.toBeNull()
    expect(controls?.querySelectorAll('button')).toHaveLength(4)
  })

  it('does not reset skill scores when confirmation is cancelled', async () => {
    mockApi.listSkills.mockResolvedValue([
      {
        name: 'resolve-time',
        description: 'Resolve current time',
        tool_names: ['resolve_current_time'],
        path: '/skills/resolve-time',
        loaded: false,
        evaluation: {
          overall_score: 0.91,
          description_tokens: 12,
          skill_md_lines: 35,
          python_lines: 80,
          max_cyclomatic_complexity: 4,
          tool_count: 1,
        },
        latest_evaluation: {
          id: 3,
          created_at: '2026-07-02T01:00:00Z',
          skill_name: 'resolve-time',
          overall_score: 0.72,
          routing_score: 0.8,
          runtime_score: null,
          usage_score: null,
          static_score: 0.6,
          source: 'golden:golden.jsonl',
          report: {},
        },
      },
    ])
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    await user.click(await screen.findByRole('button', { name: '重置' }))

    expect(window.confirm).toHaveBeenCalled()
    expect(mockApi.resetSkillEvaluations).not.toHaveBeenCalled()
  })

  it('resets skill scores after confirmation and reloads the static scorecards', async () => {
    mockApi.listSkills
      .mockResolvedValueOnce([
        {
          name: 'resolve-time',
          description: 'Resolve current time',
          tool_names: ['resolve_current_time'],
          path: '/skills/resolve-time',
          loaded: false,
          evaluation: {
            overall_score: 0.91,
            description_tokens: 12,
            skill_md_lines: 35,
            python_lines: 80,
            max_cyclomatic_complexity: 4,
            tool_count: 1,
          },
          latest_evaluation: {
            id: 3,
            created_at: '2026-07-02T01:00:00Z',
            skill_name: 'resolve-time',
            overall_score: 0.72,
            routing_score: 0.8,
            runtime_score: null,
            usage_score: null,
            static_score: 0.6,
            source: 'golden:golden.jsonl',
            report: {},
          },
        },
      ])
      .mockResolvedValueOnce([
        {
          name: 'resolve-time',
          description: 'Resolve current time',
          tool_names: ['resolve_current_time'],
          path: '/skills/resolve-time',
          loaded: false,
          evaluation: {
            overall_score: 0.91,
            description_tokens: 12,
            skill_md_lines: 35,
            python_lines: 80,
            max_cyclomatic_complexity: 4,
            tool_count: 1,
          },
          latest_evaluation: null,
        },
      ])
    mockApi.resetSkillEvaluations.mockResolvedValue({ deleted: 3, results: [] })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()

    render(<WorkspacePanel panel="skills" threadId="t1" />)

    expect(await screen.findByText('72%')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重置' }))

    expect(mockApi.resetSkillEvaluations).toHaveBeenCalledOnce()
    expect(await screen.findByText('91%')).toBeInTheDocument()
    expect(screen.queryByText('golden:golden.jsonl')).not.toBeInTheDocument()
  })
})
