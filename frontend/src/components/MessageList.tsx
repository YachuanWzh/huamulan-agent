import { useEffect, useRef } from 'react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../hooks/useChat'

interface Props {
  messages: Message[]
  loading?: boolean
  onToggleReasoning?: (messageId: string) => void
  onToggleCompacting?: (messageId: string) => void
  onToggleChild?: (messageId: string) => void
}

export function MessageList({ messages, loading, onToggleReasoning, onToggleCompacting, onToggleChild }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const lastMessage = messages[messages.length - 1]
  const showLoadingIndicator = loading && lastMessage?.role !== 'assistant'
  const scrollKey = [
    messages.length,
    lastMessage?.id ?? '',
    lastMessage?.content ?? '',
    lastMessage?.reasoning ?? '',
    lastMessage?.compacting ?? '',
    lastMessage?.streaming ? 'streaming' : '',
    lastMessage?.reasoningStreaming ? 'reasoning' : '',
    lastMessage?.compactingStreaming ? 'compacting' : '',
    showLoadingIndicator ? 'loading' : '',
  ].join('|')

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [scrollKey])

  return (
    <div className="message-list">
      {messages.length === 0 && (
        <div className="empty-state">整备任务，准备出征。</div>
      )}
      {messages.map((msg) => (
        <MessageBubble
          key={msg.id}
          id={msg.id}
          role={msg.role}
          content={msg.content}
          node={msg.node}
          agentRole={msg.agentRole}
          approvalStatus={msg.approvalStatus}
          streaming={msg.streaming}
          reasoning={msg.reasoning}
          reasoningStreaming={msg.reasoningStreaming}
          reasoningCollapsed={msg.reasoningCollapsed}
          compacting={msg.compacting}
          compactingStreaming={msg.compactingStreaming}
          compactingCollapsed={msg.compactingCollapsed}
          childCollapsed={msg.childCollapsed}
          knowledgeContext={msg.knowledgeContext}
          onToggleReasoning={onToggleReasoning}
          onToggleCompacting={onToggleCompacting}
          onToggleChild={onToggleChild}
        />
      ))}
      {showLoadingIndicator && (
        <div className="loading-indicator" data-testid="loading-indicator">
          思考中...
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
