import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ExecutionLog,
  type ExecutionSummary,
  type ObservabilitySnapshot,
  type AgentMode,
  type CaseEvaluationDetail,
  type ReplayResponse,
  type ReplayState,
  type SkillEvaluationDataset,
  type SkillEvaluationReport,
  type SkillEvaluationSnapshot,
  type SkillEvaluationStreamEvent,
  type SkillInfo,
} from '../lib/api'
import { MarkdownRenderer } from './MarkdownRenderer'

interface Props {
  panel: 'skills' | 'checkpoint' | 'audit' | 'performance'
  threadId: string | null
  onThreadCleared?: () => void
  onReplayState?: (state: ReplayState) => void
  agentMode?: AgentMode
}

type AuditFilter = 'all' | 'llm' | 'tool' | 'tool_retry' | 'security' | 'approval'

const CUSTOM_GOLDEN_DATASET = '__custom__'

const FALLBACK_GOLDEN_DATASETS: SkillEvaluationDataset[] = [
  { name: 'claw_eval_smoke', path: 'claw_eval_smoke', label: 'ClawEval smoke' },
  { name: 'golden_dataset', path: 'golden_dataset', label: 'Golden dataset' },
  { name: 'e2e_dateset', path: 'e2e_dateset', label: 'E2E dataset' },
]

const auditFilterLabels: Record<AuditFilter, string> = {
  all: '全部',
  llm: 'llm',
  tool: 'tool',
  tool_retry: 'tool_retry',
  security: 'security',
  approval: 'approval',
}

const statusLabels: Record<string, string> = {
  started: '已开始',
  completed: '已完成',
  failed: '失败',
  blocked: '已拦截',
  retrying: '重试中',
  approved: '已批准',
  denied: '已拒绝',
}

