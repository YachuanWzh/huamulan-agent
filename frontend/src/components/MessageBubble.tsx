import { useState } from 'react'
import type { Message } from '../hooks/useChat'
import { MarkdownRenderer } from './MarkdownRenderer'

interface Props {
  id?: string
  role: Message['role']
  content: string
  approvalStatus?: Message['approvalStatus']
  streaming?: boolean
  reasoning?: string
  reasoningStreaming?: boolean
  reasoningCollapsed?: boolean
  compacting?: string
  compactingStreaming?: boolean
  compactingCollapsed?: boolean
  onToggleReasoning?: (messageId: string) => void
  onToggleCompacting?: (messageId: string) => void
}

const roleLabels: Record<Message['role'], string> = {
  user: '你',
  assistant: '木兰',
  tool_call: '工具调用',
}

const toolResultPreview = (content: string) => {
  const firstLine = content.trim().split(/\r?\n/, 1)[0] ?? ''
  return firstLine.length > 96 ? `${firstLine.slice(0, 96)}...` : firstLine
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
  compacting,
  compactingStreaming,
  compactingCollapsed,
  onToggleReasoning,
  onToggleCompacting,
}: Props) {
  const [toolResultExpanded, setToolResultExpanded] = useState(false)
  const isToolResult = role === 'tool_call'
  const toolResultId = id ? `${id}-tool-result` : undefined

  return (
    <div className={`message-bubble ${role}`} data-testid="message-bubble">
      <div className="message-header">
        <span className="role-label">{roleLabels[role]}</span>
        {role === 'tool_call' && approvalStatus && (
          <span className={`badge badge-${approvalStatus}`}>
            {approvalStatus === 'pending' && '待审批'}
            {approvalStatus === 'approved' && '已批准'}
            {approvalStatus === 'denied' && '已拒绝'}
          </span>
        )}
        {streaming && <span className="streaming-badge">生成中...</span>}
      </div>
      {role === 'assistant' && reasoning && (
        <div className={`reasoning-card ${reasoningCollapsed ? 'collapsed' : ''}`}>
          <button
            type="button"
            className="reasoning-header"
            onClick={() => onToggleReasoning?.(id)}
            aria-expanded={!reasoningCollapsed}
          >
            <span>{reasoningStreaming ? '思考中' : '已完成'}</span>
            <span className="reasoning-toggle">
              {reasoningCollapsed ? '展开' : '收起'}
            </span>
          </button>
          {!reasoningCollapsed && (
            <div className="reasoning-content">{reasoning}</div>
          )}
        </div>
      )}
      {role === 'assistant' && compacting && (
        <div className={`reasoning-card compacting-card ${compactingCollapsed ? 'collapsed' : ''}`}>
          <button
            type="button"
            className="reasoning-header"
            onClick={() => onToggleCompacting?.(id)}
            aria-expanded={!compactingCollapsed}
          >
            <span>{compactingStreaming ? '压缩上下文中' : '上下文已压缩'}</span>
            <span className="reasoning-toggle">
              {compactingCollapsed ? '展开' : '收起'}
            </span>
          </button>
          {!compactingCollapsed && (
            <div className="reasoning-content">{compacting}</div>
          )}
        </div>
      )}
      {isToolResult ? (
        <div className="tool-result">
          <button
            type="button"
            className="tool-result-header"
            onClick={() => setToolResultExpanded((expanded) => !expanded)}
            aria-expanded={toolResultExpanded}
            aria-controls={toolResultId}
          >
            <span className="tool-result-title">tool_result</span>
            <span className="tool-result-preview">{toolResultPreview(content)}</span>
            <span className="tool-result-toggle">
              {toolResultExpanded ? '收起' : '展开'}
            </span>
          </button>
          {toolResultExpanded && (
            <div
              id={toolResultId}
              className="tool-result-content"
              role="region"
              aria-label="tool_result"
            >
              {content}
            </div>
          )}
          {streaming && <span className="typewriter-cursor" data-testid="typewriter-cursor" />}
        </div>
      ) : role === 'assistant' ? (
        <MarkdownRenderer content={content} streaming={streaming} />
      ) : (
        <div className="message-content">
          {content}
          {streaming && <span className="typewriter-cursor" data-testid="typewriter-cursor" />}
        </div>
      )}
    </div>
  )
}
