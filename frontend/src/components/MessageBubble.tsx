import type { Message } from '../hooks/useChat'

interface Props {
  id?: string
  role: Message['role']
  content: string
  approvalStatus?: Message['approvalStatus']
  streaming?: boolean
  reasoning?: string
  reasoningStreaming?: boolean
  reasoningCollapsed?: boolean
  onToggleReasoning?: (messageId: string) => void
}

const roleLabels: Record<Message['role'], string> = {
  user: 'You',
  assistant: 'Assistant',
  tool_call: 'Tool Call',
}

export function MessageBubble({
  id = '',
  role,
  content,
  approvalStatus,
  streaming,
  reasoning,
  reasoningStreaming,
  reasoningCollapsed,
  onToggleReasoning,
}: Props) {
  return (
    <div className={`message-bubble ${role}`} data-testid="message-bubble">
      <div className="message-header">
        <span className="role-label">{roleLabels[role]}</span>
        {role === 'tool_call' && approvalStatus && (
          <span className={`badge badge-${approvalStatus}`}>
            {approvalStatus === 'pending' && 'Pending'}
            {approvalStatus === 'approved' && 'Approved'}
            {approvalStatus === 'denied' && 'Denied'}
          </span>
        )}
        {streaming && <span className="streaming-badge">typing...</span>}
      </div>
      {role === 'assistant' && reasoning && (
        <div className={`reasoning-card ${reasoningCollapsed ? 'collapsed' : ''}`}>
          <button
            type="button"
            className="reasoning-header"
            onClick={() => onToggleReasoning?.(id)}
            aria-expanded={!reasoningCollapsed}
          >
            <span>{reasoningStreaming ? 'Thinking' : 'Thinking complete'}</span>
            <span className="reasoning-toggle">
              {reasoningCollapsed ? 'Show' : 'Hide'}
            </span>
          </button>
          {!reasoningCollapsed && (
            <div className="reasoning-content">{reasoning}</div>
          )}
        </div>
      )}
      <div className="message-content">
        {content}
        {streaming && <span className="typewriter-cursor" data-testid="typewriter-cursor" />}
      </div>
    </div>
  )
}
