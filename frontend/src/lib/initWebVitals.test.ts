import { afterEach, describe, expect, it, vi } from 'vitest'

const mockOnLCP = vi.fn()
const mockOnFCP = vi.fn()
const mockOnINP = vi.fn()
const mockOnCLS = vi.fn()
const mockOnTTFB = vi.fn()

vi.mock('web-vitals', () => ({
  onLCP: mockOnLCP,
  onFCP: mockOnFCP,
  onINP: mockOnINP,
  onCLS: mockOnCLS,
  onTTFB: mockOnTTFB,
}))

// Dynamic import after mock setup
async function importInitWebVitals() {
  return import('./initWebVitals')
}

/** Extract the first callback passed to a mocked web-vitals function. */
function firstCallback(mock: ReturnType<typeof vi.fn>): (metric: { name: string; value: number }) => void {
  return mock.mock.calls[0]![0]
}

describe('initWebVitals', () => {
  afterEach(() => {
    vi.resetAllMocks()
    vi.resetModules()
  })

  it('registers callbacks for all five core web vitals', async () => {
    const { initWebVitals } = await importInitWebVitals()
    const reportWebVital = vi.fn()

    initWebVitals(reportWebVital)

    expect(mockOnLCP).toHaveBeenCalledTimes(1)
    expect(mockOnFCP).toHaveBeenCalledTimes(1)
    expect(mockOnINP).toHaveBeenCalledTimes(1)
    expect(mockOnCLS).toHaveBeenCalledTimes(1)
    expect(mockOnTTFB).toHaveBeenCalledTimes(1)
  })

  it.each([
    { mock: () => mockOnLCP, name: 'LCP', value: 1234.5 },
    { mock: () => mockOnFCP, name: 'FCP', value: 800 },
    { mock: () => mockOnINP, name: 'INP', value: 150 },
    { mock: () => mockOnCLS, name: 'CLS', value: 0.123 },
    { mock: () => mockOnTTFB, name: 'TTFB', value: 350 },
  ])('reports $name value when the callback fires', async ({ mock, name, value }) => {
    const { initWebVitals } = await importInitWebVitals()
    const reportWebVital = vi.fn()

    initWebVitals(reportWebVital)

    const callback = firstCallback(mock())
    callback({ name, value })

    expect(reportWebVital).toHaveBeenCalledWith(name, value)
  })
})
