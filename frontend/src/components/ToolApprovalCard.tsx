import type { ToolCallApproval } from '../lib/api'

interface Props {
  approval: ToolCallApproval
  onApprove: (approvalId: string) => void
  onDeny: (approvalId: string) => void
  variant?: 'inline' | 'memory'
}

export function ToolApprovalCard({
  approval,
  onApprove,
  onDeny,
  variant = 'inline',
}: Props) {
  const argsDisplay =
    Object.keys(approval.args).length === 0
      ? '{}'
      : JSON.stringify(approval.args, null, 2)

  return (
    <div
      className={`approval-card ${variant === 'memory' ? 'memory-approval-card' : ''}`}
      data-testid="approval-card"
    >
      <div className="approval-header">
        <span className="approval-icon">{variant === 'memory' ? 'M' : '!'}</span>
        <span className="approval-title">
          {variant === 'memory' ? 'Save to Memory?' : 'Tool Approval Required'}
        </span>
      </div>
      <div className="approval-body">
        <div className="tool-name">{approval.name}</div>
        <pre className="tool-args">{argsDisplay}</pre>
      </div>
      <div className="approval-actions">
        <button
          className="btn-approve"
          onClick={() => onApprove(approval.approval_id)}
        >
          Approve
        </button>
        <button
          className="btn-deny"
          onClick={() => onDeny(approval.approval_id)}
        >
          Deny
        </button>
      </div>
    </div>
  )
}
