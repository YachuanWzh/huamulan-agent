import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ExecutionLog,
  type ExecutionSummary,
  type CaseEvaluationDetail,
  type ReplayResponse,
  type ReplayState,
  type SkillEvaluationDataset,
  type SkillEvaluationReport,
  type SkillEvaluationSnapshot,
  type SkillEvaluationStreamEvent,
  type SkillInfo,
} from '../lib/api'

interface Props {
  panel: 'skills' | 'checkpoint' | 'audit'
  threadId: string | null
  onThreadCleared?: () => void
  onReplayState?: (state: ReplayState) => void
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
}: Props) {
  const [replay, setReplay] = useState<ReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)
  const [historyDeleting, setHistoryDeleting] = useState(false)
  const [executionLogs, setExecutionLogs] = useState<ExecutionLog[]>([])
  const [executionSummary, setExecutionSummary] = useState<ExecutionSummary | null>(null)
  const [executionLoading, setExecutionLoading] = useState(false)
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
    setEvaluationReport(null)
    setEvaluationError(null)
    try {
      for await (const event of api.runSkillEvaluationStream({
        golden_path: trimmedPath,
        evaluation_mode: mode,
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

          {evaluationReport && (
            <section className="claw-eval-summary" aria-label="ClawEval summary">
              <h3>ClawEval</h3>
              <dl className="skill-evaluation-metrics">
                {buildEvaluationSummary(evaluationReport).map((metric) => (
                  <div key={metric.label}>
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

function EvaluationDetails({ details }: { details: CaseEvaluationDetail[] }) {
  const routingResults = details.map((detail) => {
    const checks = detail.checks.filter((check) => check.stage === 'routing')
    const passed = checks.length > 0 && checks.every((check) => check.passed)
    return { detail, checks, passed }
  })
  const failedCount = routingResults.filter((result) => !result.passed).length
  const passedCount = routingResults.length - failedCount

  return (
    <section className="evaluation-details" aria-label="Evaluation details">
      <div className="evaluation-details-header">
        <div>
          <h3>Evaluation Details</h3>
          <p>
            {failedCount} routing failed / {details.length} cases
          </p>
        </div>
        <div className="evaluation-stage-chips" aria-label="Failure stage summary">
          <span className="stage-chip stage-passed">routing passed {passedCount}</span>
          {failedCount > 0 && (
            <span className="stage-chip stage-routing">routing failed {failedCount}</span>
          )}
        </div>
      </div>
      <details className="evaluation-case-list-wrapper">
        <summary className="evaluation-case-list-toggle">
          <span>展开详情 ({details.length} cases)</span>
        </summary>
        <div className="evaluation-case-list">
        {routingResults.map(({ detail, checks, passed }) => (
          <details
            key={detail.case_id}
            className={`evaluation-case stage-${passed ? 'passed' : 'routing'}`}
          >
            <summary>
              <span>{detail.case_id}</span>
              <strong>{passed ? 'PASS' : 'FAIL'}</strong>
            </summary>
            <div className="evaluation-case-body">
              {!passed && (
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
              <div className="evaluation-checks">
                {checks.map((check) => (
                  <span
                    key={`${detail.case_id}-${check.stage}-${check.name}`}
                    data-status={check.passed ? 'pass' : 'fail'}
                  >
                    {check.passed ? 'PASS' : 'FAIL'} {check.stage}.{check.name}
                    {check.reason ? `: ${check.reason}` : ''}
                  </span>
                ))}
              </div>
            </div>
          </details>
        ))}
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

function formatEvaluationError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error)
  const detailMatch = message.match(/"detail"\s*:\s*"([^"]+)"/)
  return detailMatch?.[1] ?? message
}

function buildEvaluationSummary(report: SkillEvaluationReport) {
  return [
    {
      label: 'Attack Block',
      value: formatPercent(report.safety?.attack_block_rate),
    },
    {
      label: 'Unsafe Tools',
      value: formatPercent(report.safety?.unsafe_tool_call_rate),
    },
    {
      label: 'Tool Selection',
      value: formatPercent(report.tools?.tool_selection_accuracy),
    },
    {
      label: 'Tool F1',
      value: formatPercent(report.tools?.tool_call_f1),
    },
    {
      label: 'Argument Fidelity',
      value: formatPercent(report.tools?.argument_fidelity),
    },
    {
      label: 'Argument F1',
      value: formatPercent(report.tools?.argument_f1),
    },
    {
      label: 'Answer Contains',
      value: formatPercent(report.answers?.answer_contains_rate),
    },
    {
      label: 'Answer Violations',
      value: formatPercent(report.answers?.forbidden_answer_violation_rate),
    },
    {
      label: 'Answer Hallucination',
      value: formatPercent(report.hallucinations?.answer_hallucination_rate),
    },
    {
      label: 'Repeated Tools',
      value: formatPercent(report.hallucinations?.repeated_tool_call_rate),
    },
    {
      label: 'Argument Hallucination',
      value: formatPercent(report.hallucinations?.tool_argument_hallucination_rate),
    },
    {
      label: 'Evidence Usage',
      value: formatPercent(report.hallucinations?.tool_evidence_usage_rate),
    },
    {
      label: 'Unsupported Answer',
      value: formatPercent(report.hallucinations?.unsupported_answer_rate),
    },
  ]
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
