import { useChat } from '../hooks/useChat'
import { MessageList } from './MessageList'
import { MessageInput } from './MessageInput'
import { ToolApprovalCard } from './ToolApprovalCard'
import { ToolApprovalBatchCard } from './ToolApprovalBatchCard'
import type { ReplayState } from '../lib/api'

interface Props {
  threadId: string | null
  onThreadCreated: () => string
  onNewConversation: () => void
  replayState?: ReplayState | null
}

export function ChatPanel({
  threadId,
  onThreadCreated,
  onNewConversation,
  replayState,
}: Props) {
  const {
    messages,
    pendingApprovals,
    memoryApprovals,
    loading,
    inputDisabled,
    error,
    send,
    approve,
    approveBatch,
    deny,
    clearError,
    toggleReasoning,
    toggleCompacting,
  } = useChat(threadId, onThreadCreated, replayState)

  return (
    <div className="chat-panel">
      <MessageList
        messages={messages}
        loading={loading}
        onToggleReasoning={toggleReasoning}
        onToggleCompacting={toggleCompacting}
      />
      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button className="error-dismiss" onClick={clearError}>✕</button>
        </div>
      )}
      {memoryApprovals.length > 0 && (
        <div className="memory-approval-stack" aria-live="polite">
          {memoryApprovals.map((approval) => (
            <ToolApprovalCard
              key={approval.approval_id}
              approval={approval}
              onApprove={approve}
              onDeny={deny}
              variant="memory"
            />
          ))}
        </div>
      )}
      {pendingApprovals.length === 1 && (
        <ToolApprovalCard
          approval={pendingApprovals[0]!}
          onApprove={approve}
          onDeny={deny}
        />
      )}
      {pendingApprovals.length > 1 && (
        <ToolApprovalBatchCard approvals={pendingApprovals} onSubmit={approveBatch} />
      )}
      <MessageInput
        onSend={send}
        onNewConversation={onNewConversation}
        disabled={inputDisabled}
      />
    </div>
  )
}
