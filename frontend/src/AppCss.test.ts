import { describe, expect, it } from 'vitest'
import appCss from './App.css?raw'

describe('App stylesheet decorations', () => {
  it('does not render decorative markers in the header or empty state', () => {
    expect(appCss).not.toContain('.app-header h1::before')
    expect(appCss).not.toContain('.empty-state::before')
  })
})
