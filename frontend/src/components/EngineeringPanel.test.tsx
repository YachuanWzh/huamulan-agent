import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EngineeringPanel } from './EngineeringPanel'
import * as apiModule from '../lib/api'

vi.mock('../lib/api', () => ({
  api: {
    listThreadTraces: vi.fn(), getTrace: vi.fn(), listEvaluationRuns: vi.fn(),
    compareEvaluationRuns: vi.fn(), diffReplay: vi.fn(), createReplayFork: vi.fn(),
    listSkillEvaluationDatasets: vi.fn(), runSkillEvaluationStream: vi.fn(),
    listSBSTasks: vi.fn(), getSBSTask: vi.fn(), submitSBSReview: vi.fn(), exportSBS: vi.fn(),
    createSBSTask: vi.fn(), getSBSRunOptions: vi.fn(), runSBSCandidates: vi.fn(),
  },
}))

const api = vi.mocked(apiModule.api)

describe('EngineeringPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks()
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
    api.listSkillEvaluationDatasets.mockResolvedValue([
      { name: 'claw_eval_smoke', path: 'claw_eval_smoke', label: 'Smoke dataset' },
    ])
    api.listSBSTasks.mockResolvedValue([])
    api.getSBSRunOptions.mockResolvedValue({
      default_model: 'model-one', known_models: ['model-one', 'model-two'],
      agent_modes: ['single', 'multi'],
    })
  })

  it('renders the four tools and drills into a trace tree', async () => {
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    expect(screen.getByRole('tab', { name: '链路追踪' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '回归评测' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '回放差异' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'SBS 评审' })).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /trace-1/ }))
    expect(await screen.findByText('user_turn')).toBeInTheDocument()
    expect(screen.getAllByText('120 ms')).toHaveLength(2)
    expect(screen.getByText('1 次工具调用')).toBeInTheDocument()
    expect(screen.getByText('0 次重试')).toBeInTheDocument()

    await user.click(screen.getByText('user_turn'))
    expect(screen.getByText('执行片段 ID')).toBeInTheDocument()
    expect(screen.getByText('root')).toBeInTheDocument()
    expect(screen.getByText('令牌用量')).toBeInTheDocument()
    expect(screen.getByText('输入')).toBeInTheDocument()
    expect(screen.getByText('输出')).toBeInTheDocument()
    expect(screen.getByText(/hello/)).toBeInTheDocument()
    expect(screen.getByText(/world/)).toBeInTheDocument()
  })

  it('requires a reason before submitting both-bad SBS review', async () => {
    api.listSBSTasks.mockResolvedValue([{
      task_id: 'sbs-1', prompt: '诊断超时', status: 'pending',
    }])
    api.getSBSTask.mockResolvedValue({
      task_id: 'sbs-1', prompt: '诊断超时',
      candidates: [{ label: 'A', output: 'answer A' }, { label: 'B', output: 'answer B' }],
    })
    api.submitSBSReview.mockResolvedValue({
      task_id: 'sbs-1', reviewer: 'alice', winner: 'both_bad', reason: '两边都缺少证据',
      dimension_scores: {}, revision: 1, canonical_winner: 'both_bad',
    })
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: 'SBS 评审' }))
    await user.click(await screen.findByRole('button', { name: /诊断超时/ }))
    await user.selectOptions(await screen.findByLabelText('胜出项'), 'both_bad')

    expect(screen.getByRole('button', { name: '保存评审' })).toBeDisabled()
    await user.type(screen.getByLabelText('评审人'), 'alice')
    await user.type(screen.getByLabelText('理由'), '两边都缺少证据')
    await waitFor(() => expect(screen.getByRole('button', { name: '保存评审' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '保存评审' }))
    expect(await screen.findByText('评审已保存')).toBeInTheDocument()
    expect(api.listSBSTasks).toHaveBeenCalledTimes(2)
  })

  it('creates a persisted EvalRun in Regression and selects it as the baseline', async () => {
    const completedRun = {
      run_id: 'run-new', created_at: '2026-07-14T01:00:00Z', updated_at: '2026-07-14T01:01:00Z',
      mode: 'quick', agent_mode: 'multi', status: 'completed' as const,
      dataset_path: 'claw_eval_smoke', dataset_hash: 'hash', total_cases: 1,
      completed_cases: 1, failed_cases: 0, case_results: [],
    }
    api.listEvaluationRuns
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([completedRun])
    api.runSkillEvaluationStream.mockImplementation(async function* () {
      yield {
        type: 'started' as const, run_id: 'run-new', mode: 'quick' as const,
        source: 'golden:claw_eval_smoke', total: 1, completed: 0,
      }
      yield {
        type: 'done' as const, run_id: 'run-new', mode: 'quick' as const,
        source: 'golden:claw_eval_smoke', total: 1, completed: 1, percent: 100,
        results: [],
      }
    })
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" agentMode="multi" />)

    await user.click(screen.getByRole('tab', { name: '回归评测' }))
    expect(await screen.findByText(/暂无 EvalRun/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('黄金数据集'), 'claw_eval_smoke')
    await user.click(screen.getByRole('button', { name: '创建 EvalRun' }))

    await waitFor(() => expect(api.runSkillEvaluationStream).toHaveBeenCalledWith({
      golden_path: 'claw_eval_smoke', evaluation_mode: 'quick', agent_mode: 'multi',
    }))
    await waitFor(() => expect(screen.getByLabelText('基线')).toHaveValue('run-new'))
    expect(screen.getByText('1 / 1 个用例')).toBeInTheDocument()
  })

  it('does not compare the same EvalRun against itself', async () => {
    api.listEvaluationRuns.mockResolvedValue([{
      run_id: 'run-one', created_at: '2026-07-14T01:00:00Z', updated_at: '2026-07-14T01:01:00Z',
      mode: 'quick', agent_mode: 'single', status: 'completed', dataset_path: 'claw_eval_smoke',
      dataset_hash: 'hash', total_cases: 1, completed_cases: 1, failed_cases: 0, case_results: [],
    }])
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: '回归评测' }))
    await user.selectOptions(await screen.findByLabelText('基线'), 'run-one')
    await user.selectOptions(screen.getByLabelText('候选'), 'run-one')

    expect(screen.getByRole('button', { name: '执行门禁' })).toBeDisabled()
  })

  it('explains what every Agent Engineering module does', async () => {
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    expect(screen.getByText(/检查单次智能体执行中的每个片段/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '回归评测' }))
    expect(await screen.findByText(/创建持久化 EvalRun/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '回放差异' }))
    expect(screen.getByText(/比较两个检查点/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'SBS 评审' }))
    expect(screen.getByText(/同一提示词并行运行两套模型或智能体配置/)).toBeInTheDocument()
  })

  it('runs two project Agent configurations and opens the blinded SBS task', async () => {
    const task = {
      task_id: 'sbs-run', prompt: '诊断接口超时', status: 'pending' as const,
      provenance: { source: 'project_agent_ab_run' },
      candidate_a: { candidate_id: 'candidate-a', output: 'Answer one', metadata: {} },
      candidate_b: { candidate_id: 'candidate-b', output: 'Answer two', metadata: {} },
    }
    api.runSBSCandidates.mockResolvedValue(task)
    api.listSBSTasks.mockResolvedValueOnce([]).mockResolvedValueOnce([{
      task_id: task.task_id, prompt: task.prompt, status: task.status,
    }])
    api.getSBSTask.mockResolvedValue({
      task_id: task.task_id, prompt: task.prompt,
      candidates: [{ label: 'A', output: 'Answer two' }, { label: 'B', output: 'Answer one' }],
    })
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: 'SBS 评审' }))
    await user.type(await screen.findByLabelText('评测提示词'), task.prompt)
    await user.clear(screen.getByLabelText('配置 1 模型'))
    await user.type(screen.getByLabelText('配置 1 模型'), 'model-one')
    await user.clear(screen.getByLabelText('配置 2 模型'))
    await user.type(screen.getByLabelText('配置 2 模型'), 'model-two')
    await user.selectOptions(screen.getByLabelText('配置 2 智能体模式'), 'multi')
    await user.click(screen.getByRole('button', { name: '运行并创建盲评' }))

    await waitFor(() => expect(api.runSBSCandidates).toHaveBeenCalledWith({
      prompt: task.prompt,
      candidate_a: { model: 'model-one', agent_mode: 'single' },
      candidate_b: { model: 'model-two', agent_mode: 'multi' },
    }))
    expect(await screen.findByText('Answer one')).toBeInTheDocument()
    expect(screen.getByText('Answer two')).toBeInTheDocument()
  })

  it('creates and opens a blinded SBS task from the SBS module', async () => {
    const task = {
      task_id: 'sbs-created', prompt: 'Which answer is better?', status: 'pending' as const,
      provenance: {},
      candidate_a: { candidate_id: 'baseline', output: 'Old answer', metadata: {} },
      candidate_b: { candidate_id: 'candidate', output: 'New answer', metadata: {} },
    }
    api.listSBSTasks.mockResolvedValueOnce([]).mockResolvedValueOnce([{
      task_id: task.task_id, prompt: task.prompt, status: task.status,
    }])
    api.createSBSTask.mockResolvedValue(task)
    api.getSBSTask.mockResolvedValue({
      task_id: 'sbs-created', prompt: task.prompt,
      candidates: [{ label: 'A', output: 'Old answer' }, { label: 'B', output: 'New answer' }],
    })
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('sbs-created' as `${string}-${string}-${string}-${string}-${string}`)
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: 'SBS 评审' }))
    await user.click(screen.getByText('导入已有输出（高级）'))
    await user.type(screen.getByLabelText('提示词'), task.prompt)
    await user.type(screen.getByLabelText('候选 A 输出'), 'Old answer')
    await user.type(screen.getByLabelText('候选 B 输出'), 'New answer')
    await user.click(screen.getByRole('button', { name: '创建 SBS 任务' }))

    await waitFor(() => expect(api.createSBSTask).toHaveBeenCalledWith(task))
    expect(await screen.findByText('Old answer')).toBeInTheDocument()
    expect(screen.getByText('New answer')).toBeInTheDocument()
  })

  it('prefills an SBS task from a Regression finding', async () => {
    const runs = ['base', 'candidate'].map((runId) => ({
      run_id: runId, created_at: '2026-07-14T01:00:00Z', updated_at: '2026-07-14T01:01:00Z',
      mode: 'quick', agent_mode: 'single', status: 'completed' as const,
      dataset_path: 'claw_eval_smoke', dataset_hash: 'hash', total_cases: 1,
      completed_cases: 1, failed_cases: 0, case_results: [],
    }))
    api.listEvaluationRuns.mockResolvedValue(runs)
    api.compareEvaluationRuns.mockResolvedValue({
      baseline_run_id: 'base', candidate_run_id: 'candidate', status: 'failed',
      baseline_pass_rate: 1, candidate_pass_rate: 0,
      findings: [{
        rule: 'answer_score', severity: 'error', case_id: 'case-1',
        baseline: 'Old answer', candidate: 'New answer', message: 'Answer quality regressed',
      }],
    })
    const user = userEvent.setup()
    render(<EngineeringPanel threadId="thread-1" />)

    await user.click(screen.getByRole('tab', { name: '回归评测' }))
    await user.selectOptions(await screen.findByLabelText('基线'), 'base')
    await user.selectOptions(screen.getByLabelText('候选'), 'candidate')
    await user.click(screen.getByRole('button', { name: '执行门禁' }))
    await user.click(await screen.findByRole('button', { name: '并排评审' }))

    expect(screen.getByRole('tab', { name: 'SBS 评审' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('提示词')).toHaveValue('Answer quality regressed')
    expect(screen.getByLabelText('候选 A 输出')).toHaveValue('Old answer')
    expect(screen.getByLabelText('候选 B 输出')).toHaveValue('New answer')
  })
})
