import type { Message } from '../hooks/useChat'

interface Props {
  role: Message['role']
  content: string
  approvalStatus?: Message['approvalStatus']
}

const roleLabels: Record<string, string> = {
  user: 'You',
  assistant: 'Assistant',
  tool_call: 'Tool Call',
}

export function MessageBubble({ role, content, approvalStatus }: Props) {
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
      </div>
      <div className="message-content">{content}</div>
    </div>
  )
}
