import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { api, setBaseUrl } from './api'

const BASE = 'http://localhost'
const server = setupServer()

beforeAll(() => {
  setBaseUrl(BASE)
  server.listen()
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

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
