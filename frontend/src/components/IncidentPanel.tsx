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
      setError('Could not load incidents.')
    }
    setLoading(false)
  }, [])

  useEffect(() => { void load() }, [load])

  const replace = (next: Incident) => setIncidents((current) => current.map((item) => item.id === next.id ? next : item))
  const changeStatus = async (id: string, status: IncidentStatus) => {
    try { replace(await api.updateIncident(id, { status })) } catch { setError('Could not update incident.') }
  }
  const addAction = async (id: string) => {
    const description = actionDrafts[id]?.trim()
    if (!description) return
    try {
      replace(await api.addIncidentAction(id, description))
      setActionDrafts((drafts) => ({ ...drafts, [id]: '' }))
    } catch { setError('Could not add action.') }
  }
  const completeAction = async (incidentId: string, actionId: string, completed: boolean) => {
    try { replace(await api.updateIncidentAction(incidentId, actionId, completed)) } catch { setError('Could not update action.') }
  }

  return <section className="workspace-section governance-panel incident-command" aria-labelledby="incident-heading">
    <header className="workspace-header command-header"><div><span className="command-kicker">事故处置</span><h2 id="incident-heading">事件指挥台</h2><p>追踪响应状态、责任归属与恢复动作。</p></div><button className="command-button quiet" onClick={() => void load()} disabled={loading}>{loading ? '载入中' : '刷新'}</button></header>
    {error && <p role="alert" className="workspace-empty">{error}</p>}
    {!loading && !error && incidents.length === 0 && <div className="incident-empty" data-testid="incident-command-empty"><span>⌁</span><strong>当前战况平稳</strong><p>等待监控告警进入；P0 / P1 事件会自动出现在这里。</p></div>}
    <div className="governance-list" data-testid="incident-command-list">
      {incidents.map((incident) => <article className="governance-card" key={incident.id}>
        <div className="governance-card-title"><span className="governance-severity">{incident.severity}</span><h3>{incident.title}</h3><span>{incident.service}</span></div>
        <label>Status for {incident.title}<select value={incident.status} onChange={(event) => void changeStatus(incident.id, event.target.value as IncidentStatus)}>{statuses.map((status) => <option key={status} value={status}>{status}</option>)}</select></label>
        <div className="incident-actions"><strong>Actions</strong>{incident.actions.map((action) => <label key={action.id}><input type="checkbox" checked={action.completed} onChange={(event) => void completeAction(incident.id, action.id, event.target.checked)} aria-label={`Complete ${action.description}`} />{action.description}</label>)}</div>
        <div className="incident-action-entry"><label>New action for {incident.title}<input value={actionDrafts[incident.id] ?? ''} onChange={(event) => setActionDrafts((drafts) => ({ ...drafts, [incident.id]: event.target.value }))} /></label><button onClick={() => void addAction(incident.id)}>Add action</button></div>
      </article>)}
    </div>
  </section>
}
