import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ReplayState,
  type SkillInfo,
  type ThreadSummary,
} from '../lib/api'

interface Props {
  threadId: string | null
  onThreadCleared?: () => void
  onThreadSelected?: (threadId: string) => void
  onReplayState?: (state: ReplayState) => void
  onPanelChange?: (panel: 'chat' | 'checkpoint' | 'audit') => void
}

type Tab = 'skills' | 'history' | 'checkpoint' | 'audit'

export function Sidebar({
  threadId,
  onThreadCleared,
  onThreadSelected,
  onReplayState,
  onPanelChange,
}: Props) {
  const [tab, setTab] = useState<Tab>('skills')
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [skillsLoading, setSkillsLoading] = useState(true)
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [threadsLoading, setThreadsLoading] = useState(false)
  const [openingThreadId, setOpeningThreadId] = useState<string | null>(null)
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null)
  const [clearingHistory, setClearingHistory] = useState(false)

  const loadSkills = useCallback(async () => {
    setSkillsLoading(true)
    try {
      setSkills(await api.listSkills())
    } catch {
      // silently handle
    }
    setSkillsLoading(false)
  }, [])

  const handleReload = async () => {
    setSkillsLoading(true)
    try {
      setSkills(await api.reloadSkills())
    } catch {
      // silently handle
    }
    setSkillsLoading(false)
  }

  const loadThreads = useCallback(async () => {
    setThreadsLoading(true)
    try {
      setThreads(await api.listThreads())
    } catch {
      setThreads([])
    }
    setThreadsLoading(false)
  }, [])

  useEffect(() => {
    loadSkills()
  }, [loadSkills])

  useEffect(() => {
    if (tab === 'history') {
      loadThreads()
    }
  }, [loadThreads, tab])

  const openThread = async (selectedThreadId: string) => {
    setOpeningThreadId(selectedThreadId)
    try {
      const selectedReplay = await api.replay(selectedThreadId)
      onThreadSelected?.(selectedThreadId)
      const latestState = selectedReplay.states.find((state) => state.messages.length > 0)
      if (latestState) {
        onReplayState?.({
          ...latestState,
          values: {
            ...latestState.values,
            pending_approvals: [],
          },
        })
      }
    } catch {
      // silently handle
    }
    setOpeningThreadId(null)
  }

  const deleteHistoryThread = async (selectedThreadId: string) => {
    setDeletingThreadId(selectedThreadId)
    try {
      await api.deleteThread(selectedThreadId)
      setThreads((prev) =>
        prev.filter((thread) => thread.thread_id !== selectedThreadId),
      )
      if (selectedThreadId === threadId) {
        onThreadCleared?.()
      }
    } catch {
      // silently handle
    }
    setDeletingThreadId(null)
  }

  const clearHistory = async () => {
    setClearingHistory(true)
    try {
      await api.clearThreads()
      setThreads([])
      onThreadCleared?.()
    } catch {
      // silently handle
    }
    setClearingHistory(false)
  }

  const selectTab = (nextTab: Tab, panel: 'chat' | 'checkpoint' | 'audit') => {
    setTab(nextTab)
    onPanelChange?.(panel)
  }

  return (
    <aside className="sidebar" data-testid="sidebar-shell" aria-label="案台面板">
      <div className="sidebar-tabs">
        <button
          role="tab"
          aria-selected={tab === 'skills'}
          className={`tab ${tab === 'skills' ? 'active' : ''}`}
          onClick={() => selectTab('skills', 'chat')}
        >
          军械
        </button>
        <button
          role="tab"
          aria-selected={tab === 'history'}
          className={`tab ${tab === 'history' ? 'active' : ''}`}
          onClick={() => selectTab('history', 'chat')}
        >
          军报
        </button>
        <button
          role="tab"
          aria-selected={tab === 'checkpoint'}
          className={`tab ${tab === 'checkpoint' ? 'active' : ''}`}
          onClick={() => selectTab('checkpoint', 'checkpoint')}
        >
          驿站
        </button>
        <button
          role="tab"
          aria-selected={tab === 'audit'}
          className={`tab ${tab === 'audit' ? 'active' : ''}`}
          onClick={() => selectTab('audit', 'audit')}
        >
          校阅
        </button>
      </div>

      <div className="sidebar-content">
        {tab === 'skills' && (
          <div className="skills-panel">
            <div className="skills-header">
              <h3>军械</h3>
              <button onClick={handleReload} disabled={skillsLoading}>
                整备
              </button>
            </div>
            {skillsLoading && <div className="loading">加载中...</div>}
            {!skillsLoading && skills.length === 0 && (
              <div className="empty-state">暂无军械。</div>
            )}
            <ul className="skills-list">
              {skills.map((skill) => (
                <li key={skill.name} className="skill-item">
                  <div className="skill-name">{skill.name}</div>
                  <div className="skill-desc">{skill.description}</div>
                  <div className="skill-tools">
                    器具: {skill.tool_names.join(', ')}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {tab === 'history' && (
          <div className="history-panel">
            <div className="history-header">
              <h3>军报</h3>
              <button
                type="button"
                onClick={clearHistory}
                disabled={clearingHistory || threads.length === 0}
              >
                清空军报
              </button>
            </div>
            {threadsLoading && <div className="loading">加载中...</div>}
            {!threadsLoading && threads.length === 0 && (
              <div className="empty-state">暂无军报。</div>
            )}
            {!threadsLoading && threads.length > 0 && (
              <ol className="history-list">
                {threads.map((thread) => (
                  <li key={thread.thread_id}>
                    <div className="history-row">
                      <button
                        type="button"
                        className="history-message"
                        aria-label={`Open session ${thread.thread_id}`}
                        disabled={openingThreadId === thread.thread_id}
                        onClick={() => openThread(thread.thread_id)}
                      >
                        <span className="history-role">军令</span>
                        <span className="history-preview">
                          <span className="history-summary">
                            {thread.summary || '未命名军报'}
                          </span>
                          <span className="history-thread-id">{thread.thread_id}</span>
                          {thread.updated_at && (
                            <span className="history-time">
                              {new Date(thread.updated_at).toLocaleString()}
                            </span>
                          )}
                        </span>
                      </button>
                      <button
                        type="button"
                        className="history-delete"
                        aria-label={`Delete session ${thread.thread_id}`}
                        disabled={deletingThreadId === thread.thread_id}
                        onClick={() => deleteHistoryThread(thread.thread_id)}
                      >
                        删除
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}

        {tab === 'checkpoint' && (
          <div className="sidebar-panel-note">
            <div className="sidebar-panel-note-header">
              <h3>驿站回放</h3>
            </div>
            <div className="sidebar-panel-note-body">在主案台查看</div>
          </div>
        )}

        {tab === 'audit' && (
          <div className="sidebar-panel-note">
            <div className="sidebar-panel-note-header">
              <h3>行军校阅</h3>
            </div>
            <div className="sidebar-panel-note-body">在主案台查看</div>
          </div>
        )}
      </div>
    </aside>
  )
}