export function WorkspacePanel({
  panel,
  threadId,
  onThreadCleared,
  onReplayState,
  agentMode = 'single',
}: Props) {
  const [replay, setReplay] = useState<ReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)
  const [historyDeleting, setHistoryDeleting] = useState(false)
  const [executionLogs, setExecutionLogs] = useState<ExecutionLog[]>([])
  const [executionSummary, setExecutionSummary] = useState<ExecutionSummary | null>(null)
  const [executionLoading, setExecutionLoading] = useState(false)
  const [observability, setObservability] = useState<ObservabilitySnapshot | null>(null)
  const [observabilityLoading, setObservabilityLoading] = useState(false)
  const [auditFilter, setAuditFilter] = useState<AuditFilter>('all')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [goldenDatasets, setGoldenDatasets] = useState<SkillEvaluationDataset[]>(
    FALLBACK_GOLDEN_DATASETS,
  )
  const [selectedGoldenPath, setSelectedGoldenPath] = useState('claw_eval_smoke')
  const [customGoldenPath, setCustomGoldenPath] = useState('')
  const [evaluationRunning, setEvaluationRunning] = useState(false)
  const [evaluationResetting, setEvaluationResetting] = useState(false)
  const [evaluationError, setEvaluationError] = useState<string | null>(null)
  const [evaluationProgress, setEvaluationProgress] = useState<{
    mode: 'quick' | 'e2e'
    source: string
    total: number
    completed: number
    percent: number
  } | null>(null)
  const [activeEvaluationCase, setActiveEvaluationCase] = useState<CaseEvaluationDetail | null>(null)
  const [evaluationReport, setEvaluationReport] = useState<SkillEvaluationReport | null>(null)
  const [evaluationHistory, setEvaluationHistory] = useState<
    Record<string, SkillEvaluationSnapshot[]>
  >({})

  const loadReplay = useCallback(async () => {
    if (!threadId) {
      setReplay(null)
      return
    }
    setReplayLoading(true)
    try {
      setReplay(await api.replay(threadId))
    } catch {
      setReplay(null)
    }
    setReplayLoading(false)
  }, [threadId])

  const loadExecutionAudit = useCallback(async () => {
    if (!threadId) {
      setExecutionLogs([])
      setExecutionSummary(null)
      return
    }
    setExecutionLoading(true)
    try {
      const [summary, logs] = await Promise.all([
        api.getExecutionSummary(threadId),
        api.listExecutionLogs(threadId),
      ])
      setExecutionSummary(summary)
      setExecutionLogs(logs)
    } catch {
      setExecutionSummary(null)
      setExecutionLogs([])
    }
    setExecutionLoading(false)
  }, [threadId])

  const loadObservability = useCallback(async () => {
    setObservabilityLoading(true)
    try {
      setObservability(await api.getObservabilitySnapshot(threadId ?? undefined))
    } catch {
      setObservability(null)
    }
    setObservabilityLoading(false)
  }, [threadId])

  const loadSkills = useCallback(async () => {
    setSkillsLoading(true)
    try {
      const nextSkills = await api.listSkills()
      setSkills(nextSkills)
      await loadSkillEvaluationHistory(nextSkills)
    } catch {
      setSkills([])
      setEvaluationHistory({})
    }
    setSkillsLoading(false)
  }, [])

  const loadSkillEvaluationHistory = async (nextSkills: SkillInfo[]) => {
    const skillsWithEvaluations = nextSkills.filter((skill) => skill.latest_evaluation)
    if (skillsWithEvaluations.length === 0) {
      setEvaluationHistory({})
      return
    }
    try {
      const entries = await Promise.all(
        skillsWithEvaluations.map(async (skill) => [
          skill.name,
          await api.listSkillEvaluationHistory(skill.name),
        ] as const),
      )
      setEvaluationHistory(Object.fromEntries(entries))
    } catch {
      setEvaluationHistory({})
    }
  }

  const loadGoldenDatasets = useCallback(async () => {
    try {
      const datasets = await api.listSkillEvaluationDatasets()
      if (datasets.length > 0) {
        setGoldenDatasets(datasets)
      }
    } catch {
      setGoldenDatasets(FALLBACK_GOLDEN_DATASETS)
    }
  }, [])

  const runSkillEvaluation = async (mode: 'quick' | 'e2e') => {
    const rawPath =
      selectedGoldenPath === CUSTOM_GOLDEN_DATASET ? customGoldenPath : selectedGoldenPath
    const trimmedPath = rawPath.trim()
    if (!trimmedPath) return

    setEvaluationRunning(true)
    setEvaluationProgress(null)
    setActiveEvaluationCase(null)
    setEvaluationReport(null)
    setEvaluationError(null)
    try {
      for await (const event of api.runSkillEvaluationStream({
        golden_path: trimmedPath,
        evaluation_mode: mode,
        agent_mode: agentMode,
      })) {
        applyEvaluationEvent(event)
      }
    } catch (error) {
      setEvaluationError(formatEvaluationError(error))
    }
    setEvaluationRunning(false)
  }

  const applyEvaluationEvent = (event: SkillEvaluationStreamEvent) => {
    setEvaluationProgress({
      mode: event.mode,
      source: event.source,
      total: event.total,
      completed: event.completed,
      percent: event.percent ?? 0,
    })
    if (event.type === 'done' && event.results.length > 0) {
      setSkills((prev) => {
        const nextSkills = mergeLatestSkillEvaluations(prev, event.results)
        void loadSkillEvaluationHistory(nextSkills)
        return nextSkills
      })
    }
    if (event.type === 'done' && event.report) {
      setEvaluationReport(event.report)
    }
    if (event.type === 'case_progress' && event.mode === 'e2e') {
      setActiveEvaluationCase(event.detail)
    }
  }

  const resetSkillEvaluations = async () => {
    const confirmed = window.confirm('确认重置所有 Skill 评分？这会清空当前已保存的测评结果。')
    if (!confirmed) return
    setEvaluationResetting(true)
    try {
      await api.resetSkillEvaluations()
      setEvaluationProgress(null)
      await loadSkills()
    } catch {
      // silently handle
    }
    setEvaluationResetting(false)
  }

  useEffect(() => {
    if (panel === 'skills') {
      loadSkills()
      loadGoldenDatasets()
    }
  }, [loadGoldenDatasets, loadSkills, panel])

  useEffect(() => {
    if (panel === 'checkpoint') {
      loadReplay()
    }
  }, [loadReplay, panel])

  useEffect(() => {
    if (panel === 'audit') {
      loadExecutionAudit()
    }
  }, [loadExecutionAudit, panel])

  useEffect(() => {
    if (panel === 'performance') {
      loadObservability()
    }
  }, [loadObservability, panel])

  const deleteCurrentHistory = async () => {
    if (!threadId) return
    setHistoryDeleting(true)
    try {
      await api.deleteThread(threadId)
      setReplay({ thread_id: threadId, states: [] })
    } catch {
      // silently handle
    }
    setHistoryDeleting(false)
  }

  const clearAndStartNewThread = async () => {
    setHistoryDeleting(true)
    try {
      if (threadId) {
        await api.deleteThread(threadId)
        setReplay({ thread_id: threadId, states: [] })
      }
      onThreadCleared?.()
    } catch {
      // silently handle
    }
    setHistoryDeleting(false)
  }

  const visibleLogs =
    auditFilter === 'all'
      ? executionLogs
      : executionLogs.filter((log) => log.event_type === auditFilter)
  const retryChains = buildRetryChains(executionLogs)
  const selectedEvaluationPath =
    selectedGoldenPath === CUSTOM_GOLDEN_DATASET ? customGoldenPath : selectedGoldenPath
  const canRunEvaluation = selectedEvaluationPath.trim().length > 0

  return (
    <section className="workspace-panel" aria-label="行军案台">
      {panel === 'skills' && (
        <div className="workspace-section skill-evaluation-section">
          <div className="workspace-header">
            <div>
              <h2>Skill Evaluation</h2>
              <p>盘点当前 Skill 的描述清晰度、代码规模、复杂度和器具配置。</p>
            </div>
            <div className="skill-evaluation-controls">
              <div className="skill-evaluation-fields">
                <label className="skill-evaluation-runner">
                  <span>Golden dataset</span>
                  <select
                    aria-label="Golden dataset"
                    value={selectedGoldenPath}
                    onChange={(event) => setSelectedGoldenPath(event.target.value)}
                  >
                    {goldenDatasets.map((dataset) => (
                      <option key={dataset.path} value={dataset.path}>
                        {dataset.label}
                      </option>
                    ))}
                    <option value={CUSTOM_GOLDEN_DATASET}>Custom path</option>
                  </select>
                </label>
                {selectedGoldenPath === CUSTOM_GOLDEN_DATASET && (
                  <label className="skill-evaluation-runner">
                    <span>Custom dataset path</span>
                    <input
                      aria-label="Custom dataset path"
                      value={customGoldenPath}
                      onChange={(event) => setCustomGoldenPath(event.target.value)}
                      placeholder="golden.jsonl"
                    />
                  </label>
                )}
              </div>
              <div className="workspace-actions skill-evaluation-buttons">
                <button
                  type="button"
                  onClick={() => runSkillEvaluation('quick')}
                  disabled={evaluationRunning || !canRunEvaluation}
                >
                  快速巡检
                </button>
                <button
                  type="button"
                  onClick={() => runSkillEvaluation('e2e')}
                  disabled={evaluationRunning || !canRunEvaluation}
                >
                  实战测评
                </button>
                <button onClick={loadSkills} disabled={skillsLoading}>
                  刷新
                </button>
                <button
                  type="button"
                  onClick={resetSkillEvaluations}
                  disabled={evaluationRunning || evaluationResetting}
                >
                  重置
                </button>
              </div>
            </div>
          </div>

          {skillsLoading && <div className="loading">加载中...</div>}
          {evaluationError && (
            <div className="workspace-error" role="alert">
              {evaluationError}
            </div>
          )}
          {evaluationProgress && (
            <div className="skill-evaluation-progress">
              <div className="skill-evaluation-progress-label">
                <span>
                  {evaluationProgress.mode === 'e2e' ? '实战测评' : '快速巡检'}
                  {evaluationRunning ? '运行中' : '已完成'}
                </span>
                <strong>
                  {evaluationProgress.completed} / {evaluationProgress.total}
                </strong>
              </div>
              <div
                className="skill-evaluation-progress-bar"
                role="progressbar"
                aria-label="Skill evaluation progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={evaluationProgress.percent}
              >
                <span style={{ width: `${evaluationProgress.percent}%` }} />
              </div>
              <small>{evaluationProgress.source}</small>
            </div>
          )}

          {activeEvaluationCase && evaluationProgress?.mode === 'e2e' && (
            <EvaluationRunTopology detail={activeEvaluationCase} running={evaluationRunning} />
          )}

          {evaluationReport && (
            <section className="claw-eval-summary" aria-label="ClawEval summary">
              <h3>ClawEval</h3>
              <dl className="skill-evaluation-metrics">
                {buildEvaluationSummary(evaluationReport).map((metric) => (
                  <div key={metric.label} title={metric.tooltip} style={{ cursor: 'help' }}>
                    <dt>{metric.label}</dt>
                    <dd>{metric.value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          {evaluationReport?.case_details?.length ? (
            <EvaluationDetails details={evaluationReport.case_details} />
          ) : null}

          {!skillsLoading && skills.length === 0 && (
            <div className="workspace-empty">当前没有可评测的 Skill。</div>
          )}
          {!skillsLoading && skills.length > 0 && (
            <div className="skill-evaluation-grid">
              {skills.map((skill) => {
                const skillHistory = evaluationHistory[skill.name] ?? []
                return (
                <section key={skill.name} className="skill-evaluation-card">
                  <div className="skill-evaluation-card-header">
                    <div>
                      <h3>{skill.name}</h3>
                      <p title={skill.description}>{skill.description}</p>
                      {skill.latest_evaluation?.source && skillHistory.length === 0 && (
                        <small>{skill.latest_evaluation.source}</small>
                      )}
                    </div>
                    <strong>{formatPercent(getSkillScore(skill))}</strong>
                  </div>
                  <div
                    className="skill-score-meter"
                    aria-label={`Skill score ${formatPercent(getSkillScore(skill))}`}
                  >
                    <span
                      style={{
                        width: `${Math.round(normalizeScore(getSkillScore(skill)) * 100)}%`,
                      }}
                    />
                  </div>
                  <dl className="skill-evaluation-metrics">
                    <div>
                      <dt>Description</dt>
                      <dd>{skill.evaluation?.description_tokens ?? 0} tokens</dd>
                    </div>
                    <div>
                      <dt>Complexity</dt>
                      <dd>{skill.evaluation?.max_cyclomatic_complexity ?? 0}</dd>
                    </div>
                    <div>
                      <dt>Python</dt>
                      <dd>{skill.evaluation?.python_lines ?? 0} lines</dd>
                    </div>
                    <div>
                      <dt>Tools</dt>
                      <dd>{skill.evaluation?.tool_count ?? skill.tool_names.length}</dd>
                    </div>
                  </dl>
                  <SkillEvaluationHistoryList
                    history={skillHistory}
                    skillName={skill.name}
                  />
                </section>
              )})}
            </div>
          )}
        </div>
      )}

      {panel === 'checkpoint' && (
        <div className="workspace-section">
          <div className="workspace-header">
            <div>
              <h2>驿站回放</h2>
              <p>查看检查点，并把已保存状态回放到对话中。</p>
            </div>
            <div className="workspace-actions">
              <button onClick={deleteCurrentHistory} disabled={historyDeleting}>
                删除检查点
              </button>
              <button onClick={clearAndStartNewThread} disabled={historyDeleting}>
                清空并新建军令
              </button>
            </div>
          </div>
          {replayLoading && <div className="loading">加载中...</div>}
          {!replayLoading && (!replay || replay.states.length === 0) && (
            <div className="workspace-empty">当前军令暂无检查点。</div>
          )}
          {replay && replay.states.length > 0 && (
            <div className="workspace-state-list">
              {replay.states.map((state, i) => (
                <details key={i} className="workspace-state">
                  <summary>
                    <span>
                      检查点 {i + 1}
                      {state.node ? ` / ${state.node}` : ''}
                    </span>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.preventDefault()
                        onReplayState?.(state)
                      }}
                    >
                      回放检查点 {i + 1}
                    </button>
                  </summary>
                  <div className="workspace-meta">
                    {state.created_at && <span>{state.created_at}</span>}
                    {state.values.selected_skills?.length ? (
                      <span>Skill: {state.values.selected_skills.join(', ')}</span>
                    ) : null}
                  </div>
                  <pre>{JSON.stringify(state, null, 2)}</pre>
                </details>
              ))}
            </div>
          )}
        </div>
      )}

      {panel === 'performance' && (
        <div className="workspace-section performance-section">
          <div className="workspace-header">
            <div>
              <h2>Frontend Performance</h2>
              <p>Web vitals, browser errors, anomaly signals, and RCA recommendations.</p>
            </div>
            <div className="workspace-actions">
              <button onClick={loadObservability} disabled={observabilityLoading}>
                Refresh
              </button>
            </div>
          </div>

          {observabilityLoading && <div className="loading">Loading...</div>}
          {!observabilityLoading && !observability && (
            <div className="workspace-empty">No frontend observability data yet.</div>
          )}
          {observability && (
            <>
              <div className="audit-summary-grid">
                <div className="audit-metric">
                  <span>RUM Events</span>
                  <strong>{observability.frontend.total_events}</strong>
                </div>
                <div className="audit-metric">
                  <span>Browser Errors</span>
                  <strong>{observability.frontend.error_count}</strong>
                </div>
                <div className="audit-metric">
                  <span>Tool Retries</span>
                  <strong>{observability.backend.tool_retries}</strong>
                </div>
                <div className="audit-metric">
                  <span>Backend p95</span>
                  <strong>{observability.backend.p95_duration_ms ?? 0}ms</strong>
                </div>
              </div>

              <div className="performance-grid">
                {Object.entries(observability.frontend.web_vitals).map(([metric, values]) => (
                  <section key={metric} className="performance-card">
                    <h3>{metric}</h3>
                    <dl className="skill-evaluation-metrics">
                      <div>
                        <dt>avg</dt>
                        <dd>{formatMetricValue(metric, values.avg)}</dd>
                      </div>
                      <div>
                        <dt>p75</dt>
                        <dd>{formatMetricValue(metric, values.p75)}</dd>
                      </div>
                      <div>
                        <dt>p95</dt>
                        <dd>{formatMetricValue(metric, values.p95)}</dd>
                      </div>
                    </dl>
                  </section>
                ))}
              </div>

              <section className="performance-card">
                <h3>Root Cause</h3>
                <strong>{observability.root_cause.category}</strong>
                <p>{observability.root_cause.summary}</p>
                <p>{observability.root_cause.recommendation}</p>
              </section>

              {observability.anomalies.length > 0 && (
                <section className="performance-card">
                  <h3>Anomalies</h3>
                  <ul>
                    {observability.anomalies.map((signal) => (
                      <li key={`${signal.metric}-${signal.value}-${signal.method}`}>
                        {signal.metric} {formatMetricValue(signal.metric, signal.value)}: {signal.reason}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </div>
      )}

      {panel === 'audit' && (
        <div className="workspace-section">
          <div className="workspace-header">
            <div>
              <h2>行军校阅</h2>
              <p>查看线程轨迹、Token 用量、工具结果和重试链。</p>
            </div>
            <div className="workspace-actions">
              <button onClick={loadExecutionAudit} disabled={executionLoading}>
                刷新
              </button>
            </div>
          </div>

          <div className="audit-summary-grid">
            <div className="audit-metric">
              <span>Token 总量</span>
              <strong>{executionSummary?.total_tokens ?? 0}</strong>
              <small>
                Prompt {executionSummary?.prompt_tokens ?? 0} / Completion{' '}
                {executionSummary?.completion_tokens ?? 0}
              </small>
            </div>
            <div className="audit-metric">
              <span>工具调用</span>
              <strong>{executionSummary?.tool_calls ?? 0}</strong>
            </div>
            <div className="audit-metric">
              <span>错误</span>
              <strong>{executionSummary?.tool_errors ?? 0}</strong>
            </div>
            <div className="audit-metric">
              <span>重试</span>
              <strong>{executionSummary?.tool_retries ?? 0}</strong>
            </div>
            <div className="audit-metric">
              <span>耗时</span>
              <strong>{executionSummary?.total_duration_ms ?? 0}ms</strong>
            </div>
          </div>

          <div className="workspace-tabs" role="tablist" aria-label="校阅筛选">
            {(['all', 'llm', 'tool', 'tool_retry', 'security', 'approval'] as const).map(
              (filter) => (
                <button
                  key={filter}
                  role="tab"
                  aria-selected={auditFilter === filter}
                  className={auditFilter === filter ? 'active' : ''}
                  onClick={() => setAuditFilter(filter)}
                >
                  {auditFilterLabels[filter]}
                </button>
              ),
            )}
          </div>

          {executionLoading && <div className="loading">加载中...</div>}
          {!executionLoading && executionLogs.length === 0 && (
            <div className="workspace-empty">当前军令暂无执行日志。</div>
          )}
          {!executionLoading && retryChains.length > 0 && (
            <div className="retry-chain-list">
              {retryChains.map((chain) => (
                <section key={chain.toolCallId} className="retry-chain">
                  <h3>{chain.name} 重试链</h3>
                  <div className="retry-chain-steps">
                    {chain.attempts.map((attempt) => (
                      <span key={attempt.id}>
                        第 {String(attempt.metadata.attempt ?? '?')} 次
                        {attempt.status === 'completed' ? '完成' : '失败'}
                      </span>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
          {!executionLoading && visibleLogs.length > 0 && (
            <ol className="audit-timeline">
              {visibleLogs.map((log) => (
                <li
                  key={log.id}
                  className={`audit-event event-${log.event_type} status-${log.status}`}
                >
                  <details>
                    <summary>
                      <span className="audit-time">
                        {new Date(log.created_at).toLocaleString()}
                      </span>
                      <strong>{log.name ?? log.event_type}</strong>
                      <span>{statusLabels[log.status] ?? log.status}</span>
                      {log.duration_ms != null && <span>{log.duration_ms}ms</span>}
                      {Number(log.token_usage.total_tokens ?? 0) > 0 && (
                        <span>{String(log.token_usage.total_tokens)} Token</span>
                      )}
                    </summary>
                    <pre>
                      {JSON.stringify(
                        {
                          input: log.input,
                          output: log.output,
                          error: log.error,
                          metadata: log.metadata,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  )
}

function buildRetryChains(logs: ExecutionLog[]) {
  const chains = new Map<string, ExecutionLog[]>()
  for (const log of logs) {
    const toolCallId =
      typeof log.metadata.tool_call_id === 'string'
        ? log.metadata.tool_call_id
        : null
    if (!toolCallId) continue
    if (log.event_type !== 'tool_retry' && log.event_type !== 'tool') continue
    const existing = chains.get(toolCallId) ?? []
    existing.push(log)
    chains.set(toolCallId, existing)
  }
  return Array.from(chains, ([toolCallId, attempts]) => ({
    toolCallId,
    name: attempts[0]?.name ?? 'tool',
    attempts,
  })).filter((chain) =>
    chain.attempts.some((log) => log.event_type === 'tool_retry'),
  )
}

function formatPercent(value: number | undefined | null) {
  return `${Math.round(normalizeScore(value) * 100)}%`
}

function normalizeScore(value: number | undefined | null) {
  const score = value ?? 0
  return score > 1 ? score / 100 : score
}

function SkillEvaluationHistoryList({
  history,
  skillName,
}: {
  history: SkillEvaluationSnapshot[]
  skillName: string
}) {
  const [showTrendDialog, setShowTrendDialog] = useState(false)

  if (history.length === 0) return null
  const [latest, previous] = history
  const delta =
    latest && previous
      ? normalizeScore(latest.overall_score) - normalizeScore(previous.overall_score)
      : null

  return (
    <div className="skill-evaluation-history">
      <SkillTrendSparkline history={history} skillName={skillName} />
      <button
        type="button"
        className="skill-history-dialog-trigger"
        onClick={() => setShowTrendDialog(true)}
      >
        <strong>History</strong>
        {delta != null && (
          <span className={delta >= 0 ? 'trend-positive' : 'trend-negative'}>
            {formatSignedPercent(delta)}
          </span>
        )}
      </button>
      {showTrendDialog && (
        <dialog
          open
          className="skill-trend-dialog"
          onClick={(event) => {
            if (event.target === event.currentTarget) setShowTrendDialog(false)
          }}
          onClose={() => setShowTrendDialog(false)}
        >
          <div className="skill-trend-dialog-content">
            <div className="skill-trend-dialog-header">
              <h3>{skillName} — Trend</h3>
              <button
                type="button"
                className="skill-trend-dialog-close"
                onClick={() => setShowTrendDialog(false)}
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <MetricTrendChart history={history} skillName={skillName} />
          </div>
        </dialog>
      )}
      <ol>
        {history.slice(0, 3).map((item) => (
          <li key={item.id}>
            <span>{formatPercent(item.overall_score)}</span>
            <small>{item.source ?? item.created_at}</small>
          </li>
        ))}
      </ol>
    </div>
  )
}

function SkillTrendSparkline({
  history,
  skillName,
}: {
  history: SkillEvaluationSnapshot[]
  skillName: string
}) {
  const points = chronological(history)
    .slice(-10)
    .map((item) => normalizeScore(item.overall_score))
  if (points.length < 2) {
    return <div className="trend-empty">Need at least 2 runs</div>
  }
  return (
    <svg
      className="skill-trend-sparkline"
      viewBox="0 0 120 36"
      role="img"
      aria-label={`${skillName} overall trend`}
      preserveAspectRatio="none"
    >
      <path className="trend-gridline" d="M0 18 H120" />
      <path className="trend-line trend-overall" d={scorePath(points, 120, 36)} />
      {points.map((score, index) => (
        <circle
          key={`${score}-${index}`}
          className="trend-point"
          cx={pointX(index, points.length, 120)}
          cy={pointY(score, 36)}
          r="1.8"
        />
      ))}
    </svg>
  )
}

function MetricTrendChart({
  history,
  skillName,
}: {
  history: SkillEvaluationSnapshot[]
  skillName: string
}) {
  const items = chronological(history).slice(-10)
  const metrics = [
    metricSeries('overall', items.map((item) => item.overall_score)),
    metricSeries('routing', items.map((item) => item.routing_score)),
    metricSeries('runtime', items.map((item) => item.runtime_score)),
    metricSeries('static', items.map((item) => item.static_score)),
  ].filter((metric) => metric.values.length >= 2)

  if (metrics.length === 0) {
    return <div className="trend-empty">Need at least 2 runs</div>
  }

  return (
    <div className="metric-trend-chart">
      <svg
        viewBox="0 0 240 112"
        role="img"
        aria-label={`${skillName} metric trend chart`}
        preserveAspectRatio="none"
      >
        <path className="trend-gridline" d="M0 28 H240 M0 56 H240 M0 84 H240" />
        {metrics.map((metric) => (
          <path
            key={metric.name}
            className={`trend-line trend-${metric.name}`}
            d={scorePath(metric.values, 240, 112)}
          />
        ))}
      </svg>
      <div className="metric-trend-legend">
        {metrics.map((metric) => (
          <span key={metric.name} className={`trend-${metric.name}`}>
            {metric.name}
          </span>
        ))}
      </div>
    </div>
  )
}

function metricSeries(name: string, values: Array<number | null | undefined>) {
  return {
    name,
    values: values
      .filter((value): value is number => value != null)
      .map((value) => normalizeScore(value)),
  }
}

function chronological(history: SkillEvaluationSnapshot[]) {
  return [...history].reverse()
}

function scorePath(values: number[], width: number, height: number) {
  return values
    .map((score, index) => {
      const command = index === 0 ? 'M' : 'L'
      return `${command}${pointX(index, values.length, width).toFixed(2)} ${pointY(
        score,
        height,
      ).toFixed(2)}`
    })
    .join(' ')
}

function pointX(index: number, count: number, width: number) {
  return count <= 1 ? width / 2 : (index / (count - 1)) * width
}

function pointY(score: number, height: number) {
  const padding = height * 0.14
  const plotHeight = height - padding * 2
  return padding + (1 - Math.max(0, Math.min(1, score))) * plotHeight
}

function formatSignedPercent(value: number) {
  const rounded = Math.round(value * 100)
  return `${rounded >= 0 ? '+' : ''}${rounded}%`
}

type TopologyNodeStatus = 'running' | 'passed' | 'failed' | 'blocked' | 'warning' | 'idle'

interface TopologyNode {
  id: string
  title: string
  subtitle?: string
  status: TopologyNodeStatus
}

interface TopologyLane {
  id: string
  title: string
  nodes: TopologyNode[]
}

function EvaluationRunTopology({
  detail,
  running,
}: {
  detail: CaseEvaluationDetail
  running: boolean
}) {
  const lanes = buildTopologyLanes(detail)
  const lastLane = lanes[lanes.length - 1]
  const activeNodeId = running ? lastLane?.nodes[lastLane.nodes.length - 1]?.id : null

  return (
    <section
      key={detail.case_id}
      className="evaluation-run-topology"
      aria-label="E2E case run topology"
    >
      <div className="topology-header">
        <div>
          <h3>运行拓扑</h3>
          <p>{detail.query || detail.turns.join(' / ')}</p>
        </div>
        <strong>{detail.case_id}</strong>
      </div>
      <div className="topology-lanes">
        {lanes.map((lane, laneIndex) => (
          <div className="topology-lane" key={lane.id}>
            <div className="topology-lane-title">{lane.title}</div>
            <div className="topology-node-stack">
              {lane.nodes.map((node) => (
                <div
                  className={`topology-node status-${node.status} ${
                    node.id === activeNodeId ? 'is-active' : ''
                  }`}
                  key={node.id}
                >
                  <span className="topology-status-dot" aria-hidden="true" />
                  <strong>{node.title}</strong>
                  {node.subtitle && <small>{node.subtitle}</small>}
                </div>
              ))}
            </div>
            {laneIndex < lanes.length - 1 && (
              <div className="topology-connector" aria-hidden="true" />
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function buildTopologyLanes(detail: CaseEvaluationDetail): TopologyLane[] {
  const logs = detail.log_summary ?? []
  const safetyLogs = logs.filter(
    (log) => log.event_type === 'security' || log.status === 'blocked',
  )
  const isBlocked = safetyLogs.some((log) => log.status === 'blocked')
  const lanes: TopologyLane[] = [
    {
      id: 'input',
      title: '入口',
      nodes: [
        {
          id: 'input.user',
          title: '用户请求',
          subtitle: detail.query || detail.turns.join(' / '),
          status: 'passed',
        },
      ],
    },
  ]

  if (safetyLogs.length > 0 || hasStage(detail, 'safety')) {
    lanes.push({
      id: 'safety',
      title: '安全拦截',
      nodes: safetyLogs.length
        ? safetyLogs.map((log, index) => {
            const metadata = isRecord(log.metadata) ? log.metadata : {}
            const error = isRecord(log.error) ? log.error : {}
            return {
              id: `safety.${index}`,
              title: metadata.source === 'prompt_guard' ? 'Prompt Guard' : '安全检查',
              subtitle: String(log.name || error.reason || log.status || ''),
              status: log.status === 'blocked' ? 'blocked' : statusForStage(detail, 'safety'),
            }
          })
        : [
            {
              id: 'safety.check',
              title: 'Prompt Guard',
              subtitle: stageReasons(detail, 'safety').join(' / '),
              status: statusForStage(detail, 'safety'),
            },
          ],
    })
  }

  if (isBlocked) return lanes

  lanes.push({
    id: 'routing',
    title: '路由',
    nodes: buildRoutingNodes(detail),
  })

  const toolNodes = buildToolNodes(detail)
  if (toolNodes.length > 0) {
    lanes.push({
      id: 'tools',
      title: 'Skill / Tool',
      nodes: toolNodes,
    })
  }

  const answerNodes = buildAnswerNodes(detail)
  if (answerNodes.length > 0) {
    lanes.push({
      id: 'answer',
      title: '回答',
      nodes: answerNodes,
    })
  }

  return lanes
}

function buildRoutingNodes(detail: CaseEvaluationDetail): TopologyNode[] {
  const routingTrace = detail.routing_trace ?? []
  if (routingTrace.length > 0) {
    return routingTrace.map((trace, index) => ({
      id: `routing.${index}`,
      title: String(trace.stage || `routing-${index + 1}`),
      subtitle: compactText([
        trace.status,
        trace.selected_skill,
        Array.isArray(trace.selected_skills) ? trace.selected_skills.join(', ') : undefined,
        trace.reason,
      ]),
      status: statusForTrace(trace, statusForStage(detail, 'routing')),
    }))
  }

  return [
    {
      id: 'routing.selection',
      title: detail.selected_skills.length > 0 ? detail.selected_skills.join(', ') : 'Skill selection',
      subtitle:
        detail.expected_skills.length > 0
          ? `expected: ${detail.expected_skills.join(', ')}`
          : 'no skill expected',
      status: statusForStage(detail, 'routing'),
    },
  ]
}

function buildToolNodes(detail: CaseEvaluationDetail): TopologyNode[] {
  const nodes: TopologyNode[] = detail.selected_skills.map((skill) => ({
    id: `skill.${skill}`,
    title: skill,
    subtitle: 'selected skill',
    status: statusForStage(detail, 'routing'),
  }))
  const actualToolCalls = detail.actual_tool_calls ?? []
  const logs = detail.log_summary ?? []
  const calls = actualToolCalls.length > 0
    ? actualToolCalls
    : logs.filter((log) => log.event_type === 'tool' || log.event_type === 'tool_retry')

  calls.forEach((call, index) => {
    const name = String(call.name || call.tool || `tool-${index + 1}`)
    const status = String(call.status || '')
    nodes.push({
      id: `tool.${index}.${name}`,
      title: name,
      subtitle: compactText([status, stringifyArgs(call.args || call.input)]),
      status: status === 'failed' || status === 'retrying' ? 'failed' : statusForStage(detail, 'tool'),
    })
  })

  return nodes
}

function buildAnswerNodes(detail: CaseEvaluationDetail): TopologyNode[] {
  const nodes: TopologyNode[] = []
  if (detail.final_answer) {
    nodes.push({
      id: 'answer.final',
      title: '最终回答',
      subtitle: detail.final_answer,
      status: statusForStage(detail, 'answer'),
    })
  }
  if (detail.judge) {
    nodes.push({
      id: 'answer.judge',
      title: 'Judge',
      subtitle: detail.judge.reason || detail.judge.recommendation,
      status: detail.judge.passed === false ? 'failed' : 'passed',
    })
  }
  return nodes
}

function statusForStage(detail: CaseEvaluationDetail, stage: string): TopologyNodeStatus {
  const stageChecks = detail.checks.filter((check) => check.stage === stage)
  if (stageChecks.some((check) => !check.passed)) {
    return detail.status === 'warning' ? 'warning' : 'failed'
  }
  if (stageChecks.length > 0) return 'passed'
  return detail.status === 'fail' && detail.suspected_node === stage ? 'failed' : 'passed'
}

function statusForTrace(
  trace: Record<string, unknown>,
  fallback: TopologyNodeStatus,
): TopologyNodeStatus {
  const status = String(trace.status || '')
  if (['failed', 'rejected', 'blocked'].includes(status)) return 'failed'
  if (['missed', 'below_threshold'].includes(status)) return 'idle'
  if (['selected', 'completed', 'matched'].includes(status)) return 'passed'
  return fallback
}

function hasStage(detail: CaseEvaluationDetail, stage: string) {
  return detail.checks.some((check) => check.stage === stage)
}

function stageReasons(detail: CaseEvaluationDetail, stage: string) {
  return detail.checks
    .filter((check) => check.stage === stage)
    .map((check) => check.reason || check.name)
    .filter(Boolean)
}

function compactText(values: unknown[]) {
  return values
    .filter((value) => value !== null && value !== undefined && value !== '')
    .map((value) => String(value))
    .join(' / ')
}

function stringifyArgs(value: unknown) {
  if (!isRecord(value) || Object.keys(value).length === 0) return ''
  return JSON.stringify(value)
}

function EvaluationDetails({ details }: { details: CaseEvaluationDetail[] }) {
  const caseResults = details.map((detail) => {
    const checks = detail.checks
    const failedStages = Array.from(new Set(checks.filter(c => !c.passed).map(c => c.stage)))
    const status =
      detail.status ??
      (failedStages.length === 0
        ? 'pass'
        : failedStages.some((stage) => ['safety', 'routing'].includes(stage))
          ? 'fail'
          : 'warning')
    return { detail: { ...detail, status }, checks, failedStages }
  })
  const passedCount = caseResults.filter(r => r.detail.status === 'pass').length
  const warningCount = caseResults.filter(r => r.detail.status === 'warning').length
  const failedCount = caseResults.filter(r => r.detail.status === 'fail').length
  // 统计各阶段失败数量（warning不算失败）
  const failedCases = caseResults.filter(r => r.detail.status === 'fail')
  const safetyFailed = failedCases.filter(r => r.failedStages.includes('safety')).length
  const routingFailed = failedCases.filter(r => r.failedStages.includes('routing')).length
  const toolFailed = failedCases.filter(r => r.failedStages.includes('tool')).length
  const hallucinationFailed = failedCases.filter(r => r.failedStages.includes('hallucination')).length
  const answerFailed = failedCases.filter(r => r.failedStages.includes('answer')).length

  // 状态标签样式和文本
  const getStatusConfig = (status: string, hasSafetyFail: boolean) => {
    if (status === 'pass') return { text: 'PASS', color: '#16a34a', bg: '#f0fdf4', borderColor: '#16a34a' }
    if (status === 'warning') return { text: 'WARN', color: '#d97706', bg: '#fffbeb', borderColor: '#f59e0b' }
    // fail
    return {
      text: 'FAIL',
      color: hasSafetyFail ? '#dc2626' : '#ef4444',
      bg: '#fef2f2',
      borderColor: hasSafetyFail ? '#dc2626' : '#ef4444'
    }
  }

  return (
    <section className="evaluation-details" aria-label="Evaluation details">
      <div className="evaluation-details-header">
        <div>
          <h3>Evaluation Details</h3>
          <p>
            {passedCount} passed / {warningCount} warning / {failedCount} failed / 共 {details.length} cases
          </p>
        </div>
        <div className="evaluation-stage-chips" aria-label="Result summary">
          <span className="stage-chip stage-passed">passed {passedCount}</span>
          {warningCount > 0 && (
            <span className="stage-chip" style={{ background: '#fef3c7', color: '#92400e', border: '1px solid #fcd34d' }} title="多选技能等非严重问题，不判定为失败">warning {warningCount}</span>
          )}
          {failedCount > 0 && (
            <span className="stage-chip" style={{ background: '#fee2e2', color: '#991b1b', border: '1px solid #fca5a5' }}>failed {failedCount}</span>
          )}
          {safetyFailed > 0 && (
            <span className="stage-chip stage-safety" title="安全拦截类失败">safety {safetyFailed}</span>
          )}
          {routingFailed > 0 && (
            <span className="stage-chip stage-routing">routing {routingFailed}</span>
          )}
          {toolFailed > 0 && (
            <span className="stage-chip stage-tool">tool {toolFailed}</span>
          )}
          {hallucinationFailed > 0 && (
            <span className="stage-chip stage-hallucination">hallucination {hallucinationFailed}</span>
          )}
          {answerFailed > 0 && (
            <span className="stage-chip stage-answer">answer {answerFailed}</span>
          )}
        </div>
      </div>
      <details className="evaluation-case-list-wrapper">
        <summary className="evaluation-case-list-toggle">
          <span>展开详情 ({details.length} cases)</span>
        </summary>
        <div className="evaluation-case-list">
        {caseResults.map(({ detail, checks }) => {
          // 生成原因摘要
          const primaryChecks = checks
          const failedChecks = primaryChecks.filter(check => !check.passed)
          const reasons: string[] = []
          const hasSafetyFail = failedChecks.some(c => c.stage === 'safety')
          const isWarning = detail.status === 'warning'

          // 优先显示安全类失败原因
          const safetyFail = failedChecks.find(c => c.stage === 'safety')
          if (safetyFail) {
            reasons.push(`🔒 安全拦截: ${safetyFail.reason || '触发安全防护规则'}`)
          }
          // 路由类问题
          const routingFails = failedChecks.filter(c => c.stage === 'routing')
          routingFails.forEach(fail => {
            if (fail.name === 'skill_selection_precision') {
              const extra = (fail.actual as any)?.extra
              if (extra?.length) reasons.push(`⚠️ 多选技能: ${extra.join(', ')}`)
            } else if (fail.name === 'skill_selection_recall') {
              const missing = (fail.actual as any)?.missing
              if (missing?.length) reasons.push(`❌ 漏选技能: ${missing.join(', ')}`)
            }
          })
          // 工具/回答类失败原因
          const otherFails = failedChecks.filter(c => !['safety', 'routing'].includes(c.stage))
          otherFails.forEach(fail => {
            reasons.push(`❌ ${fail.stage}: ${fail.reason || fail.name}`)
          })

          const statusConfig = getStatusConfig(detail.status, hasSafetyFail)

          return (
          <details
            key={detail.case_id}
            className={`evaluation-case stage-${detail.status === 'fail' ? (hasSafetyFail ? 'safety' : 'routing') : detail.status}`}
            style={{ borderLeft: `4px solid ${statusConfig.borderColor}` }}
          >
            <summary>
              <span>{detail.case_id}</span>
              {detail.skill_selection_f1 != null && (
                <span
                  title="当前用例的路由F1分数：综合衡量精确率和召回率，100%为完全选对，0%为完全选错"
                  style={{ margin: '0 12px', padding: '2px 8px', borderRadius: '4px', background: '#f0f4f8', fontSize: '0.9em', cursor: 'help' }}
                >
                  F1: {(detail.skill_selection_f1 * 100).toFixed(1)}%
                </span>
              )}
              <strong style={{
                padding: '2px 8px',
                borderRadius: '4px',
                fontSize: '0.85em',
                background: statusConfig.bg,
                color: statusConfig.color,
              }}>
                {statusConfig.text}
              </strong>
            </summary>
            <div className="evaluation-case-body">
              {/* 用户query展示 */}
              <div className="evaluation-query" style={{ margin: '8px 0', padding: '8px 12px', background: '#f8f9fa', borderRadius: '4px', fontSize: '0.95em' }}>
                <strong style={{ color: '#666' }}>用户Query: </strong>
                <span>{detail.query || '(多轮对话，共' + detail.turns.length + '轮)'}</span>
              </div>

              {/* 原因摘要（warning和fail都显示） */}
              {reasons.length > 0 && (
                <div
                  className="evaluation-fail-reasons"
                  style={{
                    margin: '8px 0',
                    padding: '8px 12px',
                    background: statusConfig.bg,
                    borderRadius: '4px',
                    borderLeft: `3px solid ${statusConfig.borderColor}`
                  }}
                >
                  <strong style={{ color: statusConfig.color }}>
                    {isWarning ? '提示: ' : '失败原因: '}
                  </strong>
                  <ul style={{ margin: '4px 0 0 20px', padding: 0 }}>
                    {reasons.map((reason, idx) => (
                      <li key={idx} style={{ color: '#374151' }}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}

              {detail.status === 'fail' && (
                <div className="evaluation-detail-grid">
                  <EvaluationJsonBlock
                    label="Expected"
                    value={{
                      skills: detail.expected_skills,
                      tool_calls: detail.expected_tool_calls,
                    }}
                  />
                  <EvaluationJsonBlock
                    label="Actual"
                    value={{
                      skills: detail.selected_skills,
                      tool_calls: detail.actual_tool_calls,
                    }}
                  />
                </div>
              )}
              {(detail.status === 'fail' || detail.status === 'warning') && (
                <EvaluationDiagnosticPanel detail={detail} />
              )}
              <div className="evaluation-checks">
                {primaryChecks.map((check) => {
                  const checkKey = `${check.stage}.${check.name}`
                  const tooltip = CHECK_TOOLTIPS[checkKey] || check.reason
                  return (
                    <span
                      key={`${detail.case_id}-${check.stage}-${check.name}`}
                      data-status={check.passed ? 'pass' : isWarning ? 'warning' : 'fail'}
                      title={tooltip}
                      style={{ cursor: 'help' }}
                    >
                      {check.passed ? 'PASS' : isWarning ? 'WARN' : 'FAIL'} {check.stage}.{check.name}
                      {check.reason ? `: ${check.reason}` : ''}
                    </span>
                  )
                })}
              </div>
            </div>
          </details>
        )})}
        </div>
      </details>
    </section>
  )
}

function EvaluationJsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="evaluation-json-block">
      <strong>{label}</strong>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </div>
  )
}

function EvaluationDiagnosticPanel({ detail }: { detail: CaseEvaluationDetail }) {
  const outputs = detail.diagnostic_outputs ?? {}
  const finalAnswer =
    typeof outputs.final_answer === 'string' ? outputs.final_answer : detail.final_answer
  const logs = Array.isArray(outputs.logs) ? outputs.logs : detail.log_summary
  const judge = isRecord(outputs.judge) ? outputs.judge : detail.judge
  const routingTrace = Array.isArray(outputs.routing_trace)
    ? outputs.routing_trace.filter(isRecord)
    : detail.routing_trace ?? []
  const missingFragments = Array.isArray(outputs.missing_answer_fragments)
    ? outputs.missing_answer_fragments
    : []
  const hasDiagnostics =
    detail.suspected_node ||
    finalAnswer ||
    missingFragments.length > 0 ||
    routingTrace.length > 0 ||
    judge ||
    logs.length > 0 ||
    Object.keys(outputs).length > 0

  if (!hasDiagnostics) return null

  return (
    <div
      className="evaluation-diagnostic-panel"
      style={{
        margin: '10px 0',
        padding: '10px 12px',
        border: '1px solid #dbe3ef',
        borderRadius: '6px',
        background: '#f8fafc',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          flexWrap: 'wrap',
          marginBottom: '8px',
        }}
      >
        <strong>Diagnostic output</strong>
        {detail.suspected_node && (
          <span
            style={{
              padding: '2px 8px',
              borderRadius: '4px',
              background: '#eef2ff',
              color: '#3730a3',
              fontSize: '0.85em',
            }}
          >
            Suspected node: {detail.suspected_node}
          </span>
        )}
      </div>
      {finalAnswer && (
        <div style={{ marginBottom: '8px' }}>
          <strong>Final answer</strong>
          <MarkdownRenderer content={finalAnswer} />
        </div>
      )}
      {missingFragments.length > 0 && (
        <div style={{ marginBottom: '8px' }}>
          <strong>Missing expected content</strong>
          <pre>{JSON.stringify(missingFragments, null, 2)}</pre>
        </div>
      )}
      {routingTrace.length > 0 && (
        <div style={{ marginBottom: '8px' }}>
          <strong>Routing funnel</strong>
          <div className="evaluation-routing-funnel">
            {routingTrace.map((step, index) => (
              <div
                key={`${String(step.stage)}-${index}`}
                className="evaluation-routing-step"
              >
                <span className="evaluation-routing-stage">
                  {String(step.stage ?? `stage-${index + 1}`)}
                </span>
                <div className="evaluation-routing-step-body">
                  <span
                    style={{
                      display: 'inline-block',
                      padding: '1px 6px',
                      borderRadius: '4px',
                      background: '#f1f5f9',
                      color: '#334155',
                      fontSize: '0.85em',
                      marginBottom: '4px',
                    }}
                  >
                    {String(step.status ?? 'unknown')}
                  </span>
                  {step.reason ? <div>{String(step.reason)}</div> : null}
                  <pre>{JSON.stringify(step, null, 2)}</pre>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {judge && (
        <div style={{ marginBottom: '8px' }}>
          <strong>Judge</strong>
          <pre>{JSON.stringify(judge, null, 2)}</pre>
        </div>
      )}
      {logs.length > 0 && (
        <div>
          <strong>Execution outputs</strong>
          <pre>{JSON.stringify(logs, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatEvaluationError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error)
  const detailMatch = message.match(/"detail"\s*:\s*"([^"]+)"/)
  return detailMatch?.[1] ?? message
}

function formatMetricValue(metric: string, value: number) {
  return metric.toUpperCase() === 'CLS' ? value.toFixed(3) : `${Math.round(value)}ms`
}

// 指标说明映射
const METRIC_TOOLTIPS: Record<string, string> = {
  'Routing Exact Match': '路由精确匹配率：选中技能和期望技能完全一致（不多不少）的用例占比，要求最严格',
  'Routing Precision': '路由精确率：所有被选中的技能中，真正应该选的比例，越低说明多选的无关技能越多',
  'Routing Recall': '路由召回率：所有应该选的技能中，真的被选中的比例，越低说明漏选的必要技能越多',
  'Routing F1': '路由F1分数：精确率和召回率的调和平均值，综合衡量路由质量，越高越好',
  'Routing False Positive': '路由误报率：不需要任何技能的负向用例中，错误选中了技能的比例',
  'Routing Over-select': '过度选择率：多选了无关技能的用例占比',
  'Routing Under-select': '遗漏选择率：漏选了必要技能的用例占比',
  'Attack Block': '攻击拦截率：恶意提示词攻击被安全系统成功拦截的比例',
  'Unsafe Tools': '危险工具调用率：测试中实际调用了危险工具的比例，越低越好',
  'Secret Leak Rate': '敏感信息泄露率：回答中泄露了密钥、密码等禁止内容的比例，越低越好（Prompt Guard相关）',
  'Security Event Precision': '安全事件准确率：触发的安全事件类型和预期完全匹配的比例，越高说明安全拦截越精准（Prompt Guard相关）',
  'Tool Selection': '工具选择准确率：调用了所有期望工具的用例占比',
  'Tool F1': '工具调用F1：工具选择精确率和召回率的综合得分',
  'Argument Fidelity': '参数准确率：工具参数中包含所有期望关键字段的用例占比',
  'Argument F1': '参数F1：参数匹配的综合得分',
  'Answer Contains': '回答覆盖率：回答中包含了所有期望内容的用例占比',
  'Answer Violations': '回答违规率：回答中出现了禁止内容的比例，越低越好',
  'Answer Hallucination': '回答幻觉率：回答中编造了无依据内容的比例，越低越好',
  'Repeated Tools': '重复工具调用率：相同参数重复调用同一工具（陷入无效循环）的比例，越低越好',
  'Argument Hallucination': '参数幻觉率：工具参数编造了不存在值的比例，越低越好',
  'Evidence Usage': '证据使用率：工具返回的真实数据在回答中被引用的比例，越高说明回答越 grounded',
  'Unsupported Answer': '无依据回答率：回答内容没有工具返回结果支撑的比例，越低越好',
  'Intent Accuracy': '意图准确率：子Agent意图分类和预期完全一致的用例占比（multi-agent模式核心指标）',
  'Intent Precision': '意图精确率：所有被分类的意图中，正确的比例（multi-agent模式）',
  'Intent Recall': '意图召回率：所有期望意图中，被正确识别的比例（multi-agent模式）',
  'Intent F1': '意图F1分数：意图分类的精确率和召回率的调和平均值（multi-agent模式）',
  'Metric Extraction Recall': '指标提取召回率：查询中的APM指标（如p95、LCP）被正确提取的比例',
  'Entity Extraction Recall': '实体提取召回率：查询中的关键实体被正确提取的比例',
}

const CHECK_TOOLTIPS: Record<string, string> = {
  'routing.skill_selection_precision': '技能选择精确率检查：是否多选了无关技能',
  'routing.skill_selection_recall': '技能选择召回率检查：是否漏选了必要技能',
  'routing.skill_selection_exact_match': '技能精确匹配检查：选中技能和期望技能是否完全一致',
  'safety.security_event': '安全检查：是否成功拦截了恶意请求',
  'tool.tool_selection': '工具选择检查：是否调用了正确的工具',
  'tool.tool_arguments': '工具参数检查：工具参数是否正确',
  'hallucination.repeated_tool_call': '重复调用检查：是否重复调用相同参数的工具（无效循环）',
  'tool.forbidden_tools': '禁用工具检查：是否调用了禁止使用的工具',
  'answer.answer_contains': '回答内容检查：回答是否包含了期望的关键信息',
  'hallucination.answer_hallucination': '回答幻觉检查：回答是否包含了无依据的编造内容',
  'tool.tool_execution': '工具执行检查：工具运行是否成功没有报错',
  'routing.intent_match': '意图匹配检查：NLU意图分类是否和期望的子Agent一致（multi-agent模式）',
  'routing.metric_extraction': '指标提取检查：APM指标（p95/LCP等）是否被正确提取（multi-agent模式）',
  'routing.entity_extraction': '实体提取检查：关键实体是否被正确提取（multi-agent模式）',
}

function buildEvaluationSummary(report: SkillEvaluationReport) {
  const routingMetrics = report.routing as Record<string, number | null | undefined> | null | undefined
  const maRouting = report.multi_agent_routing
  const metrics: Array<{label: string, value: number | null | undefined, tooltip?: string}> = []

  // Multi-agent routing 指标（agent_mode=multi 时）
  if (maRouting) {
    metrics.push(
      { label: 'Intent Accuracy', value: maRouting.intent_accuracy, tooltip: METRIC_TOOLTIPS['Intent Accuracy'] },
      { label: 'Intent Precision', value: maRouting.intent_precision, tooltip: METRIC_TOOLTIPS['Intent Precision'] },
      { label: 'Intent Recall', value: maRouting.intent_recall, tooltip: METRIC_TOOLTIPS['Intent Recall'] },
      { label: 'Intent F1', value: maRouting.intent_f1, tooltip: METRIC_TOOLTIPS['Intent F1'] },
      { label: 'Metric Extraction Recall', value: maRouting.metric_extraction_recall, tooltip: METRIC_TOOLTIPS['Metric Extraction Recall'] },
      { label: 'Entity Extraction Recall', value: maRouting.entity_extraction_recall, tooltip: METRIC_TOOLTIPS['Entity Extraction Recall'] },
    )
  }

  // Single-agent 路由指标（quick和e2e都有）
  if (!maRouting && routingMetrics) {
    metrics.push(
      { label: 'Routing Exact Match', value: routingMetrics.selection_accuracy, tooltip: METRIC_TOOLTIPS['Routing Exact Match'] },
      { label: 'Routing Precision', value: routingMetrics.skill_selection_precision, tooltip: METRIC_TOOLTIPS['Routing Precision'] },
      { label: 'Routing Recall', value: routingMetrics.skill_selection_recall, tooltip: METRIC_TOOLTIPS['Routing Recall'] },
      { label: 'Routing F1', value: routingMetrics.skill_selection_f1, tooltip: METRIC_TOOLTIPS['Routing F1'] },
      { label: 'Routing False Positive', value: routingMetrics.false_positive_rate, tooltip: METRIC_TOOLTIPS['Routing False Positive'] },
      { label: 'Routing Over-select', value: routingMetrics.skill_over_selection_rate, tooltip: METRIC_TOOLTIPS['Routing Over-select'] },
      { label: 'Routing Under-select', value: routingMetrics.skill_under_selection_rate, tooltip: METRIC_TOOLTIPS['Routing Under-select'] },
    )
  }

  // 安全指标（快检仅显示Prompt Guard相关指标，E2E显示全部安全指标）
  if (report.safety) {
    // 快检模式判断：没有工具/回答/幻觉指标说明是快检
    const isQuickMode = !report.tools && !report.answers && !report.hallucinations
    // Prompt Guard核心指标，两种模式都显示
    metrics.push(
      { label: 'Attack Block', value: report.safety.attack_block_rate, tooltip: METRIC_TOOLTIPS['Attack Block'] },
      { label: 'Security Event Precision', value: report.safety.security_event_precision, tooltip: METRIC_TOOLTIPS['Security Event Precision'] },
    )
    // 以下指标仅E2E模式有意义（快检不执行工具、不生成回答，指标恒为0）
    if (!isQuickMode) {
      metrics.push(
        { label: 'Unsafe Tools', value: report.safety.unsafe_tool_call_rate, tooltip: METRIC_TOOLTIPS['Unsafe Tools'] },
        { label: 'Secret Leak Rate', value: report.safety.secret_leak_rate, tooltip: METRIC_TOOLTIPS['Secret Leak Rate'] },
      )
    }
  }

  // 工具指标（仅e2e有）
  if (report.tools) {
    metrics.push(
      { label: 'Tool Selection', value: report.tools.tool_selection_accuracy, tooltip: METRIC_TOOLTIPS['Tool Selection'] },
      { label: 'Tool F1', value: report.tools.tool_call_f1, tooltip: METRIC_TOOLTIPS['Tool F1'] },
      { label: 'Argument Fidelity', value: report.tools.argument_fidelity, tooltip: METRIC_TOOLTIPS['Argument Fidelity'] },
      { label: 'Argument F1', value: report.tools.argument_f1, tooltip: METRIC_TOOLTIPS['Argument F1'] },
    )
  }

  // 回答指标（仅e2e有）
  if (report.answers) {
    metrics.push(
      { label: 'Answer Contains', value: report.answers.answer_contains_rate, tooltip: METRIC_TOOLTIPS['Answer Contains'] },
      { label: 'Answer Violations', value: report.answers.forbidden_answer_violation_rate, tooltip: METRIC_TOOLTIPS['Answer Violations'] },
    )
  }

  // 幻觉指标（仅e2e有）
  if (report.hallucinations) {
    metrics.push(
      { label: 'Answer Hallucination', value: report.hallucinations.answer_hallucination_rate, tooltip: METRIC_TOOLTIPS['Answer Hallucination'] },
      { label: 'Repeated Tools', value: report.hallucinations.repeated_tool_call_rate, tooltip: METRIC_TOOLTIPS['Repeated Tools'] },
      { label: 'Argument Hallucination', value: report.hallucinations.tool_argument_hallucination_rate, tooltip: METRIC_TOOLTIPS['Argument Hallucination'] },
      { label: 'Evidence Usage', value: report.hallucinations.tool_evidence_usage_rate, tooltip: METRIC_TOOLTIPS['Evidence Usage'] },
      { label: 'Unsupported Answer', value: report.hallucinations.unsupported_answer_rate, tooltip: METRIC_TOOLTIPS['Unsupported Answer'] },
    )
  }

  // 格式化值，过滤掉完全没有数据的指标
  return metrics
    .filter(metric => metric.value != null)
    .map(metric => ({ ...metric, value: formatPercent(metric.value) }))
}

function getSkillScore(skill: SkillInfo) {
  return skill.latest_evaluation?.overall_score ?? skill.evaluation?.overall_score
}

function mergeLatestSkillEvaluations(
  skills: SkillInfo[],
  snapshots: SkillEvaluationSnapshot[],
) {
  const latestBySkill = new Map(
    snapshots.map((snapshot) => [snapshot.skill_name, snapshot]),
  )
  return skills.map((skill) => ({
    ...skill,
    latest_evaluation: latestBySkill.get(skill.name) ?? skill.latest_evaluation,
  }))
}
