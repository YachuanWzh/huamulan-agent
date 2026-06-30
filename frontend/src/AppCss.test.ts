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

  it('keeps batch approval cards and args from overflowing the chat panel', () => {
    expect(appCss).toMatch(/\.approval-card\s*\{[^}]*max-width:\s*100%/s)
    expect(appCss).toMatch(/\.approval-batch-card\s*\{[^}]*overflow:\s*hidden/s)
    expect(appCss).toMatch(/\.approval-batch-item\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.approval-batch-item \.tool-args\s*\{[^}]*overflow:\s*auto/s)
    expect(appCss).toMatch(/\.approval-batch-item \.tool-args\s*\{[^}]*white-space:\s*pre-wrap/s)
    expect(appCss).toMatch(/\.approval-batch-item \.tool-args\s*\{[^}]*overflow-wrap:\s*anywhere/s)
  })
})
