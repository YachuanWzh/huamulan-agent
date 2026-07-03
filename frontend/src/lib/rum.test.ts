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
