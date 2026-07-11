import { useEffect, useState, useCallback, useRef } from 'react'
import {
  api,
  type AgentMode,
  type ApprovalBatchItem,
  type KnowledgeContext,
  type ReplayState,
  type RouteCard,
  type StreamEvent,
  type ToolCallApproval,
} from '../lib/api'

export interface ToolCallEntry {
  name: string
  args?: Record<string, unknown>
  result?: string
  streaming?: boolean
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool_call' | 'child_agent'
  content: string
  node?: string        // LangGraph node name (e.g. 'troubleshoot_agent')
  agentRole?: string   // 'child' | 'orchestrator' | 'system'
  approvalId?: string
  approvalStatus?: 'pending' | 'approved' | 'denied'
  streaming?: boolean
  reasoning?: string
  reasoningStreaming?: boolean
  reasoningCollapsed?: boolean
  compacting?: string
  compactingStreaming?: boolean
  compactingCollapsed?: boolean
  knowledgeContext?: KnowledgeContext
  rewrittenQuery?: string  // 改写后的查询文本（来自 QueryRewriter）
  cards?: RouteCard[]  // 路由/改写卡片（query_rewrite / skill_route）
  childCollapsed?: boolean  // child agent card collapse state
  toolCalls?: ToolCallEntry[]  // tool calls inside child agent cards
}

