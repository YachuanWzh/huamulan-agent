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
  const lastMessage = messages[messages.length - 1]
  const showLoadingIndicator = loading && lastMessage?.role !== 'assistant'
  const scrollKey = [
    messages.length,
    lastMessage?.id ?? '',
    lastMessage?.content ?? '',
    lastMessage?.reasoning ?? '',
    lastMessage?.streaming ? 'streaming' : '',
    lastMessage?.reasoningStreaming ? 'reasoning' : '',
    showLoadingIndicator ? 'loading' : '',
  ].join('|')

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [scrollKey])

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
      {showLoadingIndicator && (
        <div className="loading-indicator" data-testid="loading-indicator">
          Thinking…
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
