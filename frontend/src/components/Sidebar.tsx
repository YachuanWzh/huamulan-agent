import { useEffect, useState } from 'react'
import { api, type SkillInfo, type ReplayResponse } from '../lib/api'

interface Props {
  threadId: string
}

type Tab = 'skills' | 'history'

export function Sidebar({ threadId }: Props) {
  const [tab, setTab] = useState<Tab>('skills')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [skillsLoading, setSkillsLoading] = useState(true)
  const [replay, setReplay] = useState<ReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)

  useEffect(() => {
    loadSkills()
  }, [])

  useEffect(() => {
    if (tab === 'history') {
      loadReplay()
    }
  }, [tab])

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
    setReplayLoading(true)
    try {
      setReplay(await api.replay(threadId))
    } catch {
      // silently handle
    }
    setReplayLoading(false)
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-tabs">
        <button
          role="tab"
          className={`tab ${tab === 'skills' ? 'active' : ''}`}
          onClick={() => setTab('skills')}
        >
          Skills
        </button>
        <button
          role="tab"
          className={`tab ${tab === 'history' ? 'active' : ''}`}
          onClick={() => setTab('history')}
        >
          History
        </button>
      </div>

      <div className="sidebar-content">
        {tab === 'skills' && (
          <div className="skills-panel">
            <div className="skills-header">
              <h3>Skills</h3>
              <button onClick={handleReload} disabled={skillsLoading}>
                ↻ Reload
              </button>
            </div>
            {skillsLoading && <div className="loading">Loading…</div>}
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
            <h3>Thread Replay</h3>
            {replayLoading && <div className="loading">Loading…</div>}
            {!replayLoading && !replay && (
              <div className="empty-state">No history for this thread.</div>
            )}
            {replay && (
              <div className="replay-states">
                {replay.states.map((state, i) => (
                  <details key={i} className="replay-state">
                    <summary>State {i + 1}</summary>
                    <pre>{JSON.stringify(state, null, 2)}</pre>
                  </details>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
