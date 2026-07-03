import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { createRumClient } from './lib/rum'

const rum = createRumClient()
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
