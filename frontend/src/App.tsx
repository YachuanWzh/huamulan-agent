import { useState } from 'react'
import { ChatPanel } from './components/ChatPanel'
import { Sidebar } from './components/Sidebar'
import './App.css'

function App() {
  const [threadId] = useState(() => {
    const stored = localStorage.getItem('threadId')
    if (stored) return stored
    const id = crypto.randomUUID()
    localStorage.setItem('threadId', id)
    return id
  })

  return (
    <div className="app">
      <header className="app-header">
        <h1>🤖 LangGraph Assistant</h1>
        <div className="thread-info" title={threadId}>
          Thread: {threadId.slice(0, 8)}…
        </div>
      </header>
      <main className="app-body">
        <ChatPanel threadId={threadId} />
        <Sidebar threadId={threadId} />
      </main>
    </div>
  )
}

export default App
