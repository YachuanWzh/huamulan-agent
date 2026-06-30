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
  const [activePanel, setActivePanel] = useState<'chat' | 'checkpoint' | 'audit'>('chat')

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
      <header className="app-header" aria-label="Assistant console">
        <h1>LangGraph Assistant</h1>
        <div className="header-actions">
          {/* <button
            type="button"
            className="header-audit-button"
            aria-label="Open audit workspace"
            onClick={() => setActivePanel('audit')}
          >
            Audit
          </button> */}
          <div className="thread-info" title={threadId ?? ''}>
            Thread: {threadId ? `${threadId.slice(0, 8)}...` : 'not started'}
          </div>
        </div>
      </header>
      <main className="app-body" aria-label="Conversation workspace">
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
