import { useEffect, useState, useCallback, useRef } from 'react'
import { api, type OtelAlert, type AgentMode } from '../lib/api'

type RcaStatus = 'idle' | 'analyzing' | 'completed' | 'failed' | 'need_approve'

interface RcaEntry {
  threadId: string
  status: RcaStatus
  pendingApprovals?: ToolCallApproval[]
}

interface ToolCallApproval {
  approval_id: string
  tool_call_id: string
  name: string
  args: Record<string, unknown>
}

interface Props {
  threadId: string | null
  agentMode: AgentMode
  /** Called when user wants to view RCA results — parent should switch to chat panel with this thread */
  onViewAnalysis?: (threadId: string) => void
}

export function OtelAlertsPanel({ threadId, agentMode, onViewAnalysis }: Props) {
  const [alerts, setAlerts] = useState<OtelAlert[]>([])
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [rcaStates, setRcaStates] = useState<Record<string, RcaEntry>>({})
  const triggeredRef = useRef<Set<string>>(new Set())

  const loadHistory = useCallback(async () => {
    setLoading(true)
    try {
      const history = await api.listOtelAlerts(50)
      setAlerts(history)
    } catch { /* silent */ }
    setLoading(false)
  }, [])

  // Poll RCA thread statuses
  useEffect(() => {
    const pollInterval = setInterval(async () => {
      setRcaStates((prev) => {
        const updated = { ...prev }
        let changed = false
        const promises: Promise<void>[] = []
        for (const [alertId, entry] of Object.entries(prev)) {
          if (entry.status === 'analyzing') {
            promises.push(
              api.getExecutionSummary(entry.threadId).then((summary) => {
                if (summary && summary.total_events > 0) {
                  // A completed analysis has events logged
                  updated[alertId] = { ...entry, status: 'completed' }
                  changed = true
                }
              }).catch(() => {
                // If we can't fetch, mark as failed if it's been more than 5 min
              })
            )
          }
        }
        Promise.allSettled(promises).then(() => {
          if (changed) setRcaStates({ ...updated })
        })
        return prev // return unchanged ref if nothing changed
      })
    }, 5000)
    return () => clearInterval(pollInterval)
  }, [])

  // SSE subscription — backend drives RCA for P0; frontend observes status
  useEffect(() => {
    setConnected(true)
    const controller = api.streamOtelAlerts((alert) => {
      setAlerts((prev) => {
        // Update existing alert if it's a status update, otherwise prepend
        const existingIdx = prev.findIndex((a) => a.id === alert.id)
        if (existingIdx >= 0) {
          const updated = [...prev]
          updated[existingIdx] = alert
          return updated
        }
        return [alert, ...prev.slice(0, 199)]
      })

      // Sync RCA state from backend alert status
      if (alert.rca_status && alert.id) {
        syncRcaFromBackend(alert)
      }

      // P0 fallback: if backend hasn't triggered RCA yet, do it from frontend
      if (alert.level === 'P0' && !alert.rca_status) {
        triggerRca(alert)
      }
    })
    const keepAlive = setInterval(() => setConnected(true), 10000)
    return () => {
      controller.abort()
      clearInterval(keepAlive)
      setConnected(false)
    }
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])

  /** Trigger RCA: create thread, send message, track status */
  const triggerRca = useCallback(async (alert: OtelAlert) => {
    if (triggeredRef.current.has(alert.id)) return
    triggeredRef.current.add(alert.id)

    setRcaStates((prev) => ({
      ...prev,
      [alert.id]: { threadId: '', status: 'analyzing' },
    }))

    try {
      const tid = crypto.randomUUID()
      const msg = buildRcaPrompt(alert)
      await api.chat({ thread_id: tid, message: msg, agent_mode: agentMode })

      setRcaStates((prev) => ({
        ...prev,
        [alert.id]: { threadId: tid, status: 'analyzing' },
      }))

      // Mark as completed after a short delay (chat is synchronous API call)
      setTimeout(() => {
        setRcaStates((prev) => {
          const cur = prev[alert.id]
          if (cur && cur.status === 'analyzing') {
            return { ...prev, [alert.id]: { ...cur, status: 'completed' } }
          }
          return prev
        })
      }, 2000)
    } catch (err) {
      console.error('RCA trigger failed:', err)
      setRcaStates((prev) => ({
        ...prev,
        [alert.id]: { ...prev[alert.id], status: 'failed' },
      }))
    }
  }, [agentMode])

  /** Sync RCA state from backend alert's rca_status field */
  const syncRcaFromBackend = useCallback((alert: OtelAlert) => {
    if (!alert.rca_status || !alert.id) return

    const statusMap: Record<string, RcaStatus> = {
      pending: 'idle',
      running: 'analyzing',
      completed: 'completed',
      blocked: 'need_approve',
      failed: 'failed',
    }
    const mappedStatus = statusMap[alert.rca_status] || 'idle'

    setRcaStates((prev) => ({
      ...prev,
      [alert.id]: {
        threadId: alert.rca_thread_id || prev[alert.id]?.threadId || '',
        status: mappedStatus,
        pendingApprovals: alert.rca_pending_approvals || undefined,
      },
    }))
  }, [])

  /** Approve or deny a dangerous tool during P0 RCA */
  const handleApprove = useCallback(async (
    alertId: string,
    approvalId: string,
    approved: boolean,
  ) => {
    const entry = rcaStates[alertId]
    if (!entry?.threadId) return

    setRcaStates((prev) => ({
      ...prev,
      [alertId]: { ...prev[alertId], status: 'analyzing' },
    }))

    try {
      await api.approveOtelRca(alertId, {
        thread_id: entry.threadId,
        approval_id: approvalId,
        approved,
      })
      // Status will be updated via SSE when backend re-broadcasts
    } catch (err) {
      console.error('RCA approval failed:', err)
      setRcaStates((prev) => ({
        ...prev,
        [alertId]: { ...prev[alertId], status: 'failed' },
      }))
    }
  }, [rcaStates])

  /** Open an existing thread in chat view */
  const handleViewAnalysis = useCallback((tid: string) => {
    if (onViewAnalysis) {
      onViewAnalysis(tid)
    } else {
      localStorage.setItem('threadId', tid)
      window.location.reload()
    }
  }, [onViewAnalysis])

  return (
    <div className="workspace-section alerts-section">
      <div className="workspace-header">
        <div>
          <h2>OTEL Alerts</h2>
          <p>
            {connected ? (
              <span className="sse-status live" title="SSE connected">
                ● Live &mdash; receiving real-time P0/P1 alerts from AlertManager
              </span>
            ) : (
              <span className="sse-status" title="SSE disconnected">○ Disconnected</span>
            )}
          </p>
        </div>
        <div className="workspace-actions">
          <button onClick={loadHistory} disabled={loading}>
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </div>

      {alerts.length === 0 && !loading && (
        <div className="workspace-empty">
          No alerts received yet. P0/P1 alerts from the OpenTelemetry Demo
          will appear here in real-time.
        </div>
      )}

      {alerts.length > 0 && (
        <div className="alerts-feed" role="log" aria-label="Alert feed">
          {alerts.map((alert) => (
            <AlertCard
              key={alert.id}
              alert={alert}
              rca={rcaStates[alert.id] ?? null}
              onAnalyze={() => triggerRca(alert)}
              onViewAnalysis={handleViewAnalysis}
              onApprove={(approvalId, approved) => handleApprove(alert.id, approvalId, approved)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function AlertCard({
  alert,
  rca,
  onAnalyze,
  onViewAnalysis,
  onApprove,
}: {
  alert: OtelAlert
  rca: RcaEntry | null
  onAnalyze: () => void
  onViewAnalysis: (threadId: string) => void
  onApprove: (approvalId: string, approved: boolean) => void
}) {
  const isP0 = alert.level === 'P0'
  const severityClass = isP0 ? 'alert-critical' : 'alert-warning'

  return (
    <article className={`alert-card ${severityClass}`} role="article">
      <div className="alert-card-header">
        <span className={`alert-badge ${isP0 ? 'badge-p0' : 'badge-p1'}`}>
          {alert.level}
        </span>
        <strong className="alert-service">{alert.service_name}</strong>
        <span className="alert-name">{alert.alert_name}</span>
        <time className="alert-time" dateTime={alert.received_at}>
          {formatTime(alert.received_at)}
        </time>
      </div>

      <div className="alert-card-body">
        <p className="alert-summary">{alert.summary}</p>
        {alert.description && (
          <p className="alert-description">{alert.description}</p>
        )}
        <dl className="alert-meta">
          <div>
            <dt>Severity</dt>
            <dd>{alert.severity}</dd>
          </div>
          <div>
            <dt>Alert start</dt>
            <dd>{alert.starts_at ? new Date(alert.starts_at).toLocaleString() : '—'}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd className={alert.status === 'firing' ? 'status-firing' : 'status-resolved'}>
              {alert.status}
            </dd>
          </div>
          {rca?.threadId && (
            <div>
              <dt>Thread</dt>
              <dd className="alert-thread-id" title={rca.threadId}>
                {rca.threadId.slice(0, 8)}...
              </dd>
            </div>
          )}
        </dl>
      </div>

      <div className="alert-card-actions">
        <RcaAction
          isP0={isP0}
          rca={rca}
          onAnalyze={onAnalyze}
          onViewAnalysis={onViewAnalysis}
          onApprove={onApprove}
        />
      </div>
    </article>
  )
}

function RcaAction({
  isP0,
  rca,
  onAnalyze,
  onViewAnalysis,
  onApprove,
}: {
  isP0: boolean
  rca: RcaEntry | null
  onAnalyze: () => void
  onViewAnalysis: (threadId: string) => void
  onApprove: (approvalId: string, approved: boolean) => void
}) {
  if (!rca) {
    // No RCA triggered yet
    if (isP0) {
      return (
        <span className="alert-auto-note">
          ⚡ Auto-triggering...
        </span>
      )
    }
    return (
      <button type="button" className="alert-analyze-btn" onClick={onAnalyze}>
        🔍 Analyze
      </button>
    )
  }

  if (rca.status === 'analyzing') {
    return (
      <span className="alert-analyzing">
        <span className="alert-spinner" /> Analyzing... (thread: {rca.threadId ? `${rca.threadId.slice(0, 8)}...` : 'creating...'})
      </span>
    )
  }

  if (rca.status === 'completed') {
    return (
      <button
        type="button"
        className="alert-view-btn"
        onClick={() => onViewAnalysis(rca.threadId)}
      >
        ✅ View Analysis
      </button>
    )
  }

  if (rca.status === 'failed') {
    return (
      <span className="alert-failed">
        ❌ RCA failed &mdash;
        <button type="button" className="alert-retry-btn" onClick={onAnalyze}>
          Retry
        </button>
      </span>
    )
  }

  if (rca.status === 'need_approve') {
    const pendingApproval = rca.pendingApprovals?.[0]
    const toolName = pendingApproval?.name || 'unknown tool'
    return (
      <div className="alert-approval-needed">
        <span className="alert-warning-icon">⚠️</span>
        <span className="alert-approval-text">
          Blocked: <code>{toolName}</code> needs approval
        </span>
        <div className="alert-approval-actions">
          {pendingApproval && (
            <>
              <button
                type="button"
                className="alert-approve-btn"
                onClick={() => onApprove(pendingApproval.approval_id, true)}
              >
                ✓ Approve
              </button>
              <button
                type="button"
                className="alert-deny-btn"
                onClick={() => onApprove(pendingApproval.approval_id, false)}
              >
                ✗ Deny
              </button>
            </>
          )}
        </div>
      </div>
    )
  }

  return null
}

function buildRcaPrompt(alert: OtelAlert): string {
  return [
    `🚨 ${alert.level} Alert received from OTEL push: **${alert.alert_name}**`,
    `- Service: **${alert.service_name}**`,
    `- Severity: ${alert.severity}`,
    `- Summary: ${alert.summary}`,
    alert.description ? `- Details: ${alert.description}` : '',
    alert.starts_at ? `- Alert started: ${alert.starts_at}` : '',
    '',
    'Please run root cause analysis using the otel-query skill:',
    '1. Pull Jaeger traces for the affected service',
    '2. Query Prometheus for correlated metrics',
    '3. Identify the root cause and recommend fixes',
  ].filter(Boolean).join('\n')
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    const yyyy = d.getFullYear()
    const MM = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    return `${yyyy}-${MM}-${dd} ${hh}:${mm}:${ss}`
  } catch {
    return iso
  }
}
