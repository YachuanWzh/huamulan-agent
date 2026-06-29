import { useEffect, useRef } from 'react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../hooks/useChat'

interface Props {
  messages: Message[]
  loading?: boolean
  onToggleReasoning?: (messageId: string) => void
}

export function MessageList({ messages, loading, onToggleReasoning }: Props) {
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
          id={msg.id}
          role={msg.role}
          content={msg.content}
          approvalStatus={msg.approvalStatus}
          streaming={msg.streaming}
          reasoning={msg.reasoning}
          reasoningStreaming={msg.reasoningStreaming}
          reasoningCollapsed={msg.reasoningCollapsed}
          onToggleReasoning={onToggleReasoning}
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
