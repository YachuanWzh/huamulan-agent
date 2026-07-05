---
name: otel-query
description: Query OpenTelemetry Demo telemetry data — Jaeger traces and Prometheus metrics — for APM analysis and troubleshooting. Use when the user asks to investigate traces, spans, latency, error rates, or any metric from the OTEL observability stack.
triggers:
  - otel
  - OpenTelemetry
  - trace
  - span
  - metric
  - prometheus
  - jaeger
  - latency
  - throughput
  - error rate
scripts:
  - name: query_traces
    description: Search Jaeger for traces by service name, operation, and lookback window. Returns structured trace data for APM analysis.
    command: ["python", "scripts/query_traces.py"]
  - name: query_metrics
    description: Query Prometheus metrics via PromQL through the Grafana proxy. Returns structured metric data for APM analysis.
    command: ["python", "scripts/query_metrics.py"]
---

# OTEL Query Skill

Use this skill when the user wants to query live OpenTelemetry telemetry data
from the OTEL demo observability stack (Jaeger for traces, Prometheus for metrics).

## Available Tools

### query_traces

Search Jaeger for traces. Input JSON:

```json
{
  "service": "frontend",
  "operation": "GET",
  "lookback": "15m",
  "limit": 10,
  "min_duration_ms": 100,
  "max_duration_ms": null
}
```

Returns trace data with spans, durations, and tags.

### query_metrics

Query Prometheus via PromQL. Input JSON:

```json
{
  "query": "histogram_quantile(0.95, sum(rate(http_server_duration_milliseconds_bucket[5m])) by (le, http_route))"
}
```

Returns structured metric results suitable for anomaly detection and RCA.

## Integration with APM Analysis

Use `query_traces` to fetch trace data, then pass results through
`from_jaeger_trace()` and `from_jaeger_trace_to_logs()` in `personal_assistant.apm`
to build an `ObservabilitySnapshot` for root cause analysis.

Use `query_metrics` to fetch Prometheus metrics, then pass results through
`from_prometheus_metric()` to generate `ExecutionLog` entries for the analysis pipeline.

## Configuration

The scripts read service URLs from environment variables:
- `OTEL_JAEGER_API_URL` — Jaeger API base URL (default: `http://192.168.5.7:32801/jaeger/ui/api`)
- `OTEL_PROMETHEUS_PROXY_URL` — Prometheus API via Grafana proxy (default: `http://192.168.5.7:32807/api/datasources/proxy/uid/webstore-metrics/api/v1`)
