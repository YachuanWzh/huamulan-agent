import { useEffect, useState } from 'react'
import {
  api,
  type AgentMode,
  type BlindedSBSTask,
  type EvaluationComparison,
  type EvaluationRun,
  type ReplayDiff,
  type ReplayForkDescriptor,
  type SBSReview,
  type SBSRunOptions,
  type SBSTask,
  type SBSTaskSummary,
  type SkillEvaluationDataset,
  type TraceNode,
  type TraceSummary,
  type TraceView,
} from '../lib/api'

type Tool = 'trace' | 'regression' | 'replay' | 'sbs'

export function EngineeringPanel({
  threadId,
  agentMode = 'single',
}: {
  threadId: string | null
  agentMode?: AgentMode
}) {
  const [tool, setTool] = useState<Tool>('trace')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [trace, setTrace] = useState<TraceView | null>(null)
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [baseline, setBaseline] = useState('')
  const [candidate, setCandidate] = useState('')
  const [comparison, setComparison] = useState<EvaluationComparison | null>(null)
  const [datasets, setDatasets] = useState<SkillEvaluationDataset[]>([])
  const [selectedDataset, setSelectedDataset] = useState('')
  const [evaluationMode, setEvaluationMode] = useState<'quick' | 'e2e'>('quick')
  const [evaluationRunning, setEvaluationRunning] = useState(false)
  const [evaluationProgress, setEvaluationProgress] = useState<{
    total: number
    completed: number
    percent: number
  } | null>(null)
  const [beforeCheckpoint, setBeforeCheckpoint] = useState('')
  const [afterCheckpoint, setAfterCheckpoint] = useState('')
  const [replayDiff, setReplayDiff] = useState<ReplayDiff | null>(null)
  const [fork, setFork] = useState<ReplayForkDescriptor | null>(null)
  const [sbsTasks, setSbsTasks] = useState<SBSTaskSummary[]>([])
  const [sbsTask, setSbsTask] = useState<BlindedSBSTask | null>(null)
  const [reviewer, setReviewer] = useState('')
  const [winner, setWinner] = useState<SBSReview['winner']>('A')
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState('')
  const [sbsRunOptions, setSbsRunOptions] = useState<SBSRunOptions | null>(null)
  const [sbsRunning, setSbsRunning] = useState(false)
  const [sbsImportOpen, setSbsImportOpen] = useState(false)
  const [sbsRunDraft, setSbsRunDraft] = useState({
    prompt: '', modelA: '', modeA: 'single' as AgentMode,
    modelB: '', modeB: 'multi' as AgentMode,
  })
  const [sbsDraft, setSbsDraft] = useState({
    prompt: '', candidateA: '', candidateB: '', provenance: {} as Record<string, unknown>,
  })

  useEffect(() => {
    if (!threadId) return
    setBusy(true)
    api.listThreadTraces(threadId)
      .then(setTraces)
      .catch((cause) => setError(message(cause)))
      .finally(() => setBusy(false))
  }, [threadId])

  useEffect(() => {
    if (tool === 'regression') {
      api.listEvaluationRuns().then(setRuns).catch((cause) => setError(message(cause)))
      api.listSkillEvaluationDatasets(agentMode)
        .then((items) => {
          setDatasets(items)
          setSelectedDataset((current) => current || items[0]?.path || '')
        })
        .catch((cause) => setError(message(cause)))
    }
    if (tool === 'sbs') {
      if (sbsTasks.length === 0) {
        api.listSBSTasks().then(setSbsTasks).catch((cause) => setError(message(cause)))
      }
      if (!sbsRunOptions) {
        api.getSBSRunOptions()
          .then((options) => {
            setSbsRunOptions(options)
            setSbsRunDraft((draft) => ({
              ...draft,
              modelA: draft.modelA || options.default_model,
              modelB: draft.modelB || options.known_models.find(
                (model) => model !== options.default_model,
              ) || options.default_model,
            }))
          })
          .catch((cause) => setError(message(cause)))
      }
    }
  }, [agentMode, sbsRunOptions, sbsTasks.length, tool])

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await action() } catch (cause) { setError(message(cause)) } finally { setBusy(false) }
  }

  const createEvaluationRun = async () => {
    if (!selectedDataset || evaluationRunning) return
    setEvaluationRunning(true)
    setEvaluationProgress(null)
    setError('')
    let createdRunId = ''
    try {
      for await (const event of api.runSkillEvaluationStream({
        golden_path: selectedDataset,
        evaluation_mode: evaluationMode,
        agent_mode: agentMode,
      })) {
        if ('run_id' in event && event.run_id) createdRunId = event.run_id
        if ('total' in event && 'completed' in event) {
          const percent = 'percent' in event && typeof event.percent === 'number'
            ? event.percent
            : event.total > 0 ? Math.round((event.completed / event.total) * 100) : 0
          setEvaluationProgress({ total: event.total, completed: event.completed, percent })
        }
      }
      const nextRuns = await api.listEvaluationRuns()
      setRuns(nextRuns)
      const completed = nextRuns.find((item) => item.run_id === createdRunId && item.status === 'completed')
      if (completed) {
        if (!baseline) setBaseline(completed.run_id)
        else setCandidate(completed.run_id)
      }
    } catch (cause) {
      setError(`无法创建 EvalRun：${message(cause)}`)
    } finally {
      setEvaluationRunning(false)
    }
  }

  const canCompare = baseline !== ''
    && candidate !== ''
    && baseline !== candidate
    && runs.some((item) => item.run_id === baseline && item.status === 'completed')
    && runs.some((item) => item.run_id === candidate && item.status === 'completed')

  const createSbsTask = () => run(async () => {
    const task: SBSTask = {
      task_id: crypto.randomUUID(),
      prompt: sbsDraft.prompt.trim(),
      candidate_a: {
        candidate_id: 'baseline', output: sbsDraft.candidateA.trim(), metadata: {},
      },
      candidate_b: {
        candidate_id: 'candidate', output: sbsDraft.candidateB.trim(), metadata: {},
      },
      status: 'pending',
      provenance: sbsDraft.provenance,
    }
    const created = await api.createSBSTask(task)
    setSbsTasks(await api.listSBSTasks())
    setSbsTask(await api.getSBSTask(created.task_id))
    setSbsDraft({ prompt: '', candidateA: '', candidateB: '', provenance: {} })
    setSbsImportOpen(false)
    setNotice('SBS 任务已创建')
  })

  const sameSbsRunConfig = sbsRunDraft.modelA.trim() === sbsRunDraft.modelB.trim()
    && sbsRunDraft.modeA === sbsRunDraft.modeB

  const runSbsCandidates = async () => {
    if (sbsRunning || sameSbsRunConfig) return
    setSbsRunning(true)
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const created = await api.runSBSCandidates({
        prompt: sbsRunDraft.prompt.trim(),
        candidate_a: {
          model: sbsRunDraft.modelA.trim(), agent_mode: sbsRunDraft.modeA,
        },
        candidate_b: {
          model: sbsRunDraft.modelB.trim(), agent_mode: sbsRunDraft.modeB,
        },
      })
      setSbsTasks(await api.listSBSTasks())
      setSbsTask(await api.getSBSTask(created.task_id))
      setNotice('两套配置运行完成，已创建盲评任务')
    } catch (cause) {
      setError(`无法运行 SBS 候选：${message(cause)}`)
    } finally {
      setSbsRunning(false)
      setBusy(false)
    }
  }

  const prefillSbsFromFinding = (finding: EvaluationComparison['findings'][number]) => {
    setSbsDraft({
      prompt: finding.message,
      candidateA: evidenceText(finding.baseline),
      candidateB: evidenceText(finding.candidate),
      provenance: {
        baseline_run_id: comparison?.baseline_run_id,
        candidate_run_id: comparison?.candidate_run_id,
        case_id: finding.case_id,
        rule: finding.rule,
      },
    })
    setSbsTask(null)
    setSbsImportOpen(true)
    setNotice('回归评测证据已复制到新的 SBS 任务')
    setTool('sbs')
  }

  return (
    <section className="engineering-workspace" aria-label="智能体工程工作区">
      <header className="engineering-header">
        <div><span>飞行记录器</span><h2>智能体工程</h2></div>
        <p>沿执行链路复盘，基于评测证据做决策。</p>
      </header>
      <div className="engineering-tools" role="tablist">
        {([['trace', '链路追踪'], ['regression', '回归评测'], ['replay', '回放差异'], ['sbs', 'SBS 评审']] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tool === id} onClick={() => setTool(id)}>{label}</button>
        ))}
      </div>
      <div className="engineering-body">
      {error && <div className="engineering-error" role="alert">{error}</div>}
      {busy && <div className="engineering-loading">正在加载证据…</div>}
      {notice && <div className="engineering-notice" role="status">{notice}</div>}

      {tool === 'trace' && <div className="engineering-grid">
        <aside className="evidence-index">
          <h3>线程追踪记录</h3>
          {!threadId && <p>请先选择一个对话线程。</p>}
          {threadId && traces.length === 0 && !busy && <p>该线程还没有追踪记录。</p>}
          {traces.map((item) => <button key={item.trace_id} onClick={() => run(async () => setTrace(await api.getTrace(item.trace_id)))}>
            <code>{item.trace_id}</code><span>{item.duration_ms} ms · {item.total_spans} 个执行片段</span>
          </button>)}
        </aside>
        <div className="evidence-canvas">
          <ToolIntro title="追踪证据">检查单次智能体执行中的每个片段，包括耗时、令牌、输入、输出、错误和元数据。</ToolIntro>
          {!trace ? <EmptyEvidence text="选择一条追踪记录以查看其执行链路。" /> : <>
            <div className="trace-facts">
              <span>{trace.summary.duration_ms} ms</span>
              <span>{trace.summary.total_spans} 个执行片段</span>
              <span>{trace.summary.total_tokens} 个令牌</span>
              <span>{trace.summary.tool_calls} 次工具调用</span>
              <span>{trace.summary.retry_count} 次重试</span>
              <span>{trace.summary.error_count} 个错误</span>
            </div>
            <ol className="trace-spine">{trace.roots.map((node) => <TraceBranch key={node.span.span_id} node={node} />)}</ol>
          </>}
        </div>
      </div>}

      {tool === 'regression' && <div className="evidence-canvas engineering-form">
        <ToolIntro title="回归门禁">创建持久化 EvalRun，并在同一黄金数据集上比较基线与候选结果。</ToolIntro>
        <section className="evalrun-create" aria-label="创建 EvalRun">
          <div>
            <h3>创建 EvalRun</h3>
            <p>在此运行黄金数据集。执行完成后，可在下方选择并比较结果。</p>
          </div>
          <div className="form-row">
            <label>黄金数据集<select value={selectedDataset} onChange={(e) => setSelectedDataset(e.target.value)}>
              <option value="">选择数据集</option>
              {datasets.map((item) => <option key={item.path} value={item.path}>{item.label}</option>)}
            </select></label>
            <label>评测模式<select value={evaluationMode} onChange={(e) => setEvaluationMode(e.target.value as 'quick' | 'e2e')}>
              <option value="quick">快速</option><option value="e2e">E2E</option>
            </select></label>
            <button disabled={!selectedDataset || evaluationRunning} onClick={createEvaluationRun}>
              {evaluationRunning ? '运行中…' : '创建 EvalRun'}
            </button>
          </div>
          {evaluationProgress && <div className="evalrun-progress" role="status">
            <strong>{evaluationProgress.completed} / {evaluationProgress.total} 个用例</strong>
            <span><i style={{ width: `${evaluationProgress.percent}%` }} /></span>
          </div>}
          {runs.length === 0 && !evaluationRunning && <p className="engineering-empty-note">暂无 EvalRun，请先在上方创建。</p>}
        </section>
        <h3>比较两个 EvalRun</h3>
        <div className="form-row">
          <label>基线<select value={baseline} onChange={(e) => setBaseline(e.target.value)}><option value="">选择运行记录</option>{runs.map((item) => <option key={item.run_id} value={item.run_id} disabled={item.status !== 'completed'}>{evaluationRunLabel(item)}</option>)}</select></label>
          <label>候选<select value={candidate} onChange={(e) => setCandidate(e.target.value)}><option value="">选择运行记录</option>{runs.map((item) => <option key={item.run_id} value={item.run_id} disabled={item.status !== 'completed'}>{evaluationRunLabel(item)}</option>)}</select></label>
          <button disabled={!canCompare} onClick={() => run(async () => setComparison(await api.compareEvaluationRuns(baseline, candidate)))}>执行门禁</button>
        </div>
        {comparison && <><div className={`gate-status ${comparison.status}`}>{gateStatusLabel(comparison.status)} · {(comparison.baseline_pass_rate * 100).toFixed(1)}% → {(comparison.candidate_pass_rate * 100).toFixed(1)}%</div>
          <ul className="finding-list">{comparison.findings.map((item, index) => <li key={`${item.rule}-${index}`} data-severity={item.severity}><code>{item.rule}</code><span>{item.case_id || '运行'}</span><p>{item.message}</p>{(item.baseline !== undefined || item.candidate !== undefined) && <button onClick={() => prefillSbsFromFinding(item)}>并排评审</button>}</li>)}</ul></>}
      </div>}

      {tool === 'replay' && <div className="evidence-canvas engineering-form">
        <ToolIntro title="回放证据">在不执行工具的情况下比较两个检查点，并描述保留来源信息的安全分支。</ToolIntro>
        <h3>检查点状态差异</h3>
        <div className="form-row">
          <label>变更前<input value={beforeCheckpoint} onChange={(e) => setBeforeCheckpoint(e.target.value)} placeholder="检查点 ID" /></label>
          <label>变更后<input value={afterCheckpoint} onChange={(e) => setAfterCheckpoint(e.target.value)} placeholder="检查点 ID" /></label>
          <button disabled={!threadId || !beforeCheckpoint || !afterCheckpoint} onClick={() => run(async () => setReplayDiff(await api.diffReplay(threadId!, beforeCheckpoint, afterCheckpoint)))}>显示变更</button>
          <button className="quiet" disabled={!threadId || !afterCheckpoint} onClick={() => run(async () => setFork(await api.createReplayFork(threadId!, afterCheckpoint)))}>描述安全分支</button>
        </div>
        {replayDiff && <ChangeList diff={replayDiff} />}
        {fork && <p className="fork-proof"><strong>未执行任何操作。</strong>目标线程 <code>{fork.target_thread_id}</code> 保留来自检查点 <code>{fork.source_checkpoint_id}</code> 的来源信息。</p>}
      </div>}

      {tool === 'sbs' && <div className="engineering-grid">
        <aside className="evidence-index"><h3>评审队列</h3>{sbsTasks.length === 0 && <p>暂无待评审的 SBS 任务。</p>}{sbsTasks.map((item) => <button key={item.task_id} onClick={() => run(async () => setSbsTask(await api.getSBSTask(item.task_id)))}>{item.prompt}<span>{statusLabel(item.status)}</span></button>)}</aside>
        <div className="evidence-canvas engineering-form">
          <ToolIntro title="盲化偏好评审">同一提示词并行运行两套模型或智能体配置，自动记录输出与链路追踪，再随机映射为 A/B 供人工盲评。</ToolIntro>
          <section className="sbs-runner" aria-label="运行 SBS 候选">
            <div className="sbs-runner-heading">
              <div><h3>运行真实候选</h3><p>使用项目已配置的模型服务、工具和单/多智能体执行链。</p></div>
              <span>并行执行</span>
            </div>
            <label className="sbs-prompt">评测提示词<textarea value={sbsRunDraft.prompt} onChange={(e) => setSbsRunDraft((draft) => ({ ...draft, prompt: e.target.value }))} /></label>
            <div className="sbs-config-pair">
              <fieldset>
                <legend>配置 1</legend>
                <label>模型<select aria-label="配置 1 模型" value={sbsRunDraft.modelA} disabled={!sbsRunOptions} onChange={(e) => setSbsRunDraft((draft) => ({ ...draft, modelA: e.target.value }))}>
                  {!sbsRunOptions && <option value="">正在加载模型…</option>}
                  {sbsRunOptions?.known_models.map((model) => <option key={model} value={model}>{model}</option>)}
                </select></label>
                <label>智能体模式<select aria-label="配置 1 智能体模式" value={sbsRunDraft.modeA} onChange={(e) => setSbsRunDraft((draft) => ({ ...draft, modeA: e.target.value as AgentMode }))}><option value="single">单智能体</option><option value="multi">多智能体</option></select></label>
              </fieldset>
              <fieldset>
                <legend>配置 2</legend>
                <label>模型<select aria-label="配置 2 模型" value={sbsRunDraft.modelB} disabled={!sbsRunOptions} onChange={(e) => setSbsRunDraft((draft) => ({ ...draft, modelB: e.target.value }))}>
                  {!sbsRunOptions && <option value="">正在加载模型…</option>}
                  {sbsRunOptions?.known_models.map((model) => <option key={model} value={model}>{model}</option>)}
                </select></label>
                <label>智能体模式<select aria-label="配置 2 智能体模式" value={sbsRunDraft.modeB} onChange={(e) => setSbsRunDraft((draft) => ({ ...draft, modeB: e.target.value as AgentMode }))}><option value="single">单智能体</option><option value="multi">多智能体</option></select></label>
              </fieldset>
            </div>
            <div className="sbs-run-actions">
              {sameSbsRunConfig && sbsRunDraft.modelA && <p>两套配置完全相同，请更换模型或智能体模式。</p>}
              {sbsRunning && <p role="status">正在并行运行两套配置，完成后自动进入盲评…</p>}
              <button disabled={sbsRunning || !sbsRunDraft.prompt.trim() || !sbsRunDraft.modelA.trim() || !sbsRunDraft.modelB.trim() || sameSbsRunConfig} onClick={runSbsCandidates}>{sbsRunning ? '运行中…' : '运行并创建盲评'}</button>
            </div>
          </section>
          <details className="sbs-import" open={sbsImportOpen} onToggle={(event) => setSbsImportOpen(event.currentTarget.open)}>
            <summary>导入已有输出（高级）</summary>
            <section className="sbs-create" aria-label="创建 SBS 任务">
            <h3>导入已有输出</h3>
            <div className="form-row">
              <label>提示词<input value={sbsDraft.prompt} onChange={(e) => setSbsDraft((draft) => ({ ...draft, prompt: e.target.value }))} /></label>
              <label>候选 A 输出<textarea value={sbsDraft.candidateA} onChange={(e) => setSbsDraft((draft) => ({ ...draft, candidateA: e.target.value }))} /></label>
              <label>候选 B 输出<textarea value={sbsDraft.candidateB} onChange={(e) => setSbsDraft((draft) => ({ ...draft, candidateB: e.target.value }))} /></label>
              <button disabled={!sbsDraft.prompt.trim() || !sbsDraft.candidateA.trim() || !sbsDraft.candidateB.trim()} onClick={createSbsTask}>创建 SBS 任务</button>
            </div>
            </section>
          </details>
          {!sbsTask ? <EmptyEvidence text="创建任务，或从评审队列中选择一个任务。" /> : <>
          <h3>{sbsTask.prompt}</h3><div className="candidate-pair">{sbsTask.candidates.map((item) => <article key={item.label}><strong>候选 {item.label}</strong><p>{item.output}</p></article>)}</div>
          <div className="form-row sbs-review-form">
            <label>评审人<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label>
            <label>胜出项<select aria-label="胜出项" value={winner} onChange={(e) => setWinner(e.target.value as SBSReview['winner'])}><option value="A">A</option><option value="B">B</option><option value="tie">平局</option><option value="both_bad">两者都不合格</option></select></label>
            <label className="sbs-review-reason">理由<textarea aria-label="理由" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
            <button disabled={!reviewer || (winner === 'both_bad' && !reason.trim())} onClick={() => run(async () => {
            await api.submitSBSReview(sbsTask.task_id, { task_id: sbsTask.task_id, reviewer, winner, reason, dimension_scores: {}, revision: 1 })
            setSbsTasks(await api.listSBSTasks())
            setSbsTask(null)
            setReviewer('')
            setWinner('A')
            setReason('')
            setNotice('评审已保存')
            })}>保存评审</button>
          </div>
        </>}
        </div>
      </div>}
      </div>
    </section>
  )
}

