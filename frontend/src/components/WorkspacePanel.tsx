import { useCallback, useEffect, useState } from 'react'
import { api, type AuditEvent, type ReplayResponse, type ReplayState, type ToolError } from '../lib/api'

interface Props {
  panel: 'checkpoint' | 'audit'
  threadId: string | null
  onThreadCleared?: () => void
  onReplayState?: (state: ReplayState) => void
}

type AuditView = 'security' | 'tool-errors'

export function WorkspacePanel({
  panel,
  threadId,
  onThreadCleared,
  onReplayState,
}: Props) {
  const [replay, setReplay] = useState<ReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)
  const [historyDeleting, setHistoryDeleting] = useState(false)
  const [auditView, setAuditView] = useState<AuditView>('security')
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([])
  const [auditLoading, setAuditLoading] = useState(false)
  const [toolErrors, setToolErrors] = useState<ToolError[]>([])
  const [toolErrorsLoading, setToolErrorsLoading] = useState(false)

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

  const loadAuditEvents = useCallback(async () => {
    setAuditLoading(true)
    try {
      setAuditEvents(await api.listAuditEvents(threadId ?? undefined))
    } catch {
      setAuditEvents([])
    }
    setAuditLoading(false)
  }, [threadId])

  const loadToolErrors = useCallback(async () => {
    setToolErrorsLoading(true)
    try {
      setToolErrors(await api.listToolErrors(threadId ?? undefined))
    } catch {
      setToolErrors([])
    }
    setToolErrorsLoading(false)
  }, [threadId])

  useEffect(() => {
    if (panel === 'checkpoint') {
      loadReplay()
    }
  }, [loadReplay, panel])

  useEffect(() => {
    if (panel !== 'audit') return
    if (auditView === 'security') {
      loadAuditEvents()
    } else {
      loadToolErrors()
    }
  }, [auditView, loadAuditEvents, loadToolErrors, panel])

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
              <p>Review security decisions and tool failures with full-width detail.</p>
            </div>
            <div className="workspace-tabs" role="tablist" aria-label="Audit views">
              <button
                role="tab"
                aria-selected={auditView === 'security'}
                className={auditView === 'security' ? 'active' : ''}
                onClick={() => setAuditView('security')}
              >
                Security Audit
              </button>
              <button
                role="tab"
                aria-selected={auditView === 'tool-errors'}
                className={auditView === 'tool-errors' ? 'active' : ''}
                onClick={() => setAuditView('tool-errors')}
              >
                Tool Errors
              </button>
            </div>
          </div>

          {auditView === 'security' && (
            <>
              <div className="workspace-actions">
                <button onClick={loadAuditEvents} disabled={auditLoading}>Refresh</button>
              </div>
              {auditLoading && <div className="loading">Loading...</div>}
              {!auditLoading && auditEvents.length === 0 && (
                <div className="workspace-empty">No audit events for this thread.</div>
              )}
              {!auditLoading && auditEvents.length > 0 && (
                <ul className="workspace-audit-list">
                  {auditEvents.map((event) => (
                    <li key={event.id} className={`workspace-audit-item severity-${event.severity.toLowerCase()}`}>
                      <div className="workspace-row">
                        <span>{event.category}</span>
                        <strong>{event.severity}</strong>
                      </div>
                      <p>{event.reason}</p>
                      <div className="workspace-meta">
                        <span>{event.source}</span>
                        {event.created_at && <span>{new Date(event.created_at).toLocaleString()}</span>}
                      </div>
                      {event.subject && <pre>{event.subject}</pre>}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          {auditView === 'tool-errors' && (
            <>
              <div className="workspace-actions">
                <button onClick={loadToolErrors} disabled={toolErrorsLoading}>Refresh</button>
              </div>
              {toolErrorsLoading && <div className="loading">Loading...</div>}
              {!toolErrorsLoading && toolErrors.length === 0 && (
                <div className="workspace-empty">No tool errors for this thread.</div>
              )}
              {!toolErrorsLoading && toolErrors.length > 0 && (
                <ul className="workspace-error-list">
                  {toolErrors.map((error) => (
                    <li key={error.id} className="workspace-error-item">
                      <div className="workspace-row">
                        <span>{error.tool_name}</span>
                        <strong>{error.will_retry ? 'Retrying' : 'Final'}</strong>
                      </div>
                      <p>{error.error_type}: {error.error_message}</p>
                      <div className="workspace-meta">
                        <span>Attempt {error.attempt} / {error.max_attempts}</span>
                        {error.created_at && <span>{new Date(error.created_at).toLocaleString()}</span>}
                      </div>
                      <pre>{JSON.stringify(error.tool_args, null, 2)}</pre>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}
