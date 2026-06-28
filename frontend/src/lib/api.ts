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

export interface ReplayResponse {
  thread_id: string
  states: Record<string, unknown>[]
}

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

export const api = {
  health: () => request<{ status: string }>('/api/health'),

  chat: (body: ChatRequest) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  approve: (body: ApprovalDecision) =>
    request<ChatResponse>('/api/approve', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  replay: (threadId: string) =>
    request<ReplayResponse>(`/api/threads/${threadId}/replay`),

  listSkills: () => request<SkillInfo[]>('/api/skills'),

  reloadSkills: () =>
    request<SkillInfo[]>('/api/skills/reload', { method: 'POST' }),
}
