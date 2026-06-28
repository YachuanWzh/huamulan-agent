import { useChat } from '../hooks/useChat'
import { MessageList } from './MessageList'
import { MessageInput } from './MessageInput'
import { ToolApprovalCard } from './ToolApprovalCard'

interface Props {
  threadId: string
}

export function ChatPanel({ threadId }: Props) {
  const {
    messages,
    pendingApprovals,
    loading,
    error,
    send,
    approve,
    deny,
    clearError,
  } = useChat(threadId)

  return (
    <div className="chat-panel">
      <MessageList messages={messages} loading={loading} />
      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button className="error-dismiss" onClick={clearError}>✕</button>
        </div>
      )}
      {pendingApprovals.map((approval) => (
        <ToolApprovalCard
          key={approval.approval_id}
          approval={approval}
          onApprove={approve}
          onDeny={deny}
        />
      ))}
      <MessageInput onSend={send} disabled={loading} />
    </div>
  )
}
