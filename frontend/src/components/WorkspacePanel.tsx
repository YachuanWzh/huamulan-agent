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
    <section className="workspace-panel" aria-label="Operations workspace">
      {panel === 'checkpoint' && (
        <div className="workspace-section">
          <div className="workspace-header">
            <div>
              <h2>Thread Replay</h2>
              <p>Inspect checkpoints and replay a saved state in the conversation.</p>
            </div>
            <div className="workspace-actions">
              <button onClick={deleteCurrentHistory} disabled={historyDeleting}>
                Delete Checkpoints
              </button>
              <button onClick={clearAndStartNewThread} disabled={historyDeleting}>
                Clear and New Thread
              </button>
            </div>
          </div>
          {replayLoading && <div className="loading">Loading...</div>}
          {!replayLoading && (!replay || replay.states.length === 0) && (
            <div className="workspace-empty">No checkpoints for this thread.</div>
          )}
          {replay && replay.states.length > 0 && (
            <div className="workspace-state-list">
              {replay.states.map((state, i) => (
                <details key={i} className="workspace-state">
                  <summary>
                    <span>
                      Checkpoint {i + 1}
                      {state.node ? ` / ${state.node}` : ''}
                    </span>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.preventDefault()
                        onReplayState?.(state)
                      }}
                    >
                      Replay checkpoint {i + 1}
                    </button>
                  </summary>
                  <div className="workspace-meta">
                    {state.created_at && <span>{state.created_at}</span>}
                    {state.values.selected_skills?.length ? (
                      <span>Skills: {state.values.selected_skills.join(', ')}</span>
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
              <h2>Operational Audit</h2>
              <p>Inspect the thread trace, token usage, tool results, and retry chains.</p>
            </div>
            <div className="workspace-actions">
              <button onClick={loadExecutionAudit} disabled={executionLoading}>
                Refresh
              </button>
            </div>
          </div>

          <div className="audit-summary-grid">
            <div className="audit-metric">
              <span>Total Tokens</span>
              <strong>{executionSummary?.total_tokens ?? 0}</strong>
              <small>
                Prompt {executionSummary?.prompt_tokens ?? 0} / Completion{' '}
                {executionSummary?.completion_tokens ?? 0}
              </small>
            </div>
            <div className="audit-metric">
              <span>Tool Calls</span>
              <strong>{executionSummary?.tool_calls ?? 0}</strong>
            </div>
            <div className="audit-metric">
              <span>Errors</span>
              <strong>{executionSummary?.tool_errors ?? 0}</strong>
            </div>
            <div className="audit-metric">
              <span>Retries</span>
              <strong>{executionSummary?.tool_retries ?? 0}</strong>
            </div>
            <div className="audit-metric">
              <span>Duration</span>
              <strong>{executionSummary?.total_duration_ms ?? 0}ms</strong>
            </div>
          </div>

          <div className="workspace-tabs" role="tablist" aria-label="Audit filters">
            {(['all', 'llm', 'tool', 'tool_retry', 'security', 'approval'] as const).map(
              (filter) => (
                <button
                  key={filter}
                  role="tab"
                  aria-selected={auditFilter === filter}
                  className={auditFilter === filter ? 'active' : ''}
                  onClick={() => setAuditFilter(filter)}
                >
                  {filter === 'all' ? 'All' : filter}
                </button>
              ),
            )}
          </div>

          {executionLoading && <div className="loading">Loading...</div>}
          {!executionLoading && executionLogs.length === 0 && (
            <div className="workspace-empty">No execution logs for this thread.</div>
          )}
          {!executionLoading && retryChains.length > 0 && (
            <div className="retry-chain-list">
              {retryChains.map((chain) => (
                <section key={chain.toolCallId} className="retry-chain">
                  <h3>{chain.name} retry chain</h3>
                  <div className="retry-chain-steps">
                    {chain.attempts.map((attempt) => (
                      <span key={attempt.id}>
                        Attempt {String(attempt.metadata.attempt ?? '?')}{' '}
                        {attempt.status === 'completed' ? 'completed' : 'failed'}
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
                      <span>{log.status}</span>
                      {log.duration_ms != null && <span>{log.duration_ms}ms</span>}
                      {Number(log.token_usage.total_tokens ?? 0) > 0 && (
                        <span>{String(log.token_usage.total_tokens)} tokens</span>
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
