---
name: troubleshoot-runbook
description: APM troubleshooting runbook library for CPU spikes, memory leaks, slow APIs, JS errors, resource failures, and frontend rendering regressions.
triggers:
  - runbook
  - slow API
  - memory leak
  - CPU spike
  - JS error
  - resource failure
---

# Troubleshoot Runbook

## JS Error

1. Group by error name, message, stack frame, route, and release.
2. Check whether the error follows a deploy or feature flag rollout.
3. Reproduce the UI state with the same route and payload shape.
4. Add guards for nullable data and regression tests for the failing state.

## Resource Failure

1. Identify the failed URL, status code, CDN region, and release asset hash.
2. Verify bundler manifest, cache headers, and deployment completion.
3. Roll back or re-upload missing assets when many sessions are affected.

## Slow API

1. Compare p95/p99 latency with baseline and error/retry rate.
2. Check downstream dependencies, DB queries, cache hit rate, and payload size.
3. Add timeout, retry budget, and query/index fixes according to evidence.

## Memory Leak

1. Compare heap growth across route transitions or repeated agent turns.
2. Inspect listeners, timers, subscriptions, caches, and retained closures.
3. Verify with heap snapshots or long-running synthetic checks.
