import { useState } from 'react'
import type { Message, ToolCallEntry } from '../hooks/useChat'
import type { KnowledgeContext } from '../lib/api'
import { MarkdownRenderer } from './MarkdownRenderer'

interface Props {
  id?: string
  role: Message['role']
  content: string
  node?: string
  agentRole?: string
  approvalStatus?: Message['approvalStatus']
  streaming?: boolean
  reasoning?: string
  reasoningStreaming?: boolean
  reasoningCollapsed?: boolean
  compacting?: string
  compactingStreaming?: boolean
  compactingCollapsed?: boolean
  childCollapsed?: boolean
  knowledgeContext?: KnowledgeContext
  toolCalls?: ToolCallEntry[]
  onToggleReasoning?: (messageId: string) => void
  onToggleCompacting?: (messageId: string) => void
  onToggleChild?: (messageId: string) => void
}

const roleLabels: Record<Message['role'], string> = {
  user: '你',
  assistant: '木兰',
  tool_call: '工具调用',
  child_agent: '子 Agent',
}

const childAgentIcons: Record<string, string> = {
  metrics_agent: '📊',
  troubleshoot_agent: '🔍',
  patrol_agent: '🛡️',
  audit_agent: '📋',
}

const childAgentNames: Record<string, string> = {
  metrics_agent: 'Metrics 分析',
  troubleshoot_agent: '故障排查',
  patrol_agent: '巡检',
  audit_agent: '审计',
}

/** Try to parse child agent JSON report into sections */
function parseChildReport(content: string): {
  findings: string[]
  evidence: string[]
  recommendations: string[]
  confidence: number | null
  error: string | null
  status: string | null
} | null {
  try {
    const json = JSON.parse(content.trim())
    if (typeof json !== 'object' || !json) return null
    return {
      findings: Array.isArray(json.findings) ? json.findings : [],
      evidence: Array.isArray(json.evidence) ? json.evidence : [],
      recommendations: Array.isArray(json.recommendations) ? json.recommendations : [],
      confidence: typeof json.confidence === 'number' ? json.confidence : null,
      error: typeof json.error === 'string' ? json.error : null,
      status: typeof json.status === 'string' ? json.status : null,
    }
  } catch {
    return null
  }
}

const toolResultPreview = (content: string) => {
  const firstLine = content.trim().split(/\r?\n/, 1)[0] ?? ''
  return firstLine.length > 96 ? `${firstLine.slice(0, 96)}...` : firstLine
}