function TraceBranch({ node }: { node: TraceNode }) {
  const { span } = node
  return <li>
    <details className={`trace-branch status-${span.status}`}>
      <summary className={`trace-node status-${span.status}`}>
        <strong>{span.name || span.kind}</strong>
        <code>{traceKindLabel(span.kind)}</code>
        <span>{statusLabel(span.status)}</span>
        <span>{span.duration_ms ?? 0} ms</span>
      </summary>
      <div className="trace-detail">
        <dl className="trace-identifiers">
          <div><dt>开始时间</dt><dd><time dateTime={span.created_at}>{new Date(span.created_at).toLocaleString()}</time></dd></div>
          <div><dt>执行片段 ID</dt><dd><code>{span.span_id}</code></dd></div>
          {span.parent_span_id && <div><dt>父级 ID</dt><dd><code>{span.parent_span_id}</code></dd></div>}
        </dl>
        <TracePayload label="令牌用量" value={span.token_usage} />
        <TracePayload label="输入" value={span.input} />
        <TracePayload label="输出" value={span.output} />
        <TracePayload label="错误" value={span.error} />
        <TracePayload label="元数据" value={span.metadata} />
      </div>
    </details>
    {node.children.length > 0 && <ol>{node.children.map((child) => <TraceBranch key={child.span.span_id} node={child} />)}</ol>}
  </li>
}

