// --- Types matching backend schemas ---

export interface LLMConfig {
  base_url?: string
  model?: string
  api_key?: string
  temperature?: number
}

export type AgentMode = 'single' | 'multi'

export interface ChatRequest {
  thread_id: string
  message: string
  llm?: LLMConfig
  agent_mode?: AgentMode
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

export interface ApprovalBatchItem {
  approval_id: string
  approved: boolean
}

export interface ApprovalBatchDecision {
  thread_id: string
  decisions: ApprovalBatchItem[]
}

export interface SkillInfo {
  name: string
  description: string
  tool_names: string[]
  path: string
  loaded?: boolean
  evaluation?: SkillEvaluationSummary | null
  latest_evaluation?: SkillEvaluationSnapshot | null
}

export interface SkillEvaluationSummary {
  overall_score: number
  description_tokens: number
  skill_md_lines: number
  python_lines: number
  max_cyclomatic_complexity: number
  tool_count: number
}

export interface SkillEvaluationSnapshot {
  id: number
  created_at: string
  skill_name: string
  overall_score: number
  routing_score?: number | null
  runtime_score?: number | null
  usage_score?: number | null
  static_score?: number | null
  source?: string | null
  report: Record<string, unknown>
}

export interface SkillEvaluationRunRequest {
  golden_path?: string | null
  evaluation_mode?: 'quick' | 'e2e'
  agent_mode?: AgentMode
}

export interface SkillEvaluationDataset {
  name: string
  path: string
  label: string
}

export interface SkillEvaluationRunResponse {
  source: string
  results: SkillEvaluationSnapshot[]
}

export interface SkillEvaluationResetResponse {
  deleted: number
  results: SkillEvaluationSnapshot[]
}

export interface SkillEvaluationStaticMetrics {
  skill_name: string
  description_tokens: number
  skill_md_lines: number
  python_lines: number
  max_cyclomatic_complexity: number
  tool_count: number
}

export interface SkillEvaluationResult {
  skill_name: string
  overall_score: number
  static: SkillEvaluationStaticMetrics
  runtime?: Record<string, unknown> | null
  score_components: Record<string, number>
}

export interface EvaluationCheck {
  name: string
  stage: string
  passed: boolean
  expected?: unknown
  actual?: unknown
  reason: string
}

export interface CaseDiagnosis {
  stage: string
  severity: string
  summary: string
  signals: string[]
  recommendation: string
}

export interface JudgeEvaluation {
  score?: number | null
  passed?: boolean | null
  failure_stage?: string | null
  reason: string
  evidence: string[]
  recommendation: string
  model: string
  available: boolean
}

export interface CaseEvaluationDetail {
  case_id: string
  mode: 'quick' | 'e2e' | string
  query: string
  turns: string[]
  expected_skills: string[]
  selected_skills: string[]
  expected_tool_calls: { tool: string; args_contains?: Record<string, unknown> }[]
  actual_tool_calls: { name?: string; tool?: string; args?: Record<string, unknown> }[]
  final_answer: string
  checks: EvaluationCheck[]
  diagnosis?: CaseDiagnosis | null
  status?: 'pass' | 'warning' | 'fail'
  skill_selection_precision?: number | null
  skill_selection_recall?: number | null
  skill_selection_f1?: number | null
  judge?: JudgeEvaluation | null
  log_summary: Record<string, unknown>[]
  suspected_node?: string | null
  routing_trace?: Record<string, unknown>[]
  diagnostic_outputs?: Record<string, unknown>
}

export interface SkillEvaluationReport {
  skills: SkillEvaluationResult[]
  routing?: Record<string, unknown> | null
  multi_agent_routing?: {
    total_cases: number
    intent_accuracy?: number | null
    intent_precision?: number | null
    intent_recall?: number | null
    intent_f1?: number | null
    metric_extraction_recall?: number | null
    entity_extraction_recall?: number | null
  } | null
  safety?: {
    total_cases: number
    attack_block_rate?: number | null
    unsafe_tool_call_rate?: number | null
    secret_leak_rate?: number | null
    security_event_precision?: number | null
  } | null
  tools?: {
    total_cases: number
    tool_selection_accuracy?: number | null
    argument_fidelity?: number | null
    forbidden_tool_violation_rate?: number | null
    tool_call_precision?: number | null
    tool_call_recall?: number | null
    tool_call_f1?: number | null
    unnecessary_tool_call_rate?: number | null
    missing_tool_call_rate?: number | null
    duplicate_tool_call_rate?: number | null
    argument_precision?: number | null
    argument_recall?: number | null
    argument_f1?: number | null
    argument_schema_validity_rate?: number | null
    argument_value_hallucination_rate?: number | null
  } | null
  answers?: {
    total_cases: number
    answer_contains_rate?: number | null
    forbidden_answer_violation_rate?: number | null
  } | null
  hallucinations?: {
    total_cases: number
    answer_hallucination_rate?: number | null
    repeated_tool_call_rate?: number | null
    tool_argument_hallucination_rate?: number | null
    tool_evidence_usage_rate?: number | null
    unsupported_answer_rate?: number | null
  } | null
  case_details?: CaseEvaluationDetail[]
}

export type SkillEvaluationStreamEvent =
  | {
      type: 'started'
      run_id?: string
      mode: 'quick' | 'e2e'
      source: string
      total: number
      completed: number
      percent?: number
    }
  | {
      type: 'case_progress'
      mode: 'quick' | 'e2e'
      source: string
      total: number
      completed: number
      percent: number
      case_id: string
      expected_skills: string[]
      selected_skills: string[]
      expected_intent?: string | null
      actual_intent?: string | null
      intent_slots?: Record<string, unknown>
      tool_completed: boolean
      tool_failed: boolean
      detail: CaseEvaluationDetail
    }
  | {
      type: 'done'
      run_id?: string
      mode: 'quick' | 'e2e'
      source: string
      total: number
      completed: number
      percent: number
      results: SkillEvaluationSnapshot[]
      report?: SkillEvaluationReport
    }
  | {
      type: 'case_error'
      mode: 'quick' | 'e2e'
      case_id: string
      message: string
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
  summary?: string | null
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

export interface ObservabilitySnapshot {
  frontend: {
    total_events: number
    error_count: number
    resource_error_count: number
    web_vitals: Record<string, { avg: number; p75: number; p95: number; count: number }>
    top_errors: { name: string; count: number }[]
  }
  backend: {
    total_events: number
    tool_errors: number
    tool_retries: number
    p95_duration_ms?: number | null
  }
  anomalies: {
    metric: string
    value: number
    method: 'iqr' | 'zscore'
    severity: 'medium' | 'high'
    reason: string
  }[]
  root_cause: {
    category: string
    summary: string
    evidence: string[]
    recommendation: string
  }
}

// --- SSE Streaming types ---

export interface StreamNodeStarted {
  type: 'node_started'
  node: string
  agent_role?: string
  timestamp?: number
}

export interface StreamNodeFinished {
  type: 'node_finished'
  node: string
  agent_role?: string
  duration_ms?: number
  timestamp?: number
}

export interface StreamToolStarted {
  type: 'tool_started'
  name: string
  args?: Record<string, unknown>
  timestamp?: number
  node?: string
}

export interface StreamToken {
  type: 'token'
  content: string
  node?: string
  agent_role?: string
}

export interface StreamReasoning {
  type: 'reasoning'
  content: string
  node?: string
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
  node?: string
}

export interface StreamToolCallGenerating {
  type: 'tool_call_generating'
  chunks: unknown
  node?: string
  agent_role?: string
}

export interface KnowledgeContextDocument {
  title: string
  source_attribution: string
  score: number
  content_preview: string
  source_file: string
}

export interface KnowledgeContext {
  status: string
  documents: KnowledgeContextDocument[]
}

export interface StreamDone {
  type: 'done'
  status: 'completed'
  message: string
  knowledge_context?: KnowledgeContext
  rewritten_query?: string
  intent_slots?: Record<string, unknown>
}

export interface StreamQueryRewriteCard {
  type: 'card'
  card_type: 'query_rewrite'
  rewritten_query: string
  original_query: string
  intent: string
  secondary_intents: string[]
  confidence: number | null
  needs_clarification: boolean
  missing_slots: string[]
  sub_queries: string[]
}

export interface StreamSkillRouteCard {
  type: 'card'
  card_type: 'skill_route'
  selected_skills: string[]
  confidence: number | null
  reason: string
  stage: string
}

/** A structured routing card carried by the `card` SSE event.
 *  Replaces the raw JSON that route/rewrite nodes used to leak into the answer. */
export type RouteCard = StreamQueryRewriteCard | StreamSkillRouteCard

export interface StreamError {
  type: 'error'
  message: string
}

export type StreamEvent =
  | StreamNodeStarted
  | StreamNodeFinished
  | StreamToolStarted
  | StreamToken
  | StreamReasoning
  | StreamCompacting
  | StreamRequiresApproval
  | StreamToolResult
  | StreamToolCallGenerating
  | StreamDone
  | StreamQueryRewriteCard
  | StreamSkillRouteCard
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
async function* streamRequest<T extends { type: string }>(
  url: string,
  body: unknown,
): AsyncGenerator<T> {
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
            yield { type: eventType, ...payload } as T
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

// ── OTEL Push: Alert types ───────────────────────────────────────

export interface OtelAlert {
  id: string
  received_at: string
  severity: 'critical' | 'warning' | 'info' | 'none'
  level: 'P0' | 'P1' | 'P2' | 'P3'
  service_name: string
  alert_name: string
  summary: string
  description: string
  starts_at: string
  status: string
  // RCA tracking fields (populated by backend auto-RCA)
  rca_status?: 'pending' | 'running' | 'completed' | 'blocked' | 'failed'
  rca_thread_id?: string | null
  rca_pending_approvals?: ToolCallApproval[] | null
  rca_result_text?: string | null
}

export type IncidentStatus = 'open' | 'investigating' | 'mitigated' | 'closed'

export interface IncidentTimelineEvent {
  id: string
  event_type: string
  message: string
  created_at: string
  metadata: Record<string, unknown>
}

export interface IncidentAction {
  id: string
  description: string
  completed: boolean
  created_at: string
  completed_at?: string | null
}

export interface Incident {
  id: string
  alert_id: string
  severity: string
  title: string
  service: string
  status: IncidentStatus
  owner?: string | null
  rca_thread_id?: string | null
  rca_result?: string | null
  created_at: string
  updated_at: string
  timeline: IncidentTimelineEvent[]
  actions: IncidentAction[]
}

export interface GovernancePolicyDocument {
  max_global_tokens?: number | null
  max_thread_tokens?: number | null
  max_global_cost_usd?: number | string | null
  max_thread_cost_usd?: number | string | null
  auto_rca_levels?: string[]
  auto_approved_tools?: string[]
  model_prices?: Record<string, { input_per_million: number | string; output_per_million: number | string }>
}

export interface GovernancePolicyVersion {
  version: number
  document: GovernancePolicyDocument
  is_active: boolean
  created_at: string
}

export interface BudgetTotals {
  total_tokens: number
  estimated_cost_usd: number | string
}

export interface BudgetSnapshot {
  policy: GovernancePolicyVersion
  totals: BudgetTotals
}

async function requestText(url: string): Promise<string> {
  const res = await fetch(`${_baseUrl}${url}`)
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`)
  return res.text()
}

export interface TraceSpan {
  id: number
  trace_id: string
  span_id: string
  parent_span_id?: string | null
  thread_id: string
  kind: string
  status: string
  name?: string | null
  created_at: string
  duration_ms?: number | null
  token_usage: Record<string, unknown>
  error: Record<string, unknown>
  metadata: Record<string, unknown>
}

export interface TraceNode { span: TraceSpan; children: TraceNode[]; orphaned: boolean }
export interface TraceSummary {
  trace_id: string
  total_spans: number
  total_tokens: number
  error_count: number
  retry_count: number
  tool_calls: number
  duration_ms: number
}
export interface TraceView { summary: TraceSummary; spans: TraceSpan[]; roots: TraceNode[] }

export interface EvaluationCaseResult {
  run_id: string; case_id: string; status: string; passed: boolean
  safety_passed?: boolean | null; forbidden_tools: string[]
  latency_ms?: number | null; total_tokens?: number | null
  trace_id?: string | null; thread_id?: string | null; detail: Record<string, unknown>
}
export interface EvaluationRun {
  run_id: string; created_at: string; updated_at: string; mode: string; agent_mode: string
  status: 'running' | 'completed' | 'incomplete' | 'failed'
  dataset_path: string; dataset_hash: string; total_cases: number
  completed_cases: number; failed_cases: number; case_results: EvaluationCaseResult[]
}
export interface RegressionFinding {
  rule: string; severity: 'error' | 'warning' | 'info'; case_id?: string | null
  baseline?: unknown; candidate?: unknown; message: string
}
export interface EvaluationComparison {
  baseline_run_id: string; candidate_run_id: string; status: 'passed' | 'warning' | 'failed'
  baseline_pass_rate: number; candidate_pass_rate: number; findings: RegressionFinding[]
}
export interface StateChange { path: string; kind: string; before?: unknown; after?: unknown }
export interface ReplayDiff { added: StateChange[]; removed: StateChange[]; changed: StateChange[] }
export interface ReplayForkDescriptor {
  source_thread_id: string; source_checkpoint_id: string; target_thread_id: string
  execute: false; provenance: Record<string, unknown>; state: Record<string, unknown>
}
export interface SBSCandidate {
  candidate_id: string
  output: string
  metadata: Record<string, unknown>
}
export interface SBSTask {
  task_id: string
  prompt: string
  candidate_a: SBSCandidate
  candidate_b: SBSCandidate
  status: 'pending' | 'reviewed'
  provenance: Record<string, unknown>
}
export interface BlindedSBSTask {
  task_id: string; prompt: string; candidates: { label: 'A' | 'B'; output: string }[]
}
export interface SBSReview {
  task_id: string; reviewer: string; winner: 'A' | 'B' | 'tie' | 'both_bad'
  reason: string; dimension_scores: Record<string, number>; revision: number
  canonical_winner?: string | null
}

// ── API client ────────────────────────────────────────────────────

export const api = {
  health: () => request<{ status: string }>('/api/health'),

  listIncidents: (status?: IncidentStatus) => {
    const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
    return request<Incident[]>(`/api/incidents${suffix}`)
  },

  updateIncident: (incidentId: string, body: Partial<Pick<Incident, 'status' | 'owner'>>) =>
    request<Incident>(`/api/incidents/${incidentId}`, { method: 'PATCH', body: JSON.stringify(body) }),

  addIncidentAction: (incidentId: string, description: string) =>
    request<Incident>(`/api/incidents/${incidentId}/actions`, {
      method: 'POST', body: JSON.stringify({ description }),
    }),

  updateIncidentAction: (incidentId: string, actionId: string, completed: boolean) =>
    request<Incident>(`/api/incidents/${incidentId}/actions/${actionId}`, {
      method: 'PATCH', body: JSON.stringify({ completed }),
    }),

  getBudget: (threadId?: string) => {
    const suffix = threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ''
    return request<BudgetSnapshot>(`/api/governance/budget${suffix}`)
  },

  listGovernancePolicies: () => request<GovernancePolicyVersion[]>('/api/governance/policies'),

  createGovernancePolicy: (document: GovernancePolicyDocument) =>
    request<GovernancePolicyVersion>('/api/governance/policies', {
      method: 'POST', body: JSON.stringify(document),
    }),

  chat: (body: ChatRequest) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  chatStream: (body: ChatRequest) =>
    streamRequest<StreamEvent>('/api/chat/stream', body),

  approve: (body: ApprovalDecision) =>
    request<ChatResponse>('/api/approve', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  approveStream: (body: ApprovalDecision) =>
    streamRequest<StreamEvent>('/api/approve/stream', body),

  approveBatchStream: (body: ApprovalBatchDecision) =>
    streamRequest<StreamEvent>('/api/approvals/stream', body),

  listPendingApprovals: (threadId: string) =>
    request<ToolCallApproval[]>(`/api/threads/${threadId}/pending-approvals`),

  replay: (threadId: string) =>
    request<ReplayResponse>(`/api/threads/${threadId}/replay`),

  getTrace: (traceId: string) =>
    request<TraceView>(`/api/traces/${encodeURIComponent(traceId)}`),

  listThreadTraces: (threadId: string) =>
    request<TraceSummary[]>(`/api/threads/${encodeURIComponent(threadId)}/traces`),

  listEvaluationRuns: () =>
    request<EvaluationRun[]>('/api/evaluations/runs'),

  compareEvaluationRuns: (baselineRunId: string, candidateRunId: string) =>
    request<EvaluationComparison>('/api/evaluations/compare', {
      method: 'POST',
      body: JSON.stringify({ baseline_run_id: baselineRunId, candidate_run_id: candidateRunId }),
    }),

  diffReplay: (threadId: string, beforeCheckpointId: string, afterCheckpointId: string) =>
    request<ReplayDiff>(`/api/threads/${encodeURIComponent(threadId)}/replay/diff`, {
      method: 'POST',
      body: JSON.stringify({ before_checkpoint_id: beforeCheckpointId, after_checkpoint_id: afterCheckpointId }),
    }),

  createReplayFork: (threadId: string, checkpointId: string, targetThreadId?: string) =>
    request<ReplayForkDescriptor>(`/api/threads/${encodeURIComponent(threadId)}/replay/fork`, {
      method: 'POST',
      body: JSON.stringify({ checkpoint_id: checkpointId, target_thread_id: targetThreadId }),
    }),

  listSBSTasks: () => request<SBSTask[]>('/api/sbs/tasks'),
  createSBSTask: (task: SBSTask) =>
    request<SBSTask>('/api/sbs/tasks', {
      method: 'POST', body: JSON.stringify(task),
    }),
  getSBSTask: (taskId: string) =>
    request<BlindedSBSTask>(`/api/sbs/tasks/${encodeURIComponent(taskId)}`),
  submitSBSReview: (taskId: string, review: SBSReview) =>
    request<SBSReview>(`/api/sbs/tasks/${encodeURIComponent(taskId)}/reviews`, {
      method: 'POST', body: JSON.stringify(review),
    }),
  exportSBS: () => requestText('/api/sbs/export'),

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

  getObservabilitySnapshot: (threadId?: string) => {
    const params = new URLSearchParams()
    if (threadId) params.set('thread_id', threadId)
    const suffix = params.toString() ? `?${params.toString()}` : ''
    return request<ObservabilitySnapshot>(`/api/observability/frontend/summary${suffix}`)
  },

  listSkills: () => request<SkillInfo[]>('/api/skills'),

  reloadSkills: () =>
    request<SkillInfo[]>('/api/skills/reload', { method: 'POST' }),

  listSkillEvaluations: () =>
    request<SkillEvaluationSnapshot[]>('/api/skills/evaluation/latest'),

  listSkillEvaluationHistory: (skillName?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (skillName) params.set('skill_name', skillName)
    return request<SkillEvaluationSnapshot[]>(
      `/api/skills/evaluation/history?${params.toString()}`,
    )
  },

  listSkillEvaluationDatasets: (agentMode: AgentMode = 'single') => {
    const params = new URLSearchParams({ agent_mode: agentMode })
    return request<SkillEvaluationDataset[]>(
      `/api/skills/evaluation/golden-datasets?${params.toString()}`,
    )
  },

  runSkillEvaluation: (body: SkillEvaluationRunRequest) =>
    request<SkillEvaluationRunResponse>('/api/skills/evaluation/run', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  runSkillEvaluationStream: (body: SkillEvaluationRunRequest) =>
    streamRequest<SkillEvaluationStreamEvent>('/api/skills/evaluation/run/stream', body),

  resetSkillEvaluations: () =>
    request<SkillEvaluationResetResponse>('/api/skills/evaluation', {
      method: 'DELETE',
    }),

  // ── OTEL Push alerts ──────────────────────────────────────────

  listOtelAlerts: (limit = 50) =>
    request<OtelAlert[]>(`/api/otel/alerts/history?limit=${limit}`),

  /** Subscribe to SSE stream of real-time OTEL alerts. Returns an AbortController
   *  that can be used to disconnect. */
  streamOtelAlerts: (onAlert: (alert: OtelAlert) => void): AbortController => {
    const controller = new AbortController()
    const url = `${_baseUrl}/api/otel/alerts/stream`

    void (async () => {
      try {
        const res = await fetch(url, {
          headers: { Accept: 'text/event-stream' },
          signal: controller.signal,
        })
        if (!res.body) return
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let eventType = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim()
            } else if (line.startsWith('data: ') && eventType === 'alert') {
              try {
                onAlert(JSON.parse(line.slice(6)))
              } catch { /* skip malformed */ }
            }
            if (line === '') eventType = ''
          }
        }
      } catch {
        // connection closed or aborted
      }
    })()

    return controller
  },

  /** Get RCA status for a specific alert. */
  getOtelRcaStatus: (alertId: string) =>
    request<{
      alert_id: string
      rca_status: string
      rca_thread_id: string | null
      rca_pending_approvals: ToolCallApproval[] | null
    }>(`/api/otel/alerts/${alertId}/rca/status`),

  /** Approve or deny a dangerous tool during P0 RCA. */
  approveOtelRca: (alertId: string, body: { thread_id: string; approval_id: string; approved: boolean }) =>
    request<ChatResponse>(`/api/otel/alerts/${alertId}/rca/approve`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Trigger analysis for a P2/P3 alert with auto-approval for safe tools.
   *  Returns the thread_id so the frontend can auto-navigate to chat. */
  analyzeOtelAlert: (alertId: string, agentMode: AgentMode = 'single') =>
    request<{
      thread_id: string
      status: string
      message: string
      approvals: ToolCallApproval[]
    }>(`/api/otel/alerts/${alertId}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ agent_mode: agentMode }),
    }),
}
