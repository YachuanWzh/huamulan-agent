import { useState, useCallback, useRef } from 'react'
import { api, type ChatResponse, type ToolCallApproval } from '../lib/api'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool_call'
  content: string
  approvalStatus?: 'pending' | 'approved' | 'denied'
}

export function useChat(threadId: string) {
  const [messages, setMessages] = useState<Message[]>([])
  const [pendingApprovals, setPendingApprovals] = useState<ToolCallApproval[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idRef = useRef(0)

  const nextId = () => String(++idRef.current)

  const handleResponse = useCallback((response: ChatResponse) => {
    if (response.status === 'completed') {
      setMessages((prev) => [
        ...prev,
        { id: String(Date.now()), role: 'assistant' as const, content: response.message ?? '' },
      ])
      setPendingApprovals([])
    } else {
      setPendingApprovals(response.approvals)
      const toolMessages: Message[] = response.approvals.map((approval) => ({
        id: String(Date.now() + Math.random()),
        role: 'tool_call' as const,
        content: approval.name,
        approvalStatus: 'pending' as const,
      }))
      setMessages((prev) => [...prev, ...toolMessages])
    }
  }, [])

  const send = useCallback(
    async (text: string) => {
      setError(null)
      setLoading(true)
      setMessages((prev) => [...prev, { id: nextId(), role: 'user', content: text }])
      try {
        const response = await api.chat({ thread_id: threadId, message: text })
        handleResponse(response)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, handleResponse],
  )

  const approve = useCallback(
    async (approvalId: string) => {
      setError(null)
      setLoading(true)
      setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
      try {
        const response = await api.approve({
          thread_id: threadId,
          approval_id: approvalId,
          approved: true,
        })
        handleResponse(response)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, handleResponse],
  )

  const deny = useCallback(
    async (approvalId: string) => {
      setError(null)
      setLoading(true)
      setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
      setMessages((prev) =>
        prev.map((m) =>
          m.role === 'tool_call' && m.approvalStatus === 'pending'
            ? { ...m, approvalStatus: 'denied' as const }
            : m,
        ),
      )
      try {
        const response = await api.approve({
          thread_id: threadId,
          approval_id: approvalId,
          approved: false,
        })
        handleResponse(response)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error')
      } finally {
        setLoading(false)
      }
    },
    [threadId, handleResponse],
  )

  const dismissApproval = useCallback((approvalId: string) => {
    setPendingApprovals((prev) => prev.filter((a) => a.approval_id !== approvalId))
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
  }
}
