---
name: apm-metrics
description: APM metric knowledge base for metric definitions, collection schemas, Web Vitals, error rate, Apdex, frontend custom timings, custom business metrics, conversion metrics, SLO/error budget, alert thresholds, and metric interpretation. Use for explaining or designing metrics; do not run patrol checks or audit agent execution logs.
triggers:
  - Web Vitals
  - LCP
  - CLS
  - INP
  - error rate
  - Apdex
  - custom metrics
  - business metrics
  - conversion rate
  - error budget
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
- Custom business metrics should include a stable event name, numerator,
  denominator, business dimension, sampling window, owner, and alert threshold.
- Conversion metrics such as order success rate or payment conversion rate
  should define funnel steps and exclude duplicate retries from the denominator.

## Alert Guidance

Start with p95 route-level alerts. Page-level alerts should include minimum
sample count to avoid noisy low-traffic routes.
