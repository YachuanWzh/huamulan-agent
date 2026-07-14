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
  type SBSTask,
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
  const [sbsTasks, setSbsTasks] = useState<SBSTask[]>([])
  const [sbsTask, setSbsTask] = useState<BlindedSBSTask | null>(null)
  const [reviewer, setReviewer] = useState('')
  const [winner, setWinner] = useState<SBSReview['winner']>('A')
  const [reason, setReason] = useState('')
  const [notice, setNotice] = useState('')
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
    if (tool === 'sbs' && sbsTasks.length === 0) {
      api.listSBSTasks().then(setSbsTasks).catch((cause) => setError(message(cause)))
    }
  }, [agentMode, sbsTasks.length, tool])

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
      setError(`Unable to create EvalRun: ${message(cause)}`)
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
    setNotice('SBS task created')
  })

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
    setNotice('Regression evidence copied into a new SBS task')
    setTool('sbs')
  }

  return (
    <section className="engineering-workspace" aria-label="Agent engineering workspace">
      <header className="engineering-header">
        <div><span>FLIGHT RECORDER</span><h2>Agent Engineering</h2></div>
        <p>Review the execution path and make decisions from evaluation evidence.</p>
      </header>
      <div className="engineering-tools" role="tablist">
        {([['trace', 'Trace'], ['regression', 'Regression'], ['replay', 'Replay diff'], ['sbs', 'SBS review']] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tool === id} onClick={() => setTool(id)}>{label}</button>
        ))}
      </div>
      {error && <div className="engineering-error" role="alert">{error}</div>}
      {busy && <div className="engineering-loading">Loading evidence…</div>}
      {notice && <div className="engineering-notice" role="status">{notice}</div>}

      {tool === 'trace' && <div className="engineering-grid">
        <aside className="evidence-index">
          <h3>Thread traces</h3>
          {!threadId && <p>Select a conversation thread first.</p>}
          {threadId && traces.length === 0 && !busy && <p>This thread has no traces yet.</p>}
          {traces.map((item) => <button key={item.trace_id} onClick={() => run(async () => setTrace(await api.getTrace(item.trace_id)))}>
            <code>{item.trace_id}</code><span>{item.duration_ms} ms · {item.total_spans} spans</span>
          </button>)}
        </aside>
        <div className="evidence-canvas">
          <ToolIntro title="Trace evidence">Inspect every span in one agent turn, including timing, tokens, inputs, outputs, errors, and metadata.</ToolIntro>
          {!trace ? <EmptyEvidence text="Select a trace to inspect its execution spine." /> : <>
            <div className="trace-facts">
              <span>{trace.summary.duration_ms} ms</span>
              <span>{trace.summary.total_spans} spans</span>
              <span>{trace.summary.total_tokens} tokens</span>
              <span>{trace.summary.tool_calls} tools</span>
              <span>{trace.summary.retry_count} retries</span>
              <span>{trace.summary.error_count} errors</span>
            </div>
            <ol className="trace-spine">{trace.roots.map((node) => <TraceBranch key={node.span.span_id} node={node} />)}</ol>
          </>}
        </div>
      </div>}

      {tool === 'regression' && <div className="evidence-canvas engineering-form">
        <ToolIntro title="Regression gate">Create persisted EvalRuns, then compare a baseline and candidate over the same Golden Dataset.</ToolIntro>
        <section className="evalrun-create" aria-label="Create EvalRun">
          <div>
            <h3>Create EvalRun</h3>
            <p>Run a Golden Dataset here. Completed runs become available for comparison below.</p>
          </div>
          <div className="form-row">
            <label>Golden dataset<select value={selectedDataset} onChange={(e) => setSelectedDataset(e.target.value)}>
              <option value="">Select dataset</option>
              {datasets.map((item) => <option key={item.path} value={item.path}>{item.label}</option>)}
            </select></label>
            <label>Evaluation mode<select value={evaluationMode} onChange={(e) => setEvaluationMode(e.target.value as 'quick' | 'e2e')}>
              <option value="quick">Quick</option><option value="e2e">E2E</option>
            </select></label>
            <button disabled={!selectedDataset || evaluationRunning} onClick={createEvaluationRun}>
              {evaluationRunning ? 'Running…' : 'Create EvalRun'}
            </button>
          </div>
          {evaluationProgress && <div className="evalrun-progress" role="status">
            <strong>{evaluationProgress.completed} / {evaluationProgress.total} cases</strong>
            <span><i style={{ width: `${evaluationProgress.percent}%` }} /></span>
          </div>}
          {runs.length === 0 && !evaluationRunning && <p className="engineering-empty-note">No EvalRuns yet. Create the first run above.</p>}
        </section>
        <h3>Compare two EvalRuns</h3>
        <div className="form-row">
          <label>Baseline<select value={baseline} onChange={(e) => setBaseline(e.target.value)}><option value="">Select run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id} disabled={item.status !== 'completed'}>{evaluationRunLabel(item)}</option>)}</select></label>
          <label>Candidate<select value={candidate} onChange={(e) => setCandidate(e.target.value)}><option value="">Select run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id} disabled={item.status !== 'completed'}>{evaluationRunLabel(item)}</option>)}</select></label>
          <button disabled={!canCompare} onClick={() => run(async () => setComparison(await api.compareEvaluationRuns(baseline, candidate)))}>Run gate</button>
        </div>
        {comparison && <><div className={`gate-status ${comparison.status}`}>{comparison.status.toUpperCase()} · {(comparison.baseline_pass_rate * 100).toFixed(1)}% → {(comparison.candidate_pass_rate * 100).toFixed(1)}%</div>
          <ul className="finding-list">{comparison.findings.map((item, index) => <li key={`${item.rule}-${index}`} data-severity={item.severity}><code>{item.rule}</code><span>{item.case_id || 'run'}</span><p>{item.message}</p>{(item.baseline !== undefined || item.candidate !== undefined) && <button onClick={() => prefillSbsFromFinding(item)}>Review side by side</button>}</li>)}</ul></>}
      </div>}

      {tool === 'replay' && <div className="evidence-canvas engineering-form">
        <ToolIntro title="Replay evidence">Compare two checkpoints without executing tools, then describe a provenance-preserving safe fork.</ToolIntro>
        <h3>Checkpoint state diff</h3>
        <div className="form-row">
          <label>Before<input value={beforeCheckpoint} onChange={(e) => setBeforeCheckpoint(e.target.value)} placeholder="checkpoint id" /></label>
          <label>After<input value={afterCheckpoint} onChange={(e) => setAfterCheckpoint(e.target.value)} placeholder="checkpoint id" /></label>
          <button disabled={!threadId || !beforeCheckpoint || !afterCheckpoint} onClick={() => run(async () => setReplayDiff(await api.diffReplay(threadId!, beforeCheckpoint, afterCheckpoint)))}>Show changes</button>
          <button className="quiet" disabled={!threadId || !afterCheckpoint} onClick={() => run(async () => setFork(await api.createReplayFork(threadId!, afterCheckpoint)))}>Describe safe fork</button>
        </div>
        {replayDiff && <ChangeList diff={replayDiff} />}
        {fork && <p className="fork-proof"><strong>No execution performed.</strong> Target <code>{fork.target_thread_id}</code> keeps provenance from <code>{fork.source_checkpoint_id}</code>.</p>}
      </div>}

      {tool === 'sbs' && <div className="engineering-grid">
        <aside className="evidence-index"><h3>Review queue</h3>{sbsTasks.length === 0 && <p>No SBS tasks are waiting for review.</p>}{sbsTasks.map((item) => <button key={item.task_id} onClick={() => run(async () => setSbsTask(await api.getSBSTask(item.task_id)))}>{item.prompt}<span>{item.status}</span></button>)}</aside>
        <div className="evidence-canvas engineering-form">
          <ToolIntro title="Blinded preference review">Create blinded A/B tasks, judge outputs without model identity, and keep the reason as auditable evidence.</ToolIntro>
          <section className="sbs-create" aria-label="Create SBS task">
            <h3>Create SBS task</h3>
            <div className="form-row">
              <label>Prompt<input value={sbsDraft.prompt} onChange={(e) => setSbsDraft((draft) => ({ ...draft, prompt: e.target.value }))} /></label>
              <label>Candidate A output<textarea value={sbsDraft.candidateA} onChange={(e) => setSbsDraft((draft) => ({ ...draft, candidateA: e.target.value }))} /></label>
              <label>Candidate B output<textarea value={sbsDraft.candidateB} onChange={(e) => setSbsDraft((draft) => ({ ...draft, candidateB: e.target.value }))} /></label>
              <button disabled={!sbsDraft.prompt.trim() || !sbsDraft.candidateA.trim() || !sbsDraft.candidateB.trim()} onClick={createSbsTask}>Create SBS task</button>
            </div>
          </section>
          {!sbsTask ? <EmptyEvidence text="Create a task or select one from the review queue." /> : <>
          <h3>{sbsTask.prompt}</h3><div className="candidate-pair">{sbsTask.candidates.map((item) => <article key={item.label}><strong>Candidate {item.label}</strong><p>{item.output}</p></article>)}</div>
          <div className="form-row"><label>Reviewer<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label><label>Winner<select aria-label="Winner" value={winner} onChange={(e) => setWinner(e.target.value as SBSReview['winner'])}><option value="A">A</option><option value="B">B</option><option value="tie">Tie</option><option value="both_bad">Both bad</option></select></label><label>Reason<textarea aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <button disabled={!reviewer || (winner === 'both_bad' && !reason.trim())} onClick={() => run(async () => {
            await api.submitSBSReview(sbsTask.task_id, { task_id: sbsTask.task_id, reviewer, winner, reason, dimension_scores: {}, revision: 1 })
            setSbsTasks(await api.listSBSTasks())
            setSbsTask(null)
            setReviewer('')
            setWinner('A')
            setReason('')
            setNotice('Review saved')
          })}>Save review</button></div>
        </>}
        </div>
      </div>}
    </section>
  )
}