function TracePayload({ label, value }: { label: string; value: Record<string, unknown> }) {
  if (Object.keys(value).length === 0) return null
  return <section className="trace-payload"><h4>{label}</h4><pre>{JSON.stringify(value, null, 2)}</pre></section>
}

function ChangeList({ diff }: { diff: ReplayDiff }) {
  const rows = [...diff.added, ...diff.removed, ...diff.changed]
  return <ul className="change-list">{rows.map((item, index) => <li key={`${item.kind}-${item.path}-${index}`}><span>{changeKindLabel(item.kind)}</span><code>{item.path}</code><pre>{JSON.stringify(item.before)} → {JSON.stringify(item.after)}</pre></li>)}</ul>
}

function EmptyEvidence({ text }: { text: string }) { return <div className="engineering-empty"><span>⌁</span><p>{text}</p></div> }
function ToolIntro({ title, children }: { title: string; children: string }) { return <div className="tool-intro"><span>{title}</span><p>{children}</p></div> }
function message(cause: unknown) { return cause instanceof Error ? cause.message : '无法加载证据。' }
function evidenceText(value: unknown) {
  if (typeof value === 'string') return value
  if (value === undefined || value === null) return '未记录证据。'
  return JSON.stringify(value, null, 2)
}
function evaluationRunLabel(run: EvaluationRun) {
  const created = new Date(run.created_at).toLocaleString()
  return `${created} · ${run.dataset_path} · ${evaluationModeLabel(run.mode)} · ${run.completed_cases}/${run.total_cases} · ${statusLabel(run.status)}`
}

function evaluationModeLabel(mode: string) { return mode === 'e2e' ? 'E2E' : mode === 'quick' ? '快速' : mode }
function gateStatusLabel(status: string) {
  return ({ passed: '通过', failed: '未通过', warning: '警告' } as Record<string, string>)[status] || status
}
function statusLabel(status: string) {
  return ({
    pending: '待处理', running: '运行中', completed: '已完成', reviewed: '已评审',
    failed: '失败', blocked: '已阻塞', cancelled: '已取消', error: '错误',
  } as Record<string, string>)[status] || status
}
function traceKindLabel(kind: string) {
  return ({ turn: '对话轮次', agent: '智能体', llm: '大模型', tool: '工具' } as Record<string, string>)[kind] || kind
}
function changeKindLabel(kind: string) {
  return ({ added: '新增', removed: '移除', changed: '变更' } as Record<string, string>)[kind] || kind
}
