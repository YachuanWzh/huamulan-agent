import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRumClient } from './rum'

describe('createRumClient', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reports custom timings through fetch when sendBeacon is unavailable', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    })

    const rum = createRumClient({ endpoint: '/api/observability/frontend/events' })
    await rum.reportTiming('first-message-ready', 124, { route: '/chat' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/observability/frontend/events',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('first-message-ready'),
      }),
    )
  })

  it('reports web vitals with correct event type and payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    })

    const rum = createRumClient({ endpoint: '/api/observability/frontend/events' })
    await rum.reportWebVital('LCP', 2500)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/observability/frontend/events',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"type":"web_vital"'),
      }),
    )
    const body = JSON.parse(fetchMock.mock.calls[0]![1]!.body as string)
    expect(body).toMatchObject({ type: 'web_vital', name: 'LCP', value: 2500 })
  })

  it('uses sendBeacon for js errors when available', () => {
    const beaconMock = vi.fn().mockReturnValue(true)
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: beaconMock,
    })

    const rum = createRumClient({ endpoint: '/rum' })
    rum.reportError(new Error('boom'))

    expect(beaconMock).toHaveBeenCalledWith('/rum', expect.any(Blob))
  })
})
