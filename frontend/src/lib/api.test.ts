import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import {
  api,
  setBaseUrl,
  type SkillEvaluationStreamEvent,
  type StreamEvent,
} from './api'

const BASE = 'http://localhost'
const server = setupServer()

beforeAll(() => {
  setBaseUrl(BASE)
  server.listen()
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

/** Build an SSE response body matching the real backend format.
 *  The `type` goes in the `event:` line; the remaining fields go in `data:`. */
function sseBody(events: StreamEvent[]): string {
  let body = ''
  for (const e of events) {
    const { type, ...payload } = e
    body += `event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`
  }
  body += 'data: [DONE]\n\n'
  return body
}

function skillEvaluationSseBody(events: SkillEvaluationStreamEvent[]): string {
  let body = ''
  for (const e of events) {
    const { type, ...payload } = e
    body += `event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`
  }
  body += 'data: [DONE]\n\n'
  return body
}

describe('api', () => {
  describe('health', () => {
    it('returns status ok', async () => {
      server.use(
        http.get(`${BASE}/api/health`, () => HttpResponse.json({ status: 'ok' })),
      )
      const result = await api.health()
      expect(result).toEqual({ status: 'ok' })
    })
  })

  describe('chat', () => {
    it('sends message and returns completed response', async () => {
      server.use(
        http.post(`${BASE}/api/chat`, async ({ request }) => {
          const body = (await request.json()) as { thread_id: string; message: string }
          return HttpResponse.json({
            thread_id: body.thread_id,
            status: 'completed',
            message: 'Hello!',
            approvals: [],
          })
        }),
      )
      const result = await api.chat({ thread_id: 't1', message: 'Hi' })
      expect(result.status).toBe('completed')
      expect(result.message).toBe('Hello!')
    })

    it('returns requires_approval with approvals', async () => {
      server.use(
        http.post(`${BASE}/api/chat`, () =>
          HttpResponse.json({
            thread_id: 't1',
            status: 'requires_approval',
            approvals: [
              {
                approval_id: 'a1',
                tool_call_id: 'tc1',
                name: 'resolve_current_time',
                args: {},
              },
            ],
          }),
        ),
      )
      const result = await api.chat({ thread_id: 't1', message: 'What time?' })
      expect(result.status).toBe('requires_approval')
      expect(result.approvals).toHaveLength(1)
    })
  })

  describe('chatStream', () => {
    it('yields token and done events from SSE stream', async () => {
      const events: StreamEvent[] = [
        { type: 'token', content: 'Hello' },
        { type: 'token', content: ' world' },
        { type: 'done', status: 'completed', message: 'Hello world' },
      ]
      server.use(
        http.post(`${BASE}/api/chat/stream`, () =>
          new HttpResponse(sseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
        ),
      )
      const results: StreamEvent[] = []
      for await (const e of api.chatStream({ thread_id: 't1', message: 'Hi' })) {
        results.push(e)
      }
      expect(results).toHaveLength(3)
      expect(results[0]).toEqual({ type: 'token', content: 'Hello' })
      expect(results[1]).toEqual({ type: 'token', content: ' world' })
      expect(results[2]).toEqual({ type: 'done', status: 'completed', message: 'Hello world' })
    })

    it('yields reasoning events from SSE stream', async () => {
      const events: StreamEvent[] = [
        { type: 'reasoning', content: 'thinking' },
        { type: 'token', content: 'answer' },
        { type: 'done', status: 'completed', message: 'answer' },
      ]
      server.use(
        http.post(`${BASE}/api/chat/stream`, () =>
          new HttpResponse(sseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
        ),
      )
      const results: StreamEvent[] = []
      for await (const e of api.chatStream({ thread_id: 't1', message: 'Hi' })) {
        results.push(e)
      }
      expect(results[0]).toEqual({ type: 'reasoning', content: 'thinking' })
    })

    it('yields compacting events from SSE stream', async () => {
      const events: StreamEvent[] = [
        { type: 'compacting', status: 'started', content: 'Compacting context' },
        { type: 'compacting', status: 'completed', content: 'Context compacted' },
      ]
      server.use(
        http.post(`${BASE}/api/chat/stream`, () =>
          new HttpResponse(sseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
        ),
      )
      const results: StreamEvent[] = []
      for await (const e of api.chatStream({ thread_id: 't1', message: 'Hi' })) {
        results.push(e)
      }
      expect(results).toEqual(events)
    })

    it('yields requires_approval event', async () => {
      const events: StreamEvent[] = [
        { type: 'requires_approval', approvals: [
          { approval_id: 'a1', tool_call_id: 'tc1', name: 'get_time', args: {} },
        ]},
      ]
      server.use(
        http.post(`${BASE}/api/chat/stream`, () =>
          new HttpResponse(sseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
        ),
      )
      const results: StreamEvent[] = []
      for await (const e of api.chatStream({ thread_id: 't1', message: 'Hi' })) {
        results.push(e)
      }
      expect(results).toHaveLength(1)
      expect(results[0]!.type).toBe('requires_approval')
    })
  })

  describe('approve', () => {
    it('sends approval decision', async () => {
      server.use(
        http.post(`${BASE}/api/approve`, () =>
          HttpResponse.json({
            thread_id: 't1',
            status: 'completed',
            message: 'The time is 3pm.',
            approvals: [],
          }),
        ),
      )
      const result = await api.approve({
        thread_id: 't1',
        approval_id: 'a1',
        approved: true,
      })
      expect(result.status).toBe('completed')
    })
  })

  describe('approveStream', () => {
    it('streams tokens after approval', async () => {
      const events: StreamEvent[] = [
        { type: 'token', content: 'The time is 3pm.' },
        { type: 'done', status: 'completed', message: 'The time is 3pm.' },
      ]
      server.use(
        http.post(`${BASE}/api/approve/stream`, () =>
          new HttpResponse(sseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          }),
        ),
      )
      const results: StreamEvent[] = []
      for await (const e of api.approveStream({ thread_id: 't1', approval_id: 'a1', approved: true })) {
        results.push(e)
      }
      expect(results).toHaveLength(2)
      expect(results[0]!.type).toBe('token')
    })
  })

  describe('replay', () => {
    it('returns thread states', async () => {
      server.use(
        http.get(`${BASE}/api/threads/t1/replay`, () =>
          HttpResponse.json({ thread_id: 't1', states: [{ messages: [] }] }),
        ),
      )
      const result = await api.replay('t1')
      expect(result.thread_id).toBe('t1')
      expect(result.states).toHaveLength(1)
    })
  })

  describe('deleteThread', () => {
    it('deletes a thread', async () => {
      server.use(
        http.delete(`${BASE}/api/threads/t1`, () =>
          HttpResponse.json({ thread_id: 't1', deleted: true }),
        ),
      )

      const result = await api.deleteThread('t1')

      expect(result).toEqual({ thread_id: 't1', deleted: true })
    })
  })

  describe('clearThreads', () => {
    it('deletes all conversation sessions', async () => {
      server.use(
        http.delete(`${BASE}/api/threads`, () =>
          HttpResponse.json({ thread_ids: ['thread-1', 'thread-2'], deleted: 2 }),
        ),
      )

      const result = await api.clearThreads()

      expect(result).toEqual({ thread_ids: ['thread-1', 'thread-2'], deleted: 2 })
    })
  })

  describe('listThreads', () => {
    it('returns conversation sessions', async () => {
      server.use(
        http.get(`${BASE}/api/threads`, ({ request }) => {
          const url = new URL(request.url)
          expect(url.searchParams.get('limit')).toBe('100')
          return HttpResponse.json([
            {
              thread_id: 'thread-2',
              updated_at: '2026-06-29T05:00:00+00:00',
              summary: '修复首次发送消息丢失',
            },
          ])
        }),
      )

      const result = await api.listThreads()

      expect(result).toEqual([
        {
          thread_id: 'thread-2',
          updated_at: '2026-06-29T05:00:00+00:00',
          summary: '修复首次发送消息丢失',
        },
      ])
    })
  })

  describe('approveBatchStream', () => {
    it('streams tokens after a batch approval decision', async () => {
      const events: StreamEvent[] = [
        { type: 'token', content: 'The tools are complete.' },
        { type: 'done', status: 'completed', message: 'The tools are complete.' },
      ]
      server.use(
        http.post(`${BASE}/api/approvals/stream`, async ({ request }) => {
          const body = (await request.json()) as {
            thread_id: string
            decisions: { approval_id: string; approved: boolean }[]
          }
          expect(body).toEqual({
            thread_id: 't1',
            decisions: [
              { approval_id: 'a1', approved: true },
              { approval_id: 'a2', approved: false },
            ],
          })
          return new HttpResponse(sseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          })
        }),
      )

      const results: StreamEvent[] = []
      for await (const e of api.approveBatchStream({
        thread_id: 't1',
        decisions: [
          { approval_id: 'a1', approved: true },
          { approval_id: 'a2', approved: false },
        ],
      })) {
        results.push(e)
      }

      expect(results).toEqual(events)
    })
  })

  describe('listAuditEvents', () => {
    it('returns audit events for a thread', async () => {
      server.use(
        http.get(`${BASE}/api/audit-events`, ({ request }) => {
          const url = new URL(request.url)
          expect(url.searchParams.get('thread_id')).toBe('t1')
          return HttpResponse.json([
            {
              id: 7,
              created_at: '2026-06-29T04:00:00+00:00',
              thread_id: 't1',
              source: 'prompt',
              category: 'instruction_override',
              severity: 'HIGH',
              reason: 'User message attempts to override prior or system instructions.',
              subject: 'ignore previous instructions',
              metadata: { prompt_guard_blocked: true },
            },
          ])
        }),
      )

      const result = await api.listAuditEvents('t1')

      expect(result).toHaveLength(1)
      expect(result[0]!.category).toBe('instruction_override')
    })
  })

  describe('listToolErrors', () => {
    it('returns archived tool errors for a thread', async () => {
      server.use(
        http.get(`${BASE}/api/tool-errors`, ({ request }) => {
          const url = new URL(request.url)
          expect(url.searchParams.get('thread_id')).toBe('t1')
          expect(url.searchParams.get('limit')).toBe('100')
          return HttpResponse.json([
            {
              id: 12,
              created_at: '2026-06-30T01:00:00+00:00',
              thread_id: 't1',
              tool_call_id: 'call-1',
              tool_name: 'lookup',
              tool_args: { query: 'alpha' },
              attempt: 3,
              max_attempts: 3,
              error_type: 'ValueError',
              error_message: 'bad query',
              will_retry: false,
            },
          ])
        }),
      )

      const result = await api.listToolErrors('t1')

      expect(result).toHaveLength(1)
      expect(result[0]!.tool_name).toBe('lookup')
      expect(result[0]!.tool_args).toEqual({ query: 'alpha' })
    })
  })

  describe('execution audit', () => {
    it('returns execution logs for a thread', async () => {
      server.use(
        http.get(`${BASE}/api/threads/thread-1/execution-logs`, ({ request }) => {
          const url = new URL(request.url)
          expect(url.searchParams.get('limit')).toBe('500')
          return HttpResponse.json([
            {
              id: 1,
              created_at: '2026-06-30T01:00:00Z',
              thread_id: 'thread-1',
              event_type: 'llm',
              status: 'completed',
              name: 'agent',
              input: {},
              output: {},
              error: {},
              duration_ms: 30,
              token_usage: { total_tokens: 42 },
              metadata: {},
            },
          ])
        }),
      )

      const result = await api.listExecutionLogs('thread-1')

      expect(result[0]!.event_type).toBe('llm')
      expect(result[0]!.token_usage.total_tokens).toBe(42)
    })

    it('returns execution summary for a thread', async () => {
      server.use(
        http.get(`${BASE}/api/threads/thread-1/execution-summary`, () =>
          HttpResponse.json({
            thread_id: 'thread-1',
            total_events: 4,
            total_tokens: 100,
            prompt_tokens: 70,
            completion_tokens: 30,
            tool_calls: 2,
            tool_errors: 1,
            tool_retries: 1,
            security_events: 0,
            total_duration_ms: 250,
          }),
        ),
      )

      const result = await api.getExecutionSummary('thread-1')

      expect(result.total_tokens).toBe(100)
      expect(result.tool_retries).toBe(1)
    })
  })

  describe('listSkills', () => {
    it('returns skills array', async () => {
      server.use(
        http.get(`${BASE}/api/skills`, () =>
          HttpResponse.json([
            {
              name: 'resolve-time',
              description: 'Resolve current time',
              tool_names: ['resolve_current_time'],
              path: '/skills/resolve-time',
              loaded: false,
              evaluation: {
                overall_score: 0.93,
                description_tokens: 18,
                skill_md_lines: 42,
                python_lines: 88,
                max_cyclomatic_complexity: 4,
                tool_count: 1,
              },
              latest_evaluation: {
                id: 3,
                created_at: '2026-07-02T01:00:00Z',
                skill_name: 'resolve-time',
                overall_score: 0.88,
                routing_score: 1,
                runtime_score: null,
                usage_score: null,
                static_score: 0.7,
                source: 'golden:golden.jsonl',
                report: {},
              },
            },
          ]),
        ),
      )
      const result = await api.listSkills()
      expect(result).toHaveLength(1)
      expect(result[0]!.name).toBe('resolve-time')
      expect(result[0]!.evaluation?.overall_score).toBe(0.93)
      expect(result[0]!.latest_evaluation?.overall_score).toBe(0.88)
    })
  })

  describe('skill evaluation', () => {
    it('returns latest persisted skill evaluations', async () => {
      server.use(
        http.get(`${BASE}/api/skills/evaluation/latest`, () =>
          HttpResponse.json([
            {
              id: 3,
              created_at: '2026-07-02T01:00:00Z',
              skill_name: 'resolve-time',
              overall_score: 0.88,
              routing_score: 1,
              runtime_score: null,
              usage_score: null,
              static_score: 0.7,
              source: 'golden:golden.jsonl',
              report: {},
            },
          ]),
        ),
      )

      const result = await api.listSkillEvaluations()

      expect(result[0]!.skill_name).toBe('resolve-time')
      expect(result[0]!.overall_score).toBe(0.88)
    })

    it('returns persisted skill evaluation history for a skill', async () => {
      server.use(
        http.get(`${BASE}/api/skills/evaluation/history`, ({ request }) => {
          const url = new URL(request.url)
          expect(url.searchParams.get('skill_name')).toBe('weather')
          expect(url.searchParams.get('limit')).toBe('100')
          return HttpResponse.json([
            {
              id: 4,
              created_at: '2026-07-03T01:00:00Z',
              skill_name: 'weather',
              overall_score: 0.9,
              routing_score: 1,
              runtime_score: null,
              usage_score: null,
              static_score: 0.7,
              source: 'golden:new.jsonl',
              report: {},
            },
          ])
        }),
      )

      const result = await api.listSkillEvaluationHistory('weather')

      expect(result[0]!.skill_name).toBe('weather')
      expect(result[0]!.overall_score).toBe(0.9)
    })

    it('runs a golden dataset evaluation', async () => {
      server.use(
        http.post(`${BASE}/api/skills/evaluation/run`, async ({ request }) => {
          expect(await request.json()).toEqual({ golden_path: 'golden.jsonl' })
          return HttpResponse.json({
            source: 'golden:golden.jsonl',
            results: [],
          })
        }),
      )

      const result = await api.runSkillEvaluation({ golden_path: 'golden.jsonl' })

      expect(result.source).toBe('golden:golden.jsonl')
    })

    it('streams golden dataset evaluation progress', async () => {
      const events: SkillEvaluationStreamEvent[] = [
        {
          type: 'started',
          mode: 'quick',
          source: 'golden:golden.jsonl',
          total: 2,
          completed: 0,
        },
        {
          type: 'case_progress',
          mode: 'quick',
          source: 'golden:golden.jsonl',
          total: 2,
          completed: 1,
          percent: 50,
          case_id: 'rt-001',
          expected_skills: ['resolve-time'],
          selected_skills: ['resolve-time'],
          tool_completed: false,
          tool_failed: false,
          detail: {
            case_id: 'rt-001',
            mode: 'quick',
            query: 'what time is it',
            turns: [],
            expected_skills: ['resolve-time'],
            selected_skills: ['resolve-time'],
            expected_tool_calls: [],
            actual_tool_calls: [],
            final_answer: '',
            checks: [
              {
                name: 'skill_routing',
                stage: 'routing',
                passed: true,
                expected: ['resolve-time'],
                actual: ['resolve-time'],
                reason: '',
              },
            ],
            diagnosis: {
              stage: 'passed',
              severity: 'info',
              summary: '所有确定性检查通过',
              signals: [],
              recommendation: '无需处理',
            },
            log_summary: [],
          },
        },
        {
          type: 'done',
          mode: 'quick',
          source: 'golden:golden.jsonl',
          total: 2,
          completed: 2,
          percent: 100,
          results: [],
          report: {
            skills: [],
            case_details: [
              {
                case_id: 'rt-001',
                mode: 'quick',
                query: 'what time is it',
                turns: [],
                expected_skills: ['resolve-time'],
                selected_skills: ['resolve-time'],
                expected_tool_calls: [],
                actual_tool_calls: [],
                final_answer: '',
                checks: [],
                diagnosis: {
                  stage: 'passed',
                  severity: 'info',
                  summary: '所有确定性检查通过',
                  signals: [],
                  recommendation: '无需处理',
                },
                log_summary: [],
              },
            ],
          },
        },
      ]
      server.use(
        http.post(`${BASE}/api/skills/evaluation/run/stream`, async ({ request }) => {
          expect(await request.json()).toEqual({
            golden_path: 'golden.jsonl',
            evaluation_mode: 'e2e',
          })
          return new HttpResponse(skillEvaluationSseBody(events), {
            headers: { 'Content-Type': 'text/event-stream' },
          })
        }),
      )

      const results: SkillEvaluationStreamEvent[] = []
      for await (const e of api.runSkillEvaluationStream({
        golden_path: 'golden.jsonl',
        evaluation_mode: 'e2e',
      })) {
        results.push(e)
      }

      expect(results).toEqual(events)
      expect(results[1]!.type === 'case_progress' && results[1]!.detail.case_id).toBe(
        'rt-001',
      )
    })

    it('resets persisted skill evaluations', async () => {
      server.use(
        http.delete(`${BASE}/api/skills/evaluation`, () =>
          HttpResponse.json({ deleted: 3, results: [] }),
        ),
      )

      const result = await api.resetSkillEvaluations()

      expect(result).toEqual({ deleted: 3, results: [] })
    })
  })

  describe('reloadSkills', () => {
    it('returns reloaded skills', async () => {
      server.use(
        http.post(`${BASE}/api/skills/reload`, () => HttpResponse.json([])),
      )
      const result = await api.reloadSkills()
      expect(result).toEqual([])
    })
  })

  describe('error handling', () => {
    it('throws on non-ok response', async () => {
      server.use(
        http.get(`${BASE}/api/health`, () => new HttpResponse(null, { status: 500 })),
      )
      await expect(api.health()).rejects.toThrow()
    })
  })
})
