import { useState } from 'react'
import { ChatPanel } from './components/ChatPanel'
import { Sidebar } from './components/Sidebar'
import type { ReplayState } from './lib/api'
import './App.css'

function createThreadId() {
  const id = crypto.randomUUID()
  localStorage.setItem('threadId', id)
  return id
}

function App() {
  const [threadId, setThreadId] = useState<string | null>(() => {
    const stored = localStorage.getItem('threadId')
    if (stored) return stored
    return null
  })
  const [replayState, setReplayState] = useState<ReplayState | null>(null)

  const handleThreadCreated = () => {
    const id = createThreadId()
    setThreadId(id)
    setReplayState(null)
    return id
  }

  const handleThreadCleared = () => {
    handleThreadCreated()
  }

  const handleThreadSelected = (id: string) => {
    localStorage.setItem('threadId', id)
    setThreadId(id)
  }

  return (
    <div className="app">
      <header className="app-header" aria-label="Assistant console">
        <h1>LangGraph Assistant</h1>
        <div className="thread-info" title={threadId ?? ''}>
          Thread: {threadId ? `${threadId.slice(0, 8)}...` : 'not started'}
        </div>
      </header>
      <main className="app-body" aria-label="Conversation workspace">
        <ChatPanel
          key={threadId ?? 'empty-thread'}
          threadId={threadId}
          onThreadCreated={handleThreadCreated}
          onNewConversation={handleThreadCreated}
          replayState={replayState}
        />
        <Sidebar
          threadId={threadId}
          onThreadCleared={handleThreadCleared}
          onThreadSelected={handleThreadSelected}
          onReplayState={setReplayState}
        />
      </main>
    </div>
  )
}

export default App
