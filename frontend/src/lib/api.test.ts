import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { api, setBaseUrl, type StreamEvent } from './api'

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
            },
          ]),
        ),
      )
      const result = await api.listSkills()
      expect(result).toHaveLength(1)
      expect(result[0]!.name).toBe('resolve-time')
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
