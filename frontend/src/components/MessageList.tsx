import { useEffect, useRef } from 'react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../hooks/useChat'

interface Props {
  messages: Message[]
  loading?: boolean
}

export function MessageList({ messages, loading }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="message-list">
      {messages.length === 0 && (
        <div className="empty-state">Send a message to start the conversation.</div>
      )}
      {messages.map((msg) => (
        <MessageBubble
          key={msg.id}
          role={msg.role}
          content={msg.content}
          approvalStatus={msg.approvalStatus}
        />
      ))}
      {loading && (
        <div className="loading-indicator" data-testid="loading-indicator">
          Thinking…
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
