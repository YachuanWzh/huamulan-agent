export interface RumClientOptions {
  endpoint?: string
  sessionId?: string
}

export type RumEventType = 'web_vital' | 'custom_timing' | 'js_error' | 'resource_error'

export interface RumEvent {
  type: RumEventType
  name: string
  value: number
  url?: string
  session_id?: string
  timestamp?: string
  metadata?: Record<string, unknown>
}

export function createRumClient(options: RumClientOptions = {}) {
  const endpoint = options.endpoint ?? '/api/observability/frontend/events'
  const sessionId = options.sessionId ?? getOrCreateSessionId()

  const send = async (event: RumEvent) => {
    const payload: RumEvent = {
      ...event,
      session_id: event.session_id ?? sessionId,
      timestamp: event.timestamp ?? new Date().toISOString(),
      url: event.url ?? window.location.pathname,
    }
    const body = JSON.stringify(payload)
    if (navigator.sendBeacon) {
      const accepted = navigator.sendBeacon(endpoint, new Blob([body], { type: 'application/json' }))
      if (accepted) return
    }
    await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined)
  }

  return {
    reportTiming: (name: string, value: number, metadata: Record<string, unknown> = {}) =>
      send({ type: 'custom_timing', name, value, metadata }),
    reportWebVital: (name: string, value: number, metadata: Record<string, unknown> = {}) =>
      send({ type: 'web_vital', name, value, metadata }),
    reportError: (error: Error, metadata: Record<string, unknown> = {}) =>
      void send({
        type: 'js_error',
        name: error.name || 'Error',
        value: 1,
        metadata: { ...metadata, message: error.message, stack: error.stack },
      }),
    reportResourceError: (name: string, url: string, metadata: Record<string, unknown> = {}) =>
      void send({ type: 'resource_error', name, value: 1, url, metadata }),
  }
}

function getOrCreateSessionId() {
  const key = 'rumSessionId'
  const existing = localStorage.getItem(key)
  if (existing) return existing
  const id = crypto.randomUUID()
  localStorage.setItem(key, id)
  return id
}
