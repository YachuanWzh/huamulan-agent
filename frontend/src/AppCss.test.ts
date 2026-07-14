/// <reference types="node" />

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const appCss = readFileSync(resolve(__dirname, 'App.css'), 'utf8')

describe('App stylesheet decorations', () => {
  it('defines the huamulan-agent palette tokens', () => {
    expect(appCss).toContain('--color-iron-armor: #171717')
    expect(appCss).toContain('--color-jujube-red: #8f2d2d')
    expect(appCss).toContain('--color-saddle-leather: #8a5a35')
    expect(appCss).toContain('--color-rice-paper: #f7f1e3')
    expect(appCss).toContain('--color-bronze-green: #34675c')
  })

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
    expect(appCss).toMatch(/\.approval-batch-card\s*\{[^}]*max-height:\s*min\(68vh,\s*640px\)/s)
    expect(appCss).toMatch(/\.approval-batch-card\s*\{[^}]*display:\s*grid/s)
    expect(appCss).toMatch(/\.approval-batch-card\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0,\s*1fr\) auto/s)
    expect(appCss).toMatch(/\.approval-batch-card\s*\{[^}]*overflow:\s*hidden/s)
    expect(appCss).toMatch(/\.approval-batch-list\s*\{[^}]*overflow-y:\s*auto/s)
    expect(appCss).toMatch(/\.approval-batch-item\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.approval-batch-item \.tool-args\s*\{[^}]*overflow:\s*auto/s)
    expect(appCss).toMatch(/\.approval-batch-item \.tool-args\s*\{[^}]*white-space:\s*pre-wrap/s)
    expect(appCss).toMatch(/\.approval-batch-item \.tool-args\s*\{[^}]*overflow-wrap:\s*anywhere/s)
  })

  it('keeps skill evaluation cards aligned with fixed description height', () => {
    expect(appCss).toMatch(/\.skill-evaluation-grid\s*\{[^}]*grid-auto-rows:\s*1fr/s)
    expect(appCss).toMatch(/\.skill-evaluation-card\s*\{[^}]*height:\s*100%/s)
    expect(appCss).toMatch(/\.skill-evaluation-card\s*\{[^}]*display:\s*grid/s)
    expect(appCss).toMatch(/\.skill-evaluation-card\s*\{[^}]*grid-template-rows:\s*auto auto 1fr/s)
    expect(appCss).toMatch(/\.skill-evaluation-card-header p\s*\{[^}]*display:\s*-webkit-box/s)
    expect(appCss).toMatch(/\.skill-evaluation-card-header p\s*\{[^}]*-webkit-line-clamp:\s*3/s)
    expect(appCss).toMatch(/\.skill-evaluation-card-header p\s*\{[^}]*min-height:\s*3\.54rem/s)
    expect(appCss).toMatch(/\.skill-evaluation-card-header p\s*\{[^}]*max-height:\s*3\.54rem/s)
    expect(appCss).toMatch(/\.skill-evaluation-card-header p\s*\{[^}]*overflow:\s*hidden/s)
  })

  it('keeps evaluation detail panels from overflowing the sidebar', () => {
    expect(appCss).toMatch(/\.evaluation-details\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.evaluation-case\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.evaluation-case-body\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.evaluation-detail-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(min\(280px,\s*100%\),\s*1fr\)\)/s)
    expect(appCss).toMatch(/\.evaluation-json-block pre\s*\{[^}]*max-width:\s*100%/s)
    expect(appCss).toMatch(/\.evaluation-diagnostic-panel\s*\{[^}]*min-width:\s*0/s)
    expect(appCss).toMatch(/\.evaluation-routing-step\s*\{[^}]*grid-template-columns:\s*minmax\(96px,\s*130px\) minmax\(0,\s*1fr\)/s)
    expect(appCss).toMatch(/\.evaluation-diagnostic-panel pre\s*\{[^}]*max-width:\s*100%/s)
  })

  it('animates the e2e evaluation run topology with reduced-motion coverage', () => {
    expect(appCss).toMatch(/\.evaluation-run-topology\s*\{/)
    expect(appCss).toMatch(/\.topology-node\.is-active\s*\{/)
    expect(appCss).toMatch(/\.topology-connector::after\s*\{/)
    expect(appCss).toMatch(/@keyframes topology-flow\s*\{/)
    expect(appCss).toMatch(/@keyframes topology-pulse\s*\{/)
    expect(appCss).toMatch(
      /@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*\.topology-connector::after\s*\{[^}]*animation:\s*none/s,
    )
  })

  it('keeps Agent Engineering tabs intrinsic and evidence panes scrollable', () => {
    expect(appCss).toMatch(
      /\.engineering-workspace\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0,\s*1fr\)/s,
    )
    expect(appCss).toMatch(/\.engineering-grid\s*\{[^}]*min-height:\s*0/s)
    expect(appCss).toMatch(
      /\.evidence-index,\s*\.evidence-canvas\s*\{[^}]*overflow:\s*auto/s,
    )
  })

  it('presents the e2e topology as swimlanes with arrowed lane transitions', () => {
    expect(appCss).toMatch(/\.topology-lanes\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(220px,\s*1fr\)\)/s)
    expect(appCss).toMatch(/\.topology-lane\s*\{[^}]*min-height:\s*132px/s)
    expect(appCss).toMatch(/\.topology-lane-title\s*\{[^}]*position:\s*absolute/s)
    expect(appCss).toMatch(/\.topology-lane-title\s*\{[^}]*top:\s*calc\(var\(--space-3\) \* -1\)/s)
    expect(appCss).toMatch(/\.topology-connector::before\s*\{[^}]*border-left:\s*7px solid #58a6c2/s)
  })
})
