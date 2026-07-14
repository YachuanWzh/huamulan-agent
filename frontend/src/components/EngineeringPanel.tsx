import { useEffect, useState } from 'react'
import {
  api,
  type BlindedSBSTask,
  type EvaluationComparison,
  type EvaluationRun,
  type ReplayDiff,
  type ReplayForkDescriptor,
  type SBSReview,
  type SBSTask,
  type TraceNode,
  type TraceSummary,
  type TraceView,
} from '../lib/api'

type Tool = 'trace' | 'regression' | 'replay' | 'sbs'

export function EngineeringPanel({ threadId }: { threadId: string | null }) {
  const [tool, setTool] = useState<Tool>('trace')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [trace, setTrace] = useState<TraceView | null>(null)
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [baseline, setBaseline] = useState('')
  const [candidate, setCandidate] = useState('')
  const [comparison, setComparison] = useState<EvaluationComparison | null>(null)
  const [beforeCheckpoint, setBeforeCheckpoint] = useState('')
  const [afterCheckpoint, setAfterCheckpoint] = useState('')
  const [replayDiff, setReplayDiff] = useState<ReplayDiff | null>(null)
  const [fork, setFork] = useState<ReplayForkDescriptor | null>(null)
  const [sbsTasks, setSbsTasks] = useState<SBSTask[]>([])
  const [sbsTask, setSbsTask] = useState<BlindedSBSTask | null>(null)
  const [reviewer, setReviewer] = useState('')
  const [winner, setWinner] = useState<SBSReview['winner']>('A')
  const [reason, setReason] = useState('')

  useEffect(() => {
    if (!threadId) return
    setBusy(true)
    api.listThreadTraces(threadId)
      .then(setTraces)
      .catch((cause) => setError(message(cause)))
      .finally(() => setBusy(false))
  }, [threadId])

  useEffect(() => {
    if (tool === 'regression' && runs.length === 0) {
      api.listEvaluationRuns().then(setRuns).catch((cause) => setError(message(cause)))
    }
    if (tool === 'sbs' && sbsTasks.length === 0) {
      api.listSBSTasks().then(setSbsTasks).catch((cause) => setError(message(cause)))
    }
  }, [runs.length, sbsTasks.length, tool])

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await action() } catch (cause) { setError(message(cause)) } finally { setBusy(false) }
  }

  return (
    <section className="engineering-workspace" aria-label="Agent engineering workspace">
      <header className="engineering-header">
        <div><span>FLIGHT RECORDER</span><h2>Agent Engineering</h2></div>
        <p>沿链路复盘，拿评测证据做决策。</p>
      </header>
      <div className="engineering-tools" role="tablist">
        {([['trace', 'Trace'], ['regression', 'Regression'], ['replay', 'Replay diff'], ['sbs', 'SBS review']] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tool === id} onClick={() => setTool(id)}>{label}</button>
        ))}
      </div>
      {error && <div className="engineering-error" role="alert">{error}</div>}
      {busy && <div className="engineering-loading">Loading evidence…</div>}

      {tool === 'trace' && <div className="engineering-grid">
        <aside className="evidence-index">
          <h3>Thread traces</h3>
          {!threadId && <p>先选择一个对话线程。</p>}
          {threadId && traces.length === 0 && !busy && <p>该线程还没有 Trace。</p>}
          {traces.map((item) => <button key={item.trace_id} onClick={() => run(async () => setTrace(await api.getTrace(item.trace_id)))}>
            <code>{item.trace_id}</code><span>{item.duration_ms} ms · {item.total_spans} spans</span>
          </button>)}
        </aside>
        <div className="evidence-canvas">
          {!trace ? <EmptyEvidence text="选择一条 Trace 查看执行脊柱。" /> : <>
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
        <h3>Compare two EvalRuns</h3>
        <div className="form-row">
          <label>Baseline<select value={baseline} onChange={(e) => setBaseline(e.target.value)}><option value="">Select run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id} · {item.status}</option>)}</select></label>
          <label>Candidate<select value={candidate} onChange={(e) => setCandidate(e.target.value)}><option value="">Select run</option>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id} · {item.status}</option>)}</select></label>
          <button disabled={!baseline || !candidate} onClick={() => run(async () => setComparison(await api.compareEvaluationRuns(baseline, candidate)))}>Run gate</button>
        </div>
        {comparison && <><div className={`gate-status ${comparison.status}`}>{comparison.status.toUpperCase()} · {(comparison.baseline_pass_rate * 100).toFixed(1)}% → {(comparison.candidate_pass_rate * 100).toFixed(1)}%</div>
          <ul className="finding-list">{comparison.findings.map((item, index) => <li key={`${item.rule}-${index}`} data-severity={item.severity}><code>{item.rule}</code><span>{item.case_id || 'run'}</span><p>{item.message}</p></li>)}</ul></>}
      </div>}

      {tool === 'replay' && <div className="evidence-canvas engineering-form">
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
        <aside className="evidence-index"><h3>Review queue</h3>{sbsTasks.length === 0 && <p>暂无待评 SBS。</p>}{sbsTasks.map((item) => <button key={item.task_id} onClick={() => run(async () => setSbsTask(await api.getSBSTask(item.task_id)))}>{item.prompt}<span>{item.status}</span></button>)}</aside>
        <div className="evidence-canvas engineering-form">{!sbsTask ? <EmptyEvidence text="选择任务后，只显示盲化候选。" /> : <>
          <h3>{sbsTask.prompt}</h3><div className="candidate-pair">{sbsTask.candidates.map((item) => <article key={item.label}><strong>Candidate {item.label}</strong><p>{item.output}</p></article>)}</div>
          <div className="form-row"><label>Reviewer<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label><label>Winner<select aria-label="Winner" value={winner} onChange={(e) => setWinner(e.target.value as SBSReview['winner'])}><option value="A">A</option><option value="B">B</option><option value="tie">Tie</option><option value="both_bad">Both bad</option></select></label><label>Reason<textarea aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <button disabled={!reviewer || (winner === 'both_bad' && !reason.trim())} onClick={() => run(async () => { await api.submitSBSReview(sbsTask.task_id, { task_id: sbsTask.task_id, reviewer, winner, reason, dimension_scores: {}, revision: 1 }) })}>Save review</button></div>
        </>}</div>
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
function message(cause: unknown) { return cause instanceof Error ? cause.message : 'Unable to load evidence.' }
