import { useEffect, useState, useCallback, useRef } from 'react'
import {
  api,
  type AgentMode,
  type ApprovalBatchItem,
  type KnowledgeContext,
  type ReplayState,
  type StreamEvent,
  type ToolCallApproval,
} from '../lib/api'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool_call'
  content: string
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
        const result = await api.replay(threadId)
        if (cancelled) return

        // Each LangGraph checkpoint contains the full accumulated message
        // history, so we only take messages from the latest state to avoid
        // triplicating them across __start__ → agent → __end__ states.
        const lastState = result.states[result.states.length - 1]
        if (lastState) {
          const historyMessages: Message[] = lastState.messages.map((msg, i) => ({
            id: `history-${lastState.checkpoint_id}-${i}`,
            role: msg.role,
            content: msg.content,
            reasoning: msg.reasoning,
            reasoningStreaming: false,
            reasoningCollapsed: msg.reasoning ? true : undefined,
            compactingStreaming: false,
          }))

          if (historyMessages.length > 0) {
            setMessages(historyMessages)
            idRef.current = historyMessages.length
          }
        }

        // Restore pending approvals if any
        if (lastState?.values?.pending_approvals) {
          const approvals = lastState.values.pending_approvals
          setPendingApprovals(approvals.filter((a) => !isMemoryApproval(a)))
          setMemoryApprovals(approvals.filter(isMemoryApproval))
        }
      } catch {
        // Thread may not exist yet (fresh thread) — that's fine
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
      let assistantId = ''
      let buffer = ''
      let reasoningBuffer = ''
      let compactingBuffer = ''

      const ensureAssistantMessage = () => {
        if (assistantId) return assistantId
        assistantId = nextId()
        setMessages((prev) => [
          ...prev,
          {
            id: assistantId,
            role: 'assistant' as const,
            content: '',
          },
        ])
        return assistantId
      }

      const finishReasoning = () => {
        if (!assistantId || !reasoningBuffer) return
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, reasoningStreaming: false, reasoningCollapsed: true }
              : m,
          ),
        )
      }

      const finishCompacting = () => {
        if (!assistantId || !compactingBuffer) return
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, compactingStreaming: false, compactingCollapsed: true }
              : m,
          ),
        )
      }

      for await (const event of stream) {
        switch (event.type) {
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
          case 'token': {
            finishReasoning()
            finishCompacting()
            buffer += event.content
            if (!assistantId) {
              assistantId = nextId()
              setMessages((prev) => [
                ...prev,
                {
                  id: assistantId,
                  role: 'assistant' as const,
                  content: event.content,
                  streaming: true,
                },
              ])
            } else {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: m.content + event.content }
                    : m,
                ),
              )
            }
            break
          }
          case 'requires_approval': {
            finishReasoning()
            finishCompacting()
            if (assistantId && buffer) {
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
              )
            }
            setPendingApprovals(event.approvals.filter((approval) => !isMemoryApproval(approval)))
            setMemoryApprovals(event.approvals.filter(isMemoryApproval))
            return
          }
          case 'tool_result': {
            finishReasoning()
            finishCompacting()
            setMessages((prev) => {
              const pendingToolIndex = prev.findIndex(
                (m) =>
                  m.role === 'tool_call' &&
                  m.approvalStatus === 'approved' &&
                  m.content === event.name,
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
            break
          }
          case 'done': {
            finishReasoning()
            finishCompacting()
            if (assistantId && buffer) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        streaming: false,
                        knowledgeContext: event.knowledge_context ?? m.knowledgeContext,
                      }
                    : m,
                ),
              )
            } else if (assistantId && event.message) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: event.message,
                        streaming: false,
                        knowledgeContext: event.knowledge_context ?? m.knowledgeContext,
                      }
                    : m,
                ),
              )
            } else if (!assistantId && event.message) {
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
  }
}

function isMemoryApproval(approval: ToolCallApproval) {
  return approval.name === 'save_conversation_memory'
}
