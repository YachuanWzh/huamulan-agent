// --- Types matching backend schemas ---

export interface LLMConfig {
  base_url?: string
  model?: string
  api_key?: string
  temperature?: number
}

export interface ChatRequest {
  thread_id: string
  message: string
  llm?: LLMConfig
}

export interface ToolCallApproval {
  approval_id: string
  tool_call_id: string
  name: string
  args: Record<string, unknown>
}

export interface ChatResponse {
  thread_id: string
  status: 'completed' | 'requires_approval'
  message?: string | null
  approvals: ToolCallApproval[]
}

export interface ApprovalDecision {
  thread_id: string
  approval_id: string
  approved: boolean
}

export interface SkillInfo {
  name: string
  description: string
  tool_names: string[]
  path: string
}

export interface ReplayMessage {
  role: 'user' | 'assistant' | 'tool_call'
  content: string
  reasoning?: string
}

export interface ReplayState {
  checkpoint_id: string
  parent_checkpoint_id: string | null
  created_at: string | null
  node: string | null
  values: {
    selected_skills?: string[]
    pending_approvals?: ToolCallApproval[]
  }
  messages: ReplayMessage[]
  checkpoint: Record<string, unknown>
}

export interface ReplayResponse {
  thread_id: string
  states: ReplayState[]
}

export interface ThreadSummary {
  thread_id: string
  updated_at: string | null
}

export interface DeleteThreadResponse {
  thread_id: string
  deleted: boolean
}

export interface ClearThreadsResponse {
  thread_ids: string[]
  deleted: number
}

export interface AuditEvent {
  id: number
  created_at: string
  thread_id: string | null
  source: 'prompt' | 'tool'
  category: string
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  reason: string
  subject: string | null
  metadata: Record<string, unknown>
}

export interface ToolError {
  id: number
  created_at: string
  thread_id: string | null
  tool_call_id: string
  tool_name: string
  tool_args: Record<string, unknown>
  attempt: number
  max_attempts: number
  error_type: string
  error_message: string
  will_retry: boolean
}

export interface ExecutionLog {
  id: number
  created_at: string
  thread_id: string
  run_id?: string | null
  parent_id?: string | null
  event_type: 'turn' | 'skill_route' | 'llm' | 'tool' | 'tool_retry' | 'approval' | 'security'
  status: 'started' | 'completed' | 'failed' | 'blocked' | 'retrying' | 'approved' | 'denied'
  name: string | null
  input: Record<string, unknown>
  output: Record<string, unknown>
  error: Record<string, unknown>
  duration_ms: number | null
  token_usage: Record<string, unknown>
  metadata: Record<string, unknown>
}

export interface ExecutionSummary {
  thread_id: string
  total_events: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  tool_calls: number
  tool_errors: number
  tool_retries: number
  security_events: number
  total_duration_ms: number
}

// --- SSE Streaming types ---

export interface StreamToken {
  type: 'token'
  content: string
}

export interface StreamReasoning {
  type: 'reasoning'
  content: string
}

export interface StreamCompacting {
  type: 'compacting'
  status: 'started' | 'completed'
  content: string
}

export interface StreamRequiresApproval {
  type: 'requires_approval'
  approvals: ToolCallApproval[]
}

export interface StreamToolResult {
  type: 'tool_result'
  name: string
  content: string
}

export interface StreamDone {
  type: 'done'
  status: 'completed'
  message: string
}

export interface StreamError {
  type: 'error'
  message: string
}

export type StreamEvent =
  | StreamToken
  | StreamReasoning
  | StreamCompacting
  | StreamRequiresApproval
  | StreamToolResult
  | StreamDone
  | StreamError

// --- API client ---

let _baseUrl = ''
export function setBaseUrl(url: string) {
  _baseUrl = url
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${_baseUrl}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

/**
 * Read an SSE stream from the given endpoint, yielding parsed StreamEvent objects.
 *
 * Parses standard SSE format:
 *   event: <type>
 *   data: <json-payload>
 *   <blank line>
 *
 * Merges the event type into the data payload so consumers see { type, ...data }.
 */
async function* streamRequest(
  url: string,
  body: unknown,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${_baseUrl}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API error ${res.status}: ${text}`)
  }
  if (!res.body) {
    throw new Error('No response body for streaming')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') return
          try {
            const payload = JSON.parse(raw) as Record<string, unknown>
            yield { type: eventType, ...payload } as StreamEvent
          } catch {
            // Skip malformed JSON lines
          }
        }
        // blank line resets event type (SSE end-of-event marker)
        if (line === '') {
          eventType = ''
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export const api = {
  health: () => request<{ status: string }>('/api/health'),

  chat: (body: ChatRequest) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  chatStream: (body: ChatRequest) =>
    streamRequest('/api/chat/stream', body),

  approve: (body: ApprovalDecision) =>
    request<ChatResponse>('/api/approve', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  approveStream: (body: ApprovalDecision) =>
    streamRequest('/api/approve/stream', body),

  replay: (threadId: string) =>
    request<ReplayResponse>(`/api/threads/${threadId}/replay`),

  listThreads: () =>
    request<ThreadSummary[]>('/api/threads?limit=100'),

  deleteThread: (threadId: string) =>
    request<DeleteThreadResponse>(`/api/threads/${threadId}`, { method: 'DELETE' }),

  clearThreads: () =>
    request<ClearThreadsResponse>('/api/threads', { method: 'DELETE' }),

  listAuditEvents: (threadId?: string) => {
    const params = new URLSearchParams({ limit: '100' })
    if (threadId) params.set('thread_id', threadId)
    return request<AuditEvent[]>(`/api/audit-events?${params.toString()}`)
  },

  listToolErrors: (threadId?: string) => {
    const params = new URLSearchParams({ limit: '100' })
    if (threadId) params.set('thread_id', threadId)
    return request<ToolError[]>(`/api/tool-errors?${params.toString()}`)
  },

  listExecutionLogs: (threadId: string) =>
    request<ExecutionLog[]>(
      `/api/threads/${threadId}/execution-logs?limit=500`,
    ),

  getExecutionSummary: (threadId: string) =>
    request<ExecutionSummary>(
      `/api/threads/${threadId}/execution-summary`,
    ),

  listSkills: () => request<SkillInfo[]>('/api/skills'),

  reloadSkills: () =>
    request<SkillInfo[]>('/api/skills/reload', { method: 'POST' }),
}