export function MessageBubble({
  id = '',
  role,
  content,
  node,
  agentRole,
  approvalStatus,
  streaming,
  reasoning,
  reasoningStreaming,
  reasoningCollapsed,
  compacting,
  compactingStreaming,
  compactingCollapsed,
  childCollapsed,
  knowledgeContext,
  toolCalls,
  onToggleReasoning,
  onToggleCompacting,
  onToggleChild,
}: Props) {
  const [toolResultExpanded, setToolResultExpanded] = useState(false)
  const [knowledgeExpanded, setKnowledgeExpanded] = useState(false)
  const isToolResult = role === 'tool_call'
  const isChildAgent = role === 'child_agent'
  const toolResultId = id ? `${id}-tool-result` : undefined
  const childIcon = node ? (childAgentIcons[node] ?? '🤖') : '🤖'
  const childName = node ? (childAgentNames[node] ?? roleLabels.child_agent) : roleLabels.child_agent
  const childReport = isChildAgent ? parseChildReport(content) : null

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
        <>
          {knowledgeContext && knowledgeContext.documents.length > 0 && (
            <div className={`knowledge-sources ${knowledgeExpanded ? 'expanded' : 'collapsed'}`}>
              <button
                type="button"
                className="knowledge-sources-header"
                onClick={() => setKnowledgeExpanded((v) => !v)}
                aria-expanded={knowledgeExpanded}
              >
                <span>参考知识文档（{knowledgeContext.documents.length} 篇）</span>
                <span className="knowledge-toggle">
                  {knowledgeExpanded ? '收起' : '展开'}
                </span>
              </button>
              {knowledgeExpanded && (
                <div className="knowledge-sources-list">
                  {knowledgeContext.documents.map((doc, i) => (
                    <div key={i} className="knowledge-source-item">
                      <div className="knowledge-source-attribution">
                        {doc.source_attribution}
                      </div>
                      <div className="knowledge-source-meta">
                        <span className="knowledge-source-score">
                          相似度：{(doc.score * 100).toFixed(1)}%
                        </span>
                        <span className="knowledge-source-chapter">
                          章节：{doc.title}
                        </span>
                      </div>
                      <div className="knowledge-source-preview">
                        {doc.content_preview}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <MarkdownRenderer content={content} streaming={streaming} />
        </>
      ) : isChildAgent ? (
        <div className={`child-agent-card ${childCollapsed ? 'collapsed' : ''} ${streaming ? 'streaming' : ''}`} data-agent={node}>
          <button
            type="button"
            className="child-agent-header"
            onClick={() => onToggleChild?.(id)}
            aria-expanded={!childCollapsed}
          >
            <span className="child-agent-identity">
              <span className="child-agent-icon">{childIcon}</span>
              <span className="child-agent-name">{childName}</span>
            </span>
            <span className={`child-agent-status${childReport?.status === 'failed' ? ' is-failed' : ''}`}>
              <span className="child-agent-status-dot" />
              {streaming ? '运行中' : childReport?.status === 'failed' ? '失败' : childReport?.status === 'completed' ? '完成' : ''}
            </span>
            <span className="child-agent-toggle">{childCollapsed ? '展开' : '收起'}</span>
          </button>
          {!childCollapsed && (
            <div className="child-agent-body">
              {/* Tool calls inside child agent card */}
              {toolCalls && toolCalls.length > 0 && (
                <div className="child-agent-tool-calls">
                  {toolCalls.map((tc, i) => (
                    <details
                      key={i}
                      className="child-agent-tool-call"
                      open={tc.streaming && !tc.result}
                    >
                      <summary className="child-agent-tool-call-summary">
                        <span className="child-agent-tool-call-name">🔧 {tc.name}</span>
                        {tc.streaming && !tc.result && (
                          <span className="child-agent-tool-call-running">执行中...</span>
                        )}
                        {tc.result && (
                          <span className="child-agent-tool-call-done">✓</span>
                        )}
                      </summary>
                      {tc.result && (
                        <pre className="child-agent-tool-call-result">{tc.result}</pre>
                      )}
                    </details>
                  ))}
                </div>
              )}
              {childReport ? (
                <>
                  {childReport.error && (
                    <div className="child-agent-error">{childReport.error}</div>
                  )}
                  {childReport.findings.length > 0 && (
                    <div className="child-agent-section">
                      <h4>发现</h4>
                      <ul>
                        {childReport.findings.map((f, i) => <li key={i}>{f}</li>)}
                      </ul>
                    </div>
                  )}
                  {childReport.evidence.length > 0 && (
                    <div className="child-agent-section">
                      <h4>证据</h4>
                      <ul>
                        {childReport.evidence.map((e, i) => <li key={i}>{e}</li>)}
                      </ul>
                    </div>
                  )}
                  {childReport.recommendations.length > 0 && (
                    <div className="child-agent-section">
                      <h4>建议</h4>
                      <ul>
                        {childReport.recommendations.map((r, i) => <li key={i}>{r}</li>)}
                      </ul>
                    </div>
                  )}
                  {childReport.confidence !== null && (
                    <div className="child-agent-confidence">
                      <span>置信度 {(childReport.confidence * 100).toFixed(0)}%</span>
                      <div className="child-agent-confidence-bar">
                        <div
                          className="child-agent-confidence-fill"
                          style={{ width: `${Math.round(childReport.confidence * 100)}%` }}
                        />
                      </div>
                    </div>
                  )}
                  {streaming && <span className="typewriter-cursor" data-testid="typewriter-cursor" />}
                </>
              ) : streaming ? (
                <div className="child-agent-streaming">{content || '…'}</div>
              ) : (
                <div className="child-agent-raw">{content || '(无输出)'}</div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="message-content">
          {content}
          {streaming && <span className="typewriter-cursor" data-testid="typewriter-cursor" />}
        </div>
      )}
    </div>
  )
}
