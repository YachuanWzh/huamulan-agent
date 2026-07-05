import { useEffect, useState, useCallback } from 'react'
import { api, type OtelAlert, type AgentMode } from '../lib/api'

interface Props {
  threadId: string | null
  agentMode: AgentMode
  /** Called when user wants to trigger RCA for an alert (P1 confirm or manual) */
  onTriggerRca?: (alert: OtelAlert) => void
}

export function OtelAlertsPanel({ threadId, agentMode, onTriggerRca }: Props) {
  const [alerts, setAlerts] = useState<OtelAlert[]>([])
  const [loading, setLoading] = useState(false)
  const [connected, setConnected] = useState(false)
  const [lastP0AutoTriggered, setLastP0AutoTriggered] = useState<Set<string>>(new Set())

  // Load alert history on mount
  const loadHistory = useCallback(async () => {
    setLoading(true)
    try {
      const history = await api.listOtelAlerts(50)
      setAlerts(history)
    } catch {
      // silently fail
    }
    setLoading(false)
  }, [])

  // Subscribe to SSE for real-time alerts
  useEffect(() => {
    setConnected(true)
    const controller = api.streamOtelAlerts((alert) => {
      setAlerts((prev) => [alert, ...prev.slice(0, 199)])
      // Auto-trigger RCA for P0
      if (alert.level === 'P0') {
        handleP0AutoTrigger(alert)
      }
    })
    // Connection monitoring: assume connected after first successful poll
    const interval = setInterval(() => {
      setConnected(true) // will reset on reconnect
    }, 10000)
    return () => {
      controller.abort()
      clearInterval(interval)
      setConnected(false)
    }
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])

  /** Auto-trigger RCA for P0 alerts: create a thread and send agent analysis request */
  const handleP0AutoTrigger = useCallback(async (alert: OtelAlert) => {
    // Deduplicate: only trigger once per alert id
    if (lastP0AutoTriggered.has(alert.id)) return
    setLastP0AutoTriggered((prev) => new Set(prev).add(alert.id))

    try {
      const threadId = crypto.randomUUID()
      const message = buildRcaPrompt(alert)
      await api.chat({
        thread_id: threadId,
        message,
        agent_mode: agentMode,
      })
      console.log(`P0 auto-RCA triggered for ${alert.id}: ${alert.alert_name}`)
    } catch (err) {
      console.error('P0 auto-trigger failed:', err)
    }
  }, [agentMode, lastP0AutoTriggered])

  /** Trigger RCA for a P1 alert (user confirmation) */
  const handleP1Trigger = useCallback((alert: OtelAlert) => {
    if (onTriggerRca) {
      onTriggerRca(alert)
    } else {
      // Fallback: directly create a chat thread
      const threadId = crypto.randomUUID()
      const message = buildRcaPrompt(alert)
      api.chat({ thread_id: threadId, message, agent_mode: agentMode }).catch(console.error)
    }
  }, [agentMode, onTriggerRca])

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
              onAnalyze={() => handleP1Trigger(alert)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function AlertCard({ alert, onAnalyze }: { alert: OtelAlert; onAnalyze: () => void }) {
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
        </dl>
      </div>

      <div className="alert-card-actions">
        {isP0 ? (
          <span className="alert-auto-note" title="P0 alerts trigger automatic RCA">
            ⚡ Auto-RCA triggered
          </span>
        ) : (
          <button type="button" className="alert-analyze-btn" onClick={onAnalyze}>
            🔍 Analyze
          </button>
        )}
      </div>
    </article>
  )
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
    const h = String(d.getHours()).padStart(2, '0')
    const m = String(d.getMinutes()).padStart(2, '0')
    const s = String(d.getSeconds()).padStart(2, '0')
    return `${h}:${m}:${s}`
  } catch {
    return iso
  }
}
