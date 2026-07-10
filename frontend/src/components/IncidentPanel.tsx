import { useCallback, useEffect, useState } from 'react'
import { api, type Incident, type IncidentStatus } from '../lib/api'

const statuses: IncidentStatus[] = ['open', 'investigating', 'mitigated', 'closed']

export function IncidentPanel() {
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionDrafts, setActionDrafts] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setIncidents(await api.listIncidents())
      setError(null)
    } catch {
      setError('无法加载事故列表。')
    }
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load])

  const replace = (next: Incident) => setIncidents((current) =>
    current.map((item) => item.id === next.id ? next : item),
  )
  const changeStatus = async (id: string, status: IncidentStatus) => {
    try { replace(await api.updateIncident(id, { status })) } catch { setError('无法更新事故状态。') }
  }
  const addAction = async (id: string) => {
    const description = actionDrafts[id]?.trim()
    if (!description) return
    try {
      replace(await api.addIncidentAction(id, description))
      setActionDrafts((drafts) => ({ ...drafts, [id]: '' }))
    } catch { setError('无法添加恢复动作。') }
  }
  const completeAction = async (incidentId: string, actionId: string, completed: boolean) => {
    try { replace(await api.updateIncidentAction(incidentId, actionId, completed)) } catch { setError('无法更新恢复动作。') }
  }

  return (
    <section className="workspace-section incident-command" aria-labelledby="incident-heading">
      <header className="command-header">
        <div>
          <span className="command-kicker">INCIDENT COMMAND</span>
          <h2 id="incident-heading">事件指挥台</h2>
          <p>跟踪响应状态、服务影响与恢复动作。</p>
        </div>
        <button className="command-button quiet" onClick={() => void load()} disabled={loading}>
          {loading ? '刷新中…' : '刷新事件'}
        </button>
      </header>

      {error && <p role="alert" className="workspace-error">{error}</p>}
      {!loading && !error && incidents.length === 0 && (
        <div className="incident-empty" data-testid="incident-command-empty">
          <span>—</span><strong>当前没有活跃事件</strong><p>P0 / P1 告警会自动出现在这里。</p>
        </div>
      )}
      <div className="incident-list" data-testid="incident-command-list">
        {incidents.map((incident) => (
          <article className="incident-card" key={incident.id}>
            <div className="incident-card-heading">
              <span className={`incident-severity severity-${incident.severity.toLowerCase()}`}>{incident.severity}</span>
              <div>
                <h3>{incident.title}</h3>
                <span className="incident-service">{incident.service}</span>
              </div>
              <label className="incident-status">
                <span>状态</span>
                <select
                  aria-label={`Status for ${incident.title}`}
                  value={incident.status}
                  onChange={(event) => void changeStatus(incident.id, event.target.value as IncidentStatus)}
                >
                  {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
                </select>
              </label>
            </div>

            <div className="incident-actions">
              <strong>恢复动作</strong>
              {incident.actions.length === 0 ? <span className="incident-no-actions">尚未添加动作</span> : incident.actions.map((action) => (
                <label className="incident-action" key={action.id}>
                  <input type="checkbox" checked={action.completed} onChange={(event) => void completeAction(incident.id, action.id, event.target.checked)} aria-label={`Complete ${action.description}`} />
                  <span>{action.description}</span>
                </label>
              ))}
            </div>

            <div className="incident-action-entry">
              <input
                aria-label={`New action for ${incident.title}`}
                value={actionDrafts[incident.id] ?? ''}
                onChange={(event) => setActionDrafts((drafts) => ({ ...drafts, [incident.id]: event.target.value }))}
                onKeyDown={(event) => { if (event.key === 'Enter') void addAction(incident.id) }}
                placeholder="添加恢复动作…"
              />
              <button aria-label="Add action" onClick={() => void addAction(incident.id)}>添加动作</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
