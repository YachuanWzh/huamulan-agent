/// <reference types="node" />

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const appCss = readFileSync(resolve(__dirname, 'App.css'), 'utf8')

describe('App stylesheet decorations', () => {
  it('does not render decorative markers in the header or empty state', () => {
    expect(appCss).not.toContain('.app-header h1::before')
    expect(appCss).not.toContain('.empty-state::before')
  })

  it('keeps expanded tool results in a fixed-height scroll container', () => {
    expect(appCss).toMatch(/\.tool-result-content\s*\{[^}]*max-height:\s*240px/s)
    expect(appCss).toMatch(/\.tool-result-content\s*\{[^}]*overflow:\s*auto/s)
  })
})
