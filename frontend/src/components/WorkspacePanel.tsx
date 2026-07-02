import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ExecutionLog,
  type ExecutionSummary,
  type ReplayResponse,
  type ReplayState,
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
  const [goldenPath, setGoldenPath] = useState('')
  const [evaluationRunning, setEvaluationRunning] = useState(false)
  const [evaluationResetting, setEvaluationResetting] = useState(false)
  const [evaluationProgress, setEvaluationProgress] = useState<{
    mode: 'quick' | 'e2e'
    source: string
    total: number
    completed: number
    percent: number
  } | null>(null)

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
      setSkills(await api.listSkills())
    } catch {
      setSkills([])
    }
    setSkillsLoading(false)
  }, [])

  const runSkillEvaluation = async (mode: 'quick' | 'e2e') => {
    const trimmedPath = goldenPath.trim()
    if (!trimmedPath) return

    setEvaluationRunning(true)
    setEvaluationProgress(null)
    try {
      for await (const event of api.runSkillEvaluationStream({
        golden_path: trimmedPath,
        evaluation_mode: mode,
      })) {
        applyEvaluationEvent(event)
      }
    } catch {
      // silently handle
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
      setSkills((prev) => mergeLatestSkillEvaluations(prev, event.results))
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
    }
  }, [loadSkills, panel])

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

  return (
    <section className="workspace-panel" aria-label="行军案台">
      {panel === 'skills' && (
        <div className="workspace-section skill-evaluation-section">
          <div className="workspace-header">
            <div>
              <h2>Skill Evaluation</h2>
              <p>盘点当前 Skill 的描述清晰度、代码规模、复杂度和器具配置。</p>
            </div>
            <div>
              <div className="workspace-actions">
                <label className="skill-evaluation-runner">
                  <span>Golden dataset path</span>
                  <input
                    aria-label="Golden dataset path"
                    value={goldenPath}
                    onChange={(event) => setGoldenPath(event.target.value)}
                    placeholder="golden.jsonl"
                  />
                </label>
              </div>
              <div className="workspace-actions">
                <button
                  type="button"
                  onClick={() => runSkillEvaluation('quick')}
                  disabled={evaluationRunning || goldenPath.trim().length === 0}
                >
                  快速巡检
                </button>
                <button
                  type="button"
                  onClick={() => runSkillEvaluation('e2e')}
                  disabled={evaluationRunning || goldenPath.trim().length === 0}
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

          {!skillsLoading && skills.length === 0 && (
            <div className="workspace-empty">当前没有可评测的 Skill。</div>
          )}
          {!skillsLoading && skills.length > 0 && (
            <div className="skill-evaluation-grid">
              {skills.map((skill) => (
                <section key={skill.name} className="skill-evaluation-card">
                  <div className="skill-evaluation-card-header">
                    <div>
                      <h3>{skill.name}</h3>
                      <p title={skill.description}>{skill.description}</p>
                      {skill.latest_evaluation?.source && (
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
                        width: `${Math.round((getSkillScore(skill) ?? 0) * 100)}%`,
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
                </section>
              ))}
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
  return `${Math.round((value ?? 0) * 100)}%`
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