function TraceBranch({ node }: { node: TraceNode }) {
  const { span } = node
  return <li>
    <details className={`trace-branch status-${span.status}`}>
      <summary className={`trace-node status-${span.status}`}>
        <strong>{span.name || span.kind}</strong>
        <code>{span.kind}</code>
        <span>{span.status}</span>
        <span>{span.duration_ms ?? 0} ms</span>
      </summary>
      <div className="trace-detail">
        <dl className="trace-identifiers">
          <div><dt>Started</dt><dd><time dateTime={span.created_at}>{new Date(span.created_at).toLocaleString()}</time></dd></div>
          <div><dt>Span ID</dt><dd><code>{span.span_id}</code></dd></div>
          {span.parent_span_id && <div><dt>Parent ID</dt><dd><code>{span.parent_span_id}</code></dd></div>}
        </dl>
        <TracePayload label="Tokens" value={span.token_usage} />
        <TracePayload label="Input" value={span.input} />
        <TracePayload label="Output" value={span.output} />
        <TracePayload label="Error" value={span.error} />
        <TracePayload label="Metadata" value={span.metadata} />
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
  return <ul className="change-list">{rows.map((item, index) => <li key={`${item.kind}-${item.path}-${index}`}><span>{item.kind}</span><code>{item.path}</code><pre>{JSON.stringify(item.before)} → {JSON.stringify(item.after)}</pre></li>)}</ul>
}

function EmptyEvidence({ text }: { text: string }) { return <div className="engineering-empty"><span>⌁</span><p>{text}</p></div> }
function ToolIntro({ title, children }: { title: string; children: string }) { return <div className="tool-intro"><span>{title}</span><p>{children}</p></div> }
function message(cause: unknown) { return cause instanceof Error ? cause.message : 'Unable to load evidence.' }
function evidenceText(value: unknown) {
  if (typeof value === 'string') return value
  if (value === undefined || value === null) return 'No evidence recorded.'
  return JSON.stringify(value, null, 2)
}
function evaluationRunLabel(run: EvaluationRun) {
  const created = new Date(run.created_at).toLocaleString()
  return `${created} · ${run.dataset_path} · ${run.mode} · ${run.completed_cases}/${run.total_cases} · ${run.status}`
}
