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
