import { useMemo, useState } from 'react'
import type { ApprovalBatchItem, ToolCallApproval } from '../lib/api'

interface Props {
  approvals: ToolCallApproval[]
  onSubmit: (decisions: ApprovalBatchItem[]) => void
}

export function ToolApprovalBatchCard({ approvals, onSubmit }: Props) {
  const [decisions, setDecisions] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(approvals.map((approval) => [approval.approval_id, true])),
  )
  const title = `需要审批 ${approvals.length} 个工具调用`
  const submitDecisions = useMemo(
    () =>
      approvals.map((approval) => ({
        approval_id: approval.approval_id,
        approved: decisions[approval.approval_id] ?? true,
      })),
    [approvals, decisions],
  )

  const setAll = (approved: boolean) => {
    setDecisions(
      Object.fromEntries(approvals.map((approval) => [approval.approval_id, approved])),
    )
  }

  const setOne = (approvalId: string, approved: boolean) => {
    setDecisions((current) => ({ ...current, [approvalId]: approved }))
  }

  return (
    <div className="approval-card approval-batch-card" data-testid="approval-batch-card">
      <div className="approval-header">
        <span className="approval-icon">!</span>
        <span className="approval-title">{title}</span>
      </div>
      <div className="approval-batch-toolbar">
        <button className="btn-approve" onClick={() => setAll(true)}>
          全部批准
        </button>
        <button className="btn-deny" onClick={() => setAll(false)}>
          全部拒绝
        </button>
      </div>
      <div className="approval-batch-list">
        {approvals.map((approval) => {
          const approved = decisions[approval.approval_id] ?? true
          const argsDisplay =
            Object.keys(approval.args).length === 0
              ? '{}'
              : JSON.stringify(approval.args, null, 2)
          return (
            <div className="approval-batch-item" key={approval.approval_id}>
              <div className="approval-batch-item-header">
                <span className="tool-name">{approval.name}</span>
                <span className={`approval-decision ${approved ? 'approved' : 'denied'}`}>
                  {approved ? '已批准' : '已拒绝'}
                </span>
              </div>
              <pre className="tool-args">{argsDisplay}</pre>
              <div className="approval-actions">
                <button
                  className="btn-approve"
                  onClick={() => setOne(approval.approval_id, true)}
                  aria-label={`批准 ${approval.name}`}
                >
                  批准
                </button>
                <button
                  className="btn-deny"
                  onClick={() => setOne(approval.approval_id, false)}
                  aria-label={`拒绝 ${approval.name}`}
                >
                  拒绝
                </button>
              </div>
            </div>
          )
        })}
      </div>
      <div className="approval-actions approval-batch-submit">
        <button className="btn-approve" onClick={() => onSubmit(submitDecisions)}>
          提交审批
        </button>
      </div>
    </div>
  )
}
