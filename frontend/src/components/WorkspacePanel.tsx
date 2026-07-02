import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ExecutionLog,
  type ExecutionSummary,
  type ReplayResponse,
  type ReplayState,
} from '../lib/api'

interface Props {
  panel: 'checkpoint' | 'audit'
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
