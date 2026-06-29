import { useEffect, useState } from 'react'
import { api, type AuditEvent, type ReplayState, type SkillInfo, type ReplayResponse } from '../lib/api'

interface Props {
  threadId: string | null
  onThreadCleared?: () => void
  onReplayState?: (state: ReplayState) => void
}

type Tab = 'skills' | 'history' | 'audit'

export function Sidebar({ threadId, onThreadCleared, onReplayState }: Props) {
  const [tab, setTab] = useState<Tab>('skills')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [skillsLoading, setSkillsLoading] = useState(true)
  const [replay, setReplay] = useState<ReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)
  const [historyDeleting, setHistoryDeleting] = useState(false)
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([])
  const [auditLoading, setAuditLoading] = useState(false)

  useEffect(() => {
    loadSkills()
  }, [])

  useEffect(() => {
    if (tab === 'history') {
      loadReplay()
    }
    if (tab === 'audit') {
      loadAuditEvents()
    }
  }, [tab, threadId])

  const loadSkills = async () => {
    setSkillsLoading(true)
    try {
      setSkills(await api.listSkills())
    } catch {
      // silently handle
    }
    setSkillsLoading(false)
  }

  const handleReload = async () => {
    setSkillsLoading(true)
    try {
      setSkills(await api.reloadSkills())
    } catch {
      // silently handle
    }
    setSkillsLoading(false)
  }

  const loadReplay = async () => {
    if (!threadId) {
      setReplay(null)
      return
    }
    setReplayLoading(true)
    try {
      setReplay(await api.replay(threadId))
    } catch {
      // silently handle
    }
    setReplayLoading(false)
  }

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

  const loadAuditEvents = async () => {
    setAuditLoading(true)
    try {
      setAuditEvents(await api.listAuditEvents(threadId ?? undefined))
    } catch {
      setAuditEvents([])
    }
    setAuditLoading(false)
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-tabs">
        <button
          role="tab"
          aria-selected={tab === 'skills'}
          className={`tab ${tab === 'skills' ? 'active' : ''}`}
          onClick={() => setTab('skills')}
        >
          Skills
        </button>
        <button
          role="tab"
          aria-selected={tab === 'history'}
          className={`tab ${tab === 'history' ? 'active' : ''}`}
          onClick={() => setTab('history')}
        >
          History
        </button>
        <button
          role="tab"
          aria-selected={tab === 'audit'}
          className={`tab ${tab === 'audit' ? 'active' : ''}`}
          onClick={() => setTab('audit')}
        >
          Audit
        </button>
      </div>

      <div className="sidebar-content">
        {tab === 'skills' && (
          <div className="skills-panel">
            <div className="skills-header">
              <h3>Skills</h3>
              <button onClick={handleReload} disabled={skillsLoading}>
                Reload
              </button>
            </div>
            {skillsLoading && <div className="loading">Loading...</div>}
            {!skillsLoading && skills.length === 0 && (
              <div className="empty-state">No skills loaded.</div>
            )}
            <ul className="skills-list">
              {skills.map((skill) => (
                <li key={skill.name} className="skill-item">
                  <div className="skill-name">{skill.name}</div>
                  <div className="skill-desc">{skill.description}</div>
                  <div className="skill-tools">
                    Tools: {skill.tool_names.join(', ')}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {tab === 'history' && (
          <div className="replay-panel">
            <div className="replay-header">
              <h3>Thread Replay</h3>
              <div className="replay-actions">
                <button onClick={deleteCurrentHistory} disabled={historyDeleting}>
                  Delete History
                </button>
                <button onClick={clearAndStartNewThread} disabled={historyDeleting}>
                  Clear and New Thread
                </button>
              </div>
            </div>
            {replayLoading && <div className="loading">Loading...</div>}
            {!replayLoading && (!replay || replay.states.length === 0) && (
              <div className="empty-state">No history for this thread.</div>
            )}
            {replay && replay.states.length > 0 && (
              <div className="replay-states">
                {replay.states.map((state, i) => (
                  <details key={i} className="replay-state">
                    <summary>
                      <span>
                        Checkpoint {i + 1}
                        {state.node ? ` · ${state.node}` : ''}
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
                    <div className="replay-meta">
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

        {tab === 'audit' && (
          <div className="audit-panel">
            <div className="audit-header">
              <h3>Security Audit</h3>
              <button onClick={loadAuditEvents} disabled={auditLoading}>
                Refresh
              </button>
            </div>
            {auditLoading && <div className="loading">Loading...</div>}
            {!auditLoading && auditEvents.length === 0 && (
              <div className="empty-state">No audit events for this thread.</div>
            )}
            {!auditLoading && auditEvents.length > 0 && (
              <ul className="audit-list">
                {auditEvents.map((event) => (
                  <li key={event.id} className={`audit-item severity-${event.severity.toLowerCase()}`}>
                    <div className="audit-row">
                      <span className="audit-category">{event.category}</span>
                      <span className="audit-severity">{event.severity}</span>
                    </div>
                    <div className="audit-reason">{event.reason}</div>
                    <div className="audit-meta">
                      <span>{event.source}</span>
                      {event.created_at && <span>{new Date(event.created_at).toLocaleString()}</span>}
                    </div>
                    {event.subject && <div className="audit-subject">{event.subject}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
