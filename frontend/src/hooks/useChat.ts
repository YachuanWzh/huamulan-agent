import { useEffect, useState, useCallback, useRef } from 'react'
import { api, type ReplayState, type StreamEvent, type ToolCallApproval } from '../lib/api'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool_call'
  content: string
  approvalId?: string
  approvalStatus?: 'pending' | 'approved' | 'denied'
  streaming?: boolean
}

export function useChat(
  threadId: string | null,
  ensureThreadId: () => string,
  replayState?: ReplayState | null,
) {
  const [messages, setMessages] = useState<Message[]>([])
  const [pendingApprovals, setPendingApprovals] = useState<ToolCallApproval[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  const nextId = () => String(++idRef.current)

  useEffect(() => {
    if (!replayState) return
    const replayMessages: Message[] = replayState.messages.map((message, index) => ({
      id: `replay-${replayState.checkpoint_id}-${index}`,
      role: message.role,
      content: message.content,
    }))
    setMessages(replayMessages)
    setPendingApprovals(replayState.values.pending_approvals ?? [])
    setError(null)
    setLoading(false)
    idRef.current = replayMessages.length
  }, [replayState?.checkpoint_id])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    setLoading(false)
  }, [])

  const processStream = useCallback(
    async (stream: AsyncGenerator<StreamEvent>) => {
      let assistantId = ''
      let buffer = ''

      for await (const event of stream) {
        switch (event.type) {
          case 'token': {
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
            if (assistantId && buffer) {
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
              )
            }
            setPendingApprovals(event.approvals)
            const toolMessages: Message[] = event.approvals.map((a) => ({
              id: nextId(),
              role: 'tool_call' as const,
              content: a.name,
              approvalId: a.approval_id,
              approvalStatus: 'pending' as const,
            }))
            setMessages((prev) => [...prev, ...toolMessages])
            return
          }
          case 'done': {
            if (assistantId && buffer) {
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)),
              )
            } else if (!assistantId && event.message) {
              setMessages((prev) => [
                ...prev,
                { id: nextId(), role: 'assistant' as const, content: event.message },
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

  const send = useCallback(
    async (text: string) => {
      setError(null)
      setLoading(true)
      const userId = nextId()
      setMessages((prev) => [...prev, { id: userId, role: 'user', content: text }])

      try {
        const activeThreadId = threadId ?? ensureThreadId()
        const stream = api.chatStream({ thread_id: activeThreadId, message: text })
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

  const approve = useCallback(
    async (approvalId: string) => {
      setError(null)
      setLoading(true)
      setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
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

  const deny = useCallback(
    async (approvalId: string) => {
      setError(null)
      setLoading(true)
      setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
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
  }, [])

  const clearError = useCallback(() => {
    setError(null)
  }, [])

  return {
    messages,
    pendingApprovals,
    loading,
    error,
    send,
    approve,
    deny,
    dismissApproval,
    clearError,
    cancel,
  }
}
