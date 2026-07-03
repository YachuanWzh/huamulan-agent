import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { createRumClient } from './lib/rum'
import { initWebVitals } from './lib/initWebVitals'

const rum = createRumClient()

// Report Core Web Vitals (LCP, FID, INP, CLS, TTFB) to the APM backend
initWebVitals((name, value) => rum.reportWebVital(name, value))

window.addEventListener(
  'error',
  (event) => {
    if (event.error instanceof Error) {
      rum.reportError(event.error)
      return
    }
    rum.reportResourceError('resource', event.filename || window.location.href)
  },
  true,
)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
