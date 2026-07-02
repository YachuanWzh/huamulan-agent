import { useState } from 'react'
import { ChatPanel } from './components/ChatPanel'
import { Sidebar } from './components/Sidebar'
import { WorkspacePanel } from './components/WorkspacePanel'
import type { ReplayState } from './lib/api'
import './App.css'

function createThreadId() {
  const id = crypto.randomUUID()
  localStorage.setItem('threadId', id)
  return id
}

function App() {
  const [threadId, setThreadId] = useState<string | null>(null)
  const [conversationKey, setConversationKey] = useState('empty-thread')
  const [replayState, setReplayState] = useState<ReplayState | null>(null)
  const [activePanel, setActivePanel] = useState<'chat' | 'skills' | 'checkpoint' | 'audit'>('chat')

  const handleThreadCreated = () => {
    const id = createThreadId()
    setThreadId(id)
    setReplayState(null)
    setActivePanel('chat')
    return id
  }

  const handleNewConversation = () => {
    const id = handleThreadCreated()
    setConversationKey(id)
  }

  const handleThreadCleared = () => {
    handleNewConversation()
  }

  const handleThreadSelected = (id: string) => {
    localStorage.setItem('threadId', id)
    setThreadId(id)
    setConversationKey(id)
    setActivePanel('chat')
  }

  return (
    <div className="app">
      <header className="app-header" aria-label="木兰控制台">
        <div className="brand-lockup">
          <h1>huamulan-agent</h1>
          <div className="kit-rail" aria-label="四市备装栏">
            <span>东市 骏马</span>
            <span>西市 鞍鞯</span>
            <span>南市 辔头</span>
            <span>北市 长鞭</span>
          </div>
        </div>
        <div className="header-actions">
          <div className="thread-info" title={threadId ?? ''}>
            军令: {threadId ? `${threadId.slice(0, 8)}...` : '未出征'}
          </div>
        </div>
      </header>
      <main className="app-body" aria-label="对话案台">
        {activePanel === 'chat' ? (
          <ChatPanel
            key={conversationKey}
            threadId={threadId}
            onThreadCreated={handleThreadCreated}
            onNewConversation={handleNewConversation}
            replayState={replayState}
          />
        ) : (
          <WorkspacePanel
            panel={activePanel}
            threadId={threadId}
            onThreadCleared={handleThreadCleared}
            onReplayState={(state) => {
              setReplayState(state)
              setActivePanel('chat')
            }}
          />
        )}
        <Sidebar
          threadId={threadId}
          onThreadCleared={handleThreadCleared}
          onThreadSelected={handleThreadSelected}
          onReplayState={setReplayState}
          onPanelChange={setActivePanel}
        />
      </main>
    </div>
  )
}

export default App