export function useChat(
  threadId: string | null,
  ensureThreadId: () => string,
  replayState?: ReplayState | null,
  agentMode: AgentMode = 'single',
) {
  const [messages, setMessages] = useState<Message[]>([])
  const [pendingApprovals, setPendingApprovals] = useState<ToolCallApproval[]>([])
  const [memoryApprovals, setMemoryApprovals] = useState<ToolCallApproval[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const memoryPollRef = useRef<number | null>(null)

  const nextId = () => String(++idRef.current)

  useEffect(() => {
    if (!replayState) return
    const replayMessages: Message[] = replayState.messages.map((message, index) => ({
      id: `replay-${replayState.checkpoint_id}-${index}`,
      role: message.role,
      content: message.content,
      reasoning: message.reasoning,
      reasoningStreaming: false,
      reasoningCollapsed: message.reasoning ? true : undefined,
      compactingStreaming: false,
    }))
    setMessages(replayMessages)
    const approvals = replayState.values.pending_approvals ?? []
    setPendingApprovals(approvals.filter((approval) => !isMemoryApproval(approval)))
    setMemoryApprovals(approvals.filter(isMemoryApproval))
    setError(null)
    setLoading(false)
    idRef.current = replayMessages.length
  }, [replayState])

  // Load conversation history when threadId changes (e.g. navigating from
  // OTEL alerts "View Analysis" or sidebar thread selection).
  useEffect(() => {
    if (!threadId || replayState) return

    let cancelled = false
    const loadHistory = async () => {
      try {
        console.log('[Chat] Loading history for thread %s', threadId)
        const result = await api.replay(threadId)
        if (cancelled) return

        console.log('[Chat] Replay returned %d states for thread %s',
          result.states.length, threadId)

        // LangGraph's checkpointer.alist() returns checkpoints in
        // REVERSE chronological order (newest first).  Each checkpoint's
        // `channel_values.messages` contains the ACCUMULATED message
        // history at that point — not deltas.  So the newest checkpoint
        // with messages IS the complete conversation.
        //
        // We search forward (newest → oldest) and take the FIRST state
        // that has messages.  This avoids duplicates from flatMapping
        // across multiple accumulated snapshots.
        let bestState = null
        for (const state of result.states) {
          if (state.messages?.length > 0) {
            bestState = state
            break
          }
        }

        if (bestState) {
          const historyMessages: Message[] = bestState.messages.map((msg, i) => ({
            id: `history-${bestState.checkpoint_id}-${i}`,
            role: msg.role,
            content: msg.content,
            reasoning: msg.reasoning,
            reasoningStreaming: false,
            reasoningCollapsed: msg.reasoning ? true : undefined,
            compactingStreaming: false,
          }))

          console.log('[Chat] Loaded %d messages from newest state with messages (checkpoint=%s, %d states total)',
            historyMessages.length, bestState.checkpoint_id, result.states.length)

          setMessages(historyMessages)
          idRef.current = historyMessages.length
        } else if (threadId.startsWith('rca-')) {
          console.warn('[Chat] RCA thread %s has %d states but ALL have 0 messages', threadId, result.states.length)
          setMessages([{
            id: `rca-empty-${threadId}`,
            role: 'assistant' as const,
            content: '⚠️ RCA 分析线程已创建但未产生任何消息结果。可能原因：\n' +
              '- 后端 agent 运行失败（请检查后端日志中的 `Analysis failed` 错误）\n' +
              '- LLM API 不可用\n' +
              '- otel-query skill 未正确配置\n\n' +
              '请回到告警面板点击 **Retry** 重试分析。',
          }])
        }

        if (result.states.length === 0 && threadId.startsWith('rca-')) {
          console.warn('[Chat] RCA thread %s has 0 states — no checkpoints saved (agent likely failed)', threadId)
          setMessages([{
            id: `rca-no-checkpoint-${threadId}`,
            role: 'assistant' as const,
            content: '⚠️ RCA 分析线程 `' + threadId + '` 没有找到任何检查点。\n\n' +
              '这通常意味着后端 agent 运行失败，没有保存任何执行结果。\n' +
              '请检查：\n' +
              '1. 后端日志中是否有 `Analysis failed` 错误\n' +
              '2. LLM API 是否正常运行\n' +
              '3. otel-query skill 是否已安装并配置正确\n\n' +
              '请回到告警面板点击 **Retry** 重试分析。',
          }])
        }

        // Restore pending approvals from the truly-last state
        const lastState = result.states[result.states.length - 1]
        if (lastState?.values?.pending_approvals) {
          const approvals = lastState.values.pending_approvals
          setPendingApprovals(approvals.filter((a) => !isMemoryApproval(a)))
          setMemoryApprovals(approvals.filter(isMemoryApproval))
        }
      } catch (err) {
        // Thread may not exist yet (fresh thread) — that's fine
        const errorMessage = err instanceof Error ? err.message : String(err)
        console.warn('[Chat] Replay failed for thread %s: %s', threadId, errorMessage)
        if (threadId.startsWith('rca-')) {
          setMessages([{
            id: `rca-error-${threadId}`,
            role: 'assistant' as const,
            content: `⚠️ 加载 RCA 分析结果失败: ${errorMessage}\n\n请回到告警面板点击 **Retry** 重试分析。`,
          }])
        }
      }
    }

    loadHistory()
    return () => { cancelled = true }
  }, [threadId, replayState])

  useEffect(() => {
    return () => {
      if (memoryPollRef.current !== null) {
        window.clearTimeout(memoryPollRef.current)
      }
    }
  }, [])

  const refreshMemoryApprovals = useCallback(async (activeThreadId: string) => {
    const approvals = await api.listPendingApprovals(activeThreadId)
    const memoryOnly = approvals.filter(isMemoryApproval)
    setMemoryApprovals(memoryOnly)
    return memoryOnly
  }, [])

  const pollMemoryApprovals = useCallback(
    async (activeThreadId: string, attempts = 5) => {
      if (memoryPollRef.current !== null) {
        window.clearTimeout(memoryPollRef.current)
        memoryPollRef.current = null
      }
      const approvals = await refreshMemoryApprovals(activeThreadId)
      if (approvals.length > 0 || attempts <= 1) return
      memoryPollRef.current = window.setTimeout(() => {
        void pollMemoryApprovals(activeThreadId, attempts - 1)
      }, 1000)
    },
    [refreshMemoryApprovals],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    setLoading(false)
  }, [])

  const processStream = useCallback(
    async (stream: AsyncGenerator<StreamEvent>) => {
      let mainAssistantId = ''       // orchestrator (supervisor/synthesize) message
      let buffer = ''
      let reasoningBuffer = ''
      let compactingBuffer = ''
      let currentChildNode = ''     // currently active child agent node name
      const nodeMessages = new Map<string, string>()  // node → messageId for child agents

      const ensureAssistantMessage = (node?: string, agentRole?: string) => {
        // Child agents get their own card
        if (node && agentRole === 'child' && node !== 'gate') {
          const existing = nodeMessages.get(node)
          if (existing) return existing
          const id = nextId()
          nodeMessages.set(node, id)
          void _childAgentLabel(node)
          setMessages((prev) => [
            ...prev,
            {
              id,
              role: 'child_agent' as const,
              content: '',
              streaming: true,
              node,
              agentRole,
              childCollapsed: false,
            },
          ])
          return id
        }
        // Orchestrator / system nodes share the main assistant message
        if (mainAssistantId) return mainAssistantId
        mainAssistantId = nextId()
        setMessages((prev) => [
          ...prev,
          {
            id: mainAssistantId,
            role: 'assistant' as const,
            content: '',
          },
        ])
        return mainAssistantId
      }

      const finishReasoning = () => {
        if (!mainAssistantId || !reasoningBuffer) return
        setMessages((prev) =>
          prev.map((m) =>
            m.id === mainAssistantId
              ? { ...m, reasoningStreaming: false, reasoningCollapsed: true }
              : m,
          ),
        )
      }

      const finishCompacting = () => {
        if (!mainAssistantId || !compactingBuffer) return
        setMessages((prev) =>
          prev.map((m) =>
            m.id === mainAssistantId
              ? { ...m, compactingStreaming: false, compactingCollapsed: true }
              : m,
          ),
        )
      }

      for await (const event of stream) {
        switch (event.type) {
          case 'node_started': {
            const id = ensureAssistantMessage(event.node, event.agent_role)
            if (event.agent_role === 'child') {
              currentChildNode = event.node  // Track active child agent for tool routing
              // Mark existing child card as active
              setMessages((prev) =>
                prev.map((m) => (m.id === id ? { ...m, streaming: true } : m)),
              )
            }
            break
          }
          case 'node_finished': {
            if (event.agent_role === 'child' && event.node) {
              if (currentChildNode === event.node) currentChildNode = ''
              const id = nodeMessages.get(event.node)
              if (id) {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === id
                      ? { ...m, streaming: false, childCollapsed: true }
                      : m,
                  ),
                )
              }
            }
            break
          }
          case 'tool_started': {
            // Route tool start to the correct child agent card
            // Child agent subgraphs emit tool events with internal node names
            // (e.g. "agent", "tools"), so fall back to currentChildNode
            const toolNode = event.node
            let childMsgId = toolNode ? nodeMessages.get(toolNode) : undefined
            if (!childMsgId && currentChildNode) {
              childMsgId = nodeMessages.get(currentChildNode)
            }
            if (childMsgId) {
              // Append to child agent card's toolCalls
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === childMsgId && m.role === 'child_agent'
                    ? {
                        ...m,
                        toolCalls: [
                          ...(m.toolCalls || []),
                          { name: event.name, args: event.args, streaming: true },
                        ],
                      }
                    : m,
                ),
              )
            } else {
              // Orchestrator tool call — create standalone tool_call message
              const msgId = mainAssistantId
              if (!msgId) break
              setMessages((prev) => [
                ...prev,
                {
                  id: nextId(),
                  role: 'tool_call' as const,
                  content: `🔧 ${event.name}`,
                  approvalStatus: 'approved' as const,
                  node: event.name,
                },
              ])
            }
            break
          }
          case 'compacting': {
            const id = ensureAssistantMessage()
            compactingBuffer += compactingBuffer ? `\n${event.content}` : event.content
            setMessages((prev) =>
              prev.map((m) =>
                m.id === id
                  ? {
                      ...m,
                      compacting: compactingBuffer,
                      compactingStreaming: event.status === 'started',
                      compactingCollapsed: event.status === 'completed',
                    }
                  : m,
              ),
            )
            break
          }
          case 'reasoning': {
            finishCompacting()
            // Reasoning only for orchestrator (child agent reasoning suppressed by backend)
            const id = ensureAssistantMessage()
            reasoningBuffer += event.content
            setMessages((prev) =>
              prev.map((m) =>
                m.id === id
                  ? {
                      ...m,
                      reasoning: (m.reasoning ?? '') + event.content,
                      reasoningStreaming: true,
                      reasoningCollapsed: false,
                    }
                  : m,
              ),
            )
            break
          }
          case 'card': {
            // 路由/改写卡片：挂到主 assistant 气泡，早于正文 token 到达
            const id = ensureAssistantMessage()
            const card: RouteCard = event
            setMessages((prev) =>
              prev.map((m) =>
                m.id === id
                  ? { ...m, cards: [...(m.cards ?? []), card] }
                  : m,
              ),
            )
            break
          }
          case 'token': {
            finishReasoning()
            finishCompacting()
            // Route token to correct card: child agent or main assistant
            const targetId = (event.node && event.agent_role === 'child')
              ? nodeMessages.get(event.node)
              : undefined
            const id = targetId || ensureAssistantMessage(event.node, event.agent_role)
            buffer += event.content

            setMessages((prev) =>
              prev.map((m) =>
                m.id === id
                  ? { ...m, content: m.content + event.content, streaming: true }
                  : m,
              ),
            )
            break
          }
          case 'tool_call_generating': {
            // Tool call argument streaming — route to child agent card
            const targetId = (event.node && event.agent_role === 'child')
              ? nodeMessages.get(event.node)
              : undefined
            const id = targetId || mainAssistantId
            if (!id) break
            // No visible content change for arg streaming, just keep card alive
            setMessages((prev) =>
              prev.map((m) =>
                m.id === id ? { ...m, streaming: true } : m,
              ),
            )
            break
          }
          case 'requires_approval': {
            finishReasoning()
            finishCompacting()
            if (mainAssistantId && buffer) {
              setMessages((prev) =>
                prev.map((m) => (m.id === mainAssistantId ? { ...m, streaming: false } : m)),
              )
            }
            setPendingApprovals(event.approvals.filter((approval) => !isMemoryApproval(approval)))
            setMemoryApprovals(event.approvals.filter(isMemoryApproval))
            return
          }
          case 'tool_result': {
            finishReasoning()
            finishCompacting()
            // Route tool result to the correct child agent card
            // Fall back to currentChildNode when tool event carries internal node name
            const toolNode = event.node
            let childMsgId = toolNode ? nodeMessages.get(toolNode) : undefined
            if (!childMsgId && currentChildNode) {
              childMsgId = nodeMessages.get(currentChildNode)
            }
            if (childMsgId) {
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== childMsgId || m.role !== 'child_agent') return m
                  const calls = [...(m.toolCalls || [])]
                  // Find the last matching tool call without a result
                  for (let i = calls.length - 1; i >= 0; i--) {
                    const call = calls[i]
                    if (call && call.name === event.name && !call.result) {
                      calls[i] = { ...call, result: event.content, streaming: false }
                      break
                    }
                  }
                  return { ...m, toolCalls: calls }
                }),
              )
            } else {
              // Orchestrator tool result — update standalone tool_call message
              setMessages((prev) => {
                const pendingToolIndex = prev.findIndex(
                  (m) =>
                    m.role === 'tool_call' &&
                    m.approvalStatus === 'approved' &&
                    m.content === `🔧 ${event.name}`,
                )
                if (pendingToolIndex === -1) {
                  return [
                    ...prev,
                    {
                      id: nextId(),
                      role: 'tool_call' as const,
                      content: `${event.name}: ${event.content}`,
                      approvalStatus: 'approved' as const,
                    },
                  ]
                }
                return prev.map((m, index) =>
                  index === pendingToolIndex
                    ? { ...m, content: `${event.name}: ${event.content}` }
                    : m,
                )
              })
            }
            break
          }
          case 'done': {
            finishReasoning()
            finishCompacting()
            // Finish main assistant
            if (mainAssistantId && buffer) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === mainAssistantId
                    ? {
                        ...m,
                        streaming: false,
                        knowledgeContext: event.knowledge_context ?? m.knowledgeContext,
                      }
                    : m,
                ),
              )
            } else if (mainAssistantId && event.message) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === mainAssistantId
                    ? {
                        ...m,
                        content: event.message,
                        streaming: false,
                        knowledgeContext: event.knowledge_context ?? m.knowledgeContext,
                      }
                    : m,
                ),
              )
            } else if (!mainAssistantId && event.message) {
              setMessages((prev) => [
                ...prev,
                {
                  id: nextId(),
                  role: 'assistant' as const,
                  content: event.message,
                  knowledgeContext: event.knowledge_context,
                },
              ])
            }
            // Finish all child agent cards
            setMessages((prev) =>
              prev.map((m) =>
                m.role === 'child_agent' && m.streaming
                  ? { ...m, streaming: false, childCollapsed: true }
                  : m,
              ),
            )
            // Attach rewritten query to the last user message
            if (event.rewritten_query) {
              setMessages((prev) => {
                // Find the last user message (most recent, highest index)
                let lastUserIdx = -1
                for (let i = prev.length - 1; i >= 0; i--) {
                  if (prev[i].role === 'user') { lastUserIdx = i; break }
                }
                if (lastUserIdx < 0) return prev
                // Only attach if rewritten differs from original
                const userMsg = prev[lastUserIdx]
                if (event.rewritten_query!.trim() === userMsg.content.trim()) return prev
                return prev.map((m, i) =>
                  i === lastUserIdx ? { ...m, rewrittenQuery: event.rewritten_query } : m,
                )
              })
            }
            setPendingApprovals([])
            return
          }
          case 'error': {
            throw new Error(event.message)
          }
        }
      }
    },
    [],
  )

  const toggleReasoning = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId && m.reasoning
          ? { ...m, reasoningCollapsed: !m.reasoningCollapsed }
          : m,
      ),
    )
  }, [])

  const toggleChild = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId && m.role === 'child_agent'
          ? { ...m, childCollapsed: !m.childCollapsed }
          : m,
      ),
    )
  }, [])

  const toggleCompacting = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId && m.compacting
          ? { ...m, compactingCollapsed: !m.compactingCollapsed }
          : m,
      ),
    )
  }, [])

  const send = useCallback(
    async (text: string) => {
      setError(null)
      setLoading(true)
      const userId = nextId()
      setMessages((prev) => [...prev, { id: userId, role: 'user', content: text }])

      try {
        const activeThreadId = threadId ?? ensureThreadId()
        const stream = api.chatStream({
          thread_id: activeThreadId,
          message: text,
          agent_mode: agentMode,
        })
        await processStream(stream)
        await pollMemoryApprovals(activeThreadId)
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, ensureThreadId, processStream, pollMemoryApprovals, agentMode],
  )

  const approve = useCallback(
    async (approvalId: string) => {
      setError(null)
      setLoading(true)
      setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
      setMemoryApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
      setMessages((prev) =>
        prev.map((m) =>
          m.approvalId === approvalId
            ? { ...m, approvalStatus: 'approved' as const }
            : m,
        ),
      )
      try {
        const activeThreadId = threadId ?? ensureThreadId()
        const stream = api.approveStream({
          thread_id: activeThreadId,
          approval_id: approvalId,
          approved: true,
        })
        await processStream(stream)
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, ensureThreadId, processStream],
  )

  const approveBatch = useCallback(
    async (decisions: ApprovalBatchItem[]) => {
      if (decisions.length === 0) return
      setError(null)
      setLoading(true)
      const approvalIds = new Set(decisions.map((decision) => decision.approval_id))
      setPendingApprovals((prev) =>
        prev.filter((approval) => !approvalIds.has(approval.approval_id)),
      )
      setMemoryApprovals((prev) =>
        prev.filter((approval) => !approvalIds.has(approval.approval_id)),
      )
      setMessages((prev) =>
        prev.map((m) => {
          if (!m.approvalId || !approvalIds.has(m.approvalId)) return m
          const decision = decisions.find((item) => item.approval_id === m.approvalId)
          return {
            ...m,
            approvalStatus: decision?.approved ? 'approved' as const : 'denied' as const,
          }
        }),
      )
      try {
        const activeThreadId = threadId ?? ensureThreadId()
        const stream = api.approveBatchStream({
          thread_id: activeThreadId,
          decisions,
        })
        await processStream(stream)
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, ensureThreadId, processStream],
  )

  const deny = useCallback(
    async (approvalId: string) => {
      setError(null)
      setLoading(true)
      setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
      setMemoryApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
      setMessages((prev) =>
        prev.map((m) =>
          m.approvalId === approvalId
            ? { ...m, approvalStatus: 'denied' as const }
            : m,
        ),
      )
      try {
        const activeThreadId = threadId ?? ensureThreadId()
        const stream = api.approveStream({
          thread_id: activeThreadId,
          approval_id: approvalId,
          approved: false,
        })
        await processStream(stream)
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, ensureThreadId, processStream],
  )

  const dismissApproval = useCallback((approvalId: string) => {
    setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
    setMemoryApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
  }, [])

  const clearError = useCallback(() => {
    setError(null)
  }, [])

  const inputDisabled = loading || pendingApprovals.length > 0

  return {
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
    dismissApproval,
    clearError,
    cancel,
    toggleReasoning,
    toggleCompacting,
    toggleChild,
  }
}

/** Human-readable label for child agent node names */
function _childAgentLabel(node: string): string {
  const labels: Record<string, string> = {
    metrics_agent: '📊 Metrics 分析',
    troubleshoot_agent: '🔍 故障排查',
    patrol_agent: '🛡️ 巡检',
    audit_agent: '📋 审计',
  }
  return labels[node] ?? node
}

function isMemoryApproval(approval: ToolCallApproval) {
  return approval.name === 'save_conversation_memory'
}
