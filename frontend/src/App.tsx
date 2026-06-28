import { useState } from 'react'
import { ChatPanel } from './components/ChatPanel'
import { Sidebar } from './components/Sidebar'
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

  const handleThreadCreated = () => {
    const id = createThreadId()
    setThreadId(id)
    return id
  }

  const handleThreadCleared = () => {
    handleThreadCreated()
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>LangGraph Assistant</h1>
        <div className="thread-info" title={threadId ?? ''}>
          Thread: {threadId ? `${threadId.slice(0, 8)}...` : 'not started'}
        </div>
      </header>
      <main className="app-body">
        <ChatPanel
          key={threadId ?? 'empty-thread'}
          threadId={threadId}
          onThreadCreated={handleThreadCreated}
          onNewConversation={handleThreadCreated}
        />
        <Sidebar threadId={threadId} onThreadCleared={handleThreadCleared} />
      </main>
    </div>
  )
}

export default App
