---
name: apm-metrics
description: APM metric knowledge base for Web Vitals, error rate, Apdex, frontend custom timings, alert thresholds, and metric interpretation.
triggers:
  - Web Vitals
  - LCP
  - CLS
  - INP
  - error rate
  - Apdex
---

# APM Metrics Knowledge Base

## Core Web Vitals

- LCP: largest contentful paint. Good <= 2500 ms, needs improvement <= 4000 ms,
  poor > 4000 ms.
- CLS: cumulative layout shift. Good <= 0.1, needs improvement <= 0.25,
  poor > 0.25.
- INP: interaction to next paint. Good <= 200 ms, needs improvement <= 500 ms,
  poor > 500 ms.
- TTFB: time to first byte. Good <= 800 ms.

## Product Metrics

- JS error rate = JS error sessions / total sessions.
- Resource error rate = failed resource loads / resource load attempts.
- Apdex = (satisfied + tolerating / 2) / total.
- Custom timing should include name, value, route, session, and trace id when
  available.

## Alert Guidance

Start with p95 route-level alerts. Page-level alerts should include minimum
sample count to avoid noisy low-traffic routes.
