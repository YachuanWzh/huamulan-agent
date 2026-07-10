import { useMemo } from 'react'
import type { ToolCallApproval } from '../lib/api'
import { TruncatedText } from './LazyContent'

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
  const argsDisplay = useMemo(
    () =>
      Object.keys(approval.args).length === 0
        ? '{}'
        : JSON.stringify(approval.args, null, 2),
    [approval.args],
  )

  return (
    <div
      className={`approval-card ${variant === 'memory' ? 'memory-approval-card' : ''}`}
      data-testid="approval-card"
    >
      <div className="approval-header">
        <span className="approval-icon">{variant === 'memory' ? 'M' : '!'}</span>
        <span className="approval-title">
          {variant === 'memory' ? '保存到记忆？' : '需要工具审批'}
        </span>
      </div>
      <div className="approval-body">
        <div className="tool-name">{approval.name}</div>
        <pre className="tool-args">
          <TruncatedText text={argsDisplay} downloadName={`${approval.name}-args.json`} />
        </pre>
      </div>
      <div className="approval-actions">
        <button
          className="btn-approve"
          onClick={() => onApprove(approval.approval_id)}
        >
          批准
        </button>
        <button
          className="btn-deny"
          onClick={() => onDeny(approval.approval_id)}
        >
          拒绝
        </button>
      </div>
    </div>
  )
}
