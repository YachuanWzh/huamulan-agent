from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any, Literal

from pydantic import BaseModel, Field

from personal_assistant.api.schemas import ExecutionLog


RumEventType = Literal["web_vital", "custom_timing", "js_error", "resource_error"]


class FrontendRumEvent(BaseModel):
    type: RumEventType
    name: str
    value: float
    url: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnomalySignal(BaseModel):
    metric: str
    value: float
    method: Literal["iqr", "zscore"]
    severity: Literal["medium", "high"]
    reason: str


class RootCauseReport(BaseModel):
    category: Literal[
        "frontend_error",
        "frontend_resource",
        "frontend_performance",
        "backend_retry",
        "normal",
    ]
    summary: str
    evidence: list[str] = Field(default_factory=list)
    recommendation: str


class FrontendObservabilitySummary(BaseModel):
    total_events: int
    error_count: int
    resource_error_count: int
    web_vitals: dict[str, dict[str, float]] = Field(default_factory=dict)
    top_errors: list[dict[str, Any]] = Field(default_factory=list)


class BackendObservabilitySummary(BaseModel):
    total_events: int
    tool_errors: int
    tool_retries: int
    p95_duration_ms: float | None = None


class ObservabilitySnapshot(BaseModel):
    frontend: FrontendObservabilitySummary
    backend: BackendObservabilitySummary
    anomalies: list[AnomalySignal]
    root_cause: RootCauseReport


def detect_anomalies(values: list[float], *, metric: str) -> list[AnomalySignal]:
    samples = [float(value) for value in values if value is not None]
    if len(samples) < 4:
        return []
    ordered = sorted(samples)
    q1 = _percentile(ordered, 25)
    q3 = _percentile(ordered, 75)
    iqr = q3 - q1
    upper = q3 + 1.5 * iqr
    avg = mean(samples)
    deviation = pstdev(samples)
    signals: list[AnomalySignal] = []
    for value in ordered:
        if iqr > 0 and value > upper:
            signals.append(
                AnomalySignal(
                    metric=metric,
                    value=value,
                    method="iqr",
                    severity="high",
                    reason=f"{metric} value {value:g} is above IQR upper bound {upper:g}",
                )
            )
        elif deviation > 0 and (value - avg) / deviation >= 2:
            signals.append(
                AnomalySignal(
                    metric=metric,
                    value=value,
                    method="zscore",
                    severity="medium",
                    reason=f"{metric} value {value:g} is more than 2 standard deviations high",
                )
            )
    return signals


def infer_root_cause(
    rum_events: list[FrontendRumEvent],
    execution_logs: list[ExecutionLog],
) -> RootCauseReport:
    js_errors = [event for event in rum_events if event.type == "js_error"]
    resource_errors = [event for event in rum_events if event.type == "resource_error"]
    slow_vitals = [
        event
        for event in rum_events
        if event.type in {"web_vital", "custom_timing"} and _is_slow_frontend_metric(event)
    ]
    retry_logs = [log for log in execution_logs if log.event_type == "tool_retry"]

    if js_errors:
        first = js_errors[0]
        message = str(first.metadata.get("message") or first.name)
        return RootCauseReport(
            category="frontend_error",
            summary="JavaScript runtime errors are the strongest failure signal.",
            evidence=[f"{first.name}: {message}", f"{len(js_errors)} JS error event(s)"],
            recommendation=(
                "Add a null/shape guard around the failing code path, map the stack to source, "
                "and add a regression test for the affected UI state."
            ),
        )
    if resource_errors:
        first = resource_errors[0]
        return RootCauseReport(
            category="frontend_resource",
            summary="Resource loading failures can break rendering or inflate page latency.",
            evidence=[f"{first.name} failed at {first.url or 'unknown url'}"],
            recommendation=(
                "Check asset URL generation, CDN/cache headers, and deployment integrity for the "
                "failed resource."
            ),
        )
    if slow_vitals:
        worst = max(slow_vitals, key=lambda event: event.value)
        return RootCauseReport(
            category="frontend_performance",
            summary="Frontend performance metrics crossed APM thresholds.",
            evidence=[f"{worst.name}={worst.value:g} at {worst.url or 'unknown page'}"],
            recommendation=(
                "Inspect render-blocking resources, long tasks, image sizing, and data waterfall "
                "for the affected route."
            ),
        )
    if retry_logs:
        return RootCauseReport(
            category="backend_retry",
            summary="Backend tool retry activity is the dominant reliability signal.",
            evidence=[f"{len(retry_logs)} tool retry event(s)"],
            recommendation="Group retries by tool_call_id and fix the first failing tool input or dependency.",
        )
    return RootCauseReport(
        category="normal",
        summary="No dominant incident signature was detected.",
        evidence=[],
        recommendation="Keep collecting RUM, execution logs, and patrol checks to establish baseline.",
    )


def build_observability_snapshot(
    rum_events: list[FrontendRumEvent],
    execution_logs: list[ExecutionLog],
) -> ObservabilitySnapshot:
    values_by_metric: dict[str, list[float]] = defaultdict(list)
    errors: Counter[str] = Counter()
    for event in rum_events:
        if event.type in {"web_vital", "custom_timing"}:
            values_by_metric[event.name].append(event.value)
        if event.type == "js_error":
            errors[event.name] += 1
        if event.type == "resource_error":
            errors[f"resource:{event.name}"] += 1

    anomalies: list[AnomalySignal] = []
    for metric, values in values_by_metric.items():
        anomalies.extend(detect_anomalies(values, metric=metric))

    durations = [
        float(log.duration_ms)
        for log in execution_logs
        if log.duration_ms is not None
    ]
    return ObservabilitySnapshot(
        frontend=FrontendObservabilitySummary(
            total_events=len(rum_events),
            error_count=sum(
                1 for event in rum_events if event.type in {"js_error", "resource_error"}
            ),
            resource_error_count=sum(1 for event in rum_events if event.type == "resource_error"),
            web_vitals={
                metric: {
                    "avg": round(mean(values), 2),
                    "p75": _percentile(sorted(values), 75),
                    "p95": _percentile(sorted(values), 95),
                    "count": float(len(values)),
                }
                for metric, values in values_by_metric.items()
            },
            top_errors=[
                {"name": name, "count": count}
                for name, count in errors.most_common(5)
            ],
        ),
        backend=BackendObservabilitySummary(
            total_events=len(execution_logs),
            tool_errors=sum(
                1
                for log in execution_logs
                if log.event_type == "tool" and log.status == "failed"
            ),
            tool_retries=sum(1 for log in execution_logs if log.event_type == "tool_retry"),
            p95_duration_ms=_percentile(sorted(durations), 95) if durations else None,
        ),
        anomalies=anomalies,
        root_cause=infer_root_cause(rum_events, execution_logs),
    )


def _is_slow_frontend_metric(event: FrontendRumEvent) -> bool:
    thresholds = {
        "LCP": 2500,
        "FCP": 1800,
        "INP": 200,
        "CLS": 0.1,
        "TTFB": 800,
    }
    return event.value > thresholds.get(event.name.upper(), 1000)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0
    import math

    rank = max(1, math.ceil(percentile / 100 * len(sorted_values)))
    return sorted_values[min(rank - 1, len(sorted_values) - 1)]


# ── OTEL telemetry converters ─────────────────────────────────────────────


def _jaeger_tag(span: dict[str, Any], key: str) -> Any | None:
    """Extract a tag value from a Jaeger span by key name."""
    for tag in span.get("tags", []) or []:
        if isinstance(tag, dict) and tag.get("key") == key:
            return tag.get("value")
    return None


def _jaeger_has_tag(span: dict[str, Any], key: str, value: Any = None) -> bool:
    """Check if a Jaeger span has a tag with an optional value."""
    actual = _jaeger_tag(span, key)
    if value is not None:
        return actual == value
    return actual is not None


def _span_duration_ms(span: dict[str, Any]) -> float:
    """Convert Jaeger span duration (microseconds) to milliseconds."""
    return float(span.get("duration", 0)) / 1000.0


def from_jaeger_trace(trace_data: dict[str, Any]) -> list[FrontendRumEvent]:
    """Convert a Jaeger trace's spans into a list of :class:`FrontendRumEvent`.

    Mapping rules:
    - Spans with ``http.status_code >= 400`` or ``error=true`` → ``js_error``
    - Spans with ``http.method`` → ``web_vital`` (HTTP serving spans)
    - Spans with ``rpc.method`` → ``custom_timing`` (gRPC spans)
    - All others → ``custom_timing``
    - ``duration`` (μs) → ``value`` (ms)
    """
    events: list[FrontendRumEvent] = []
    trace_id = trace_data.get("traceID", "")
    for span in trace_data.get("spans", []) or []:
        duration_ms = _span_duration_ms(span)
        http_target = _jaeger_tag(span, "http.target") or ""
        http_url_tag = _jaeger_tag(span, "http.url") or ""
        http_status = _jaeger_tag(span, "http.status_code")
        is_error = _jaeger_has_tag(span, "error", True)
        is_http_error = isinstance(http_status, int) and http_status >= 400
        has_http_method = _jaeger_has_tag(span, "http.method")
        has_rpc_method = _jaeger_has_tag(span, "rpc.method")

        if is_error or is_http_error:
            event_type: RumEventType = "js_error"
        elif has_http_method:
            event_type = "web_vital"
        elif has_rpc_method:
            event_type = "custom_timing"
        else:
            event_type = "custom_timing"

        events.append(
            FrontendRumEvent(
                type=event_type,
                name=str(span.get("operationName", "")),
                value=round(duration_ms, 3),
                url=http_target or http_url_tag or None,
                trace_id=str(trace_id),
                timestamp=str(span.get("startTime", "")),
                metadata={
                    "span_id": str(span.get("spanID", "")),
                    "http_status_code": http_status,
                    "rpc_service": _jaeger_tag(span, "rpc.service"),
                    "rpc_method": _jaeger_tag(span, "rpc.method"),
                },
            )
        )
    return events


def from_jaeger_trace_to_logs(trace_data: dict[str, Any]) -> list[ExecutionLog]:
    """Convert a Jaeger trace's spans into a list of :class:`ExecutionLog`.

    Mapping rules:
    - Spans become ``ExecutionLog`` with ``event_type="tool"``
    - ``duration`` (μs) → ``duration_ms``
    - Spans with errors → ``status="failed"``, otherwise ``status="completed"``
    """
    logs: list[ExecutionLog] = []
    trace_id = trace_data.get("traceID", "")
    for span in trace_data.get("spans", []) or []:
        duration_ms = int(_span_duration_ms(span))
        http_status = _jaeger_tag(span, "http.status_code")
        is_error = _jaeger_has_tag(span, "error", True)
        is_http_error = isinstance(http_status, int) and http_status >= 400
        status: str = "failed" if (is_error or is_http_error) else "completed"
        log = ExecutionLog(
            id=0,
            created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
            thread_id=trace_id,
            run_id=str(span.get("spanID", "")),
            parent_id=None,
            event_type="tool",
            status=status,  # type: ignore[arg-type]
            name=str(span.get("operationName", "")),
            input={
                "http_method": _jaeger_tag(span, "http.method"),
                "http_url": _jaeger_tag(span, "http.url"),
                "rpc_method": _jaeger_tag(span, "rpc.method"),
            },
            output={},
            error={"http_status_code": http_status} if (is_error or is_http_error) else {},
            duration_ms=duration_ms,
            token_usage={},
            metadata={
                "span_id": str(span.get("spanID", "")),
                "trace_id": trace_id,
                "rpc_service": _jaeger_tag(span, "rpc.service"),
            },
        )
        logs.append(log)
    return logs


def _prometheus_metric_name(result: dict[str, Any]) -> str | None:
    """Extract the ``__name__`` label from a Prometheus result metric dict."""
    metric = result.get("metric", {}) if isinstance(result, dict) else {}
    return metric.get("__name__") if isinstance(metric, dict) else None


def from_prometheus_metric(
    metric_name_hint: str,
    prometheus_result: dict[str, Any],
    *,
    metric_filter: dict[str, str] | None = None,
) -> list[ExecutionLog]:
    """Convert a Prometheus query result into a list of :class:`ExecutionLog`.

    Args:
        metric_name_hint: The metric name the caller intended to query
            (used as the ``name`` field in generated logs).
        prometheus_result: The parsed JSON response from Prometheus.
        metric_filter: Optional label key-value pairs to filter results.

    Mapping rules:
    - Counter metrics (``_total`` suffix) → ``event_type="turn"``
    - Histogram/summary metrics → ``event_type="tool"``
    - ``duration_ms`` derived from the value field when numeric
    """
    logs: list[ExecutionLog] = []
    data = prometheus_result.get("data", {}) if isinstance(prometheus_result, dict) else {}
    raw_results = data.get("result", []) or []

    # Handle scalar/string result types: result is [timestamp, "value"]
    # (vector/matrix results are lists of {metric: ..., value/values: ...} objects)
    if isinstance(raw_results, list) and len(raw_results) == 2 and not isinstance(raw_results[0], dict):
        try:
            scalar_value = float(raw_results[1])
        except (ValueError, TypeError):
            scalar_value = 0.0
        return [
            ExecutionLog(
                id=0,
                created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
                thread_id="otel-prometheus",
                run_id=None,
                parent_id=None,
                event_type="tool",  # type: ignore[arg-type]
                status="completed",
                name=str(metric_name_hint),
                input={"metric_name": metric_name_hint, "labels": {}},
                output={"value": scalar_value},
                error={},
                duration_ms=None,
                token_usage={},
                metadata={"metric_name": metric_name_hint, "result_type": "scalar"},
            )
        ]

    results: list[dict[str, Any]] = raw_results

    for idx, result in enumerate(results):
        metric_labels = result.get("metric", {}) if isinstance(result, dict) else {}
        if not isinstance(metric_labels, dict):
            continue

        # Apply optional label filter
        if metric_filter:
            if not all(
                metric_labels.get(key) == value for key, value in metric_filter.items()
            ):
                continue

        # Extract the actual metric name from labels (fall back to hint)
        actual_name = _prometheus_metric_name(result) or metric_name_hint

        # Determine event type
        is_counter = actual_name.endswith("_total") or actual_name.endswith("_count")
        event_type = "turn" if is_counter else "tool"

        # Extract route/service name from common label keys
        route = metric_labels.get("http_route") or metric_labels.get("rpc_method") or ""
        service = metric_labels.get("service_name") or metric_labels.get("rpc_service") or ""
        display_name = route or actual_name

        # Extract value — Prometheus returns either "value" (instant) or "values" (range)
        raw_value = result.get("value")
        if isinstance(raw_value, list) and len(raw_value) >= 2:
            raw_value = raw_value[1]
        elif raw_value is None:
            values = result.get("values", [])
            if isinstance(values, list) and values:
                last = values[-1]
                if isinstance(last, list) and len(last) >= 2:
                    raw_value = last[1]

        try:
            numeric_value = float(raw_value) if raw_value is not None else 0.0
        except (ValueError, TypeError):
            numeric_value = 0.0

        logs.append(
            ExecutionLog(
                id=idx,
                created_at=datetime.now(timezone.utc),  # type: ignore[arg-type]
                thread_id="otel-prometheus",
                run_id=None,
                parent_id=None,
                event_type=event_type,  # type: ignore[arg-type]
                status="completed",
                name=str(display_name),
                input={"metric_name": actual_name, "labels": metric_labels},
                output={"value": numeric_value},
                error={},
                duration_ms=int(numeric_value) if is_counter else None,
                token_usage={},
                metadata={
                    "service_name": service,
                    "metric_name": actual_name,
                    "rpc_method": metric_labels.get("rpc_method"),
                },
            )
        )

    return logs


# ── OTEL telemetry query helpers ─────────────────────────────────────────


DEFAULT_JAEGER_API_URL = ""
DEFAULT_PROMETHEUS_PROXY_URL = ""


def query_jaeger_traces(
    *,
    service: str,
    operation: str | None = None,
    lookback: str = "15m",
    limit: int = 10,
    min_duration_ms: int | None = None,
    max_duration_ms: int | None = None,
    api_url: str | None = None,
) -> dict[str, Any]:
    """Search Jaeger for traces matching the given criteria.

    Returns the parsed JSON response from the Jaeger API, or a dict with an
    ``"error"`` key on failure.
    """
    import os as _os
    import json as _json
    import urllib.error as _urllib_error
    import urllib.parse as _urllib_parse
    import urllib.request as _urllib_request

    base = (api_url or _os.getenv("OTEL_JAEGER_API_URL") or DEFAULT_JAEGER_API_URL).rstrip("/")
    params: dict[str, str] = {
        "service": service,
        "limit": str(max(1, limit)),
        "lookback": lookback or "15m",
    }
    if operation:
        params["operation"] = operation
    if min_duration_ms is not None:
        params["minDuration"] = f"{min_duration_ms}ms"
    if max_duration_ms is not None:
        params["maxDuration"] = f"{max_duration_ms}ms"

    url = f"{base}/traces?{_urllib_parse.urlencode(params)}"
    try:
        request = _urllib_request.Request(url, method="GET")
        request.add_header("Accept", "application/json")
        with _urllib_request.urlopen(request, timeout=15.0) as response:
            body = response.read().decode("utf-8")
            return _json.loads(body)
    except (OSError, _urllib_error.URLError) as exc:
        return {"error": str(exc), "url": url}
    except Exception as exc:
        return {"error": str(exc), "url": url}


def query_prometheus_metrics(
    *,
    promql: str,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Query Prometheus via the Grafana datasource proxy.

    Returns the parsed JSON response from Prometheus, or a dict with an
    ``"error"`` key on failure.
    """
    import os as _os
    import json as _json
    import urllib.error as _urllib_error
    import urllib.parse as _urllib_parse
    import urllib.request as _urllib_request

    base = (
        proxy_url
        or _os.getenv("OTEL_PROMETHEUS_PROXY_URL")
        or DEFAULT_PROMETHEUS_PROXY_URL
    ).rstrip("/")

    params: dict[str, str] = {"query": promql}
    url = f"{base}/query?{_urllib_parse.urlencode(params)}"
    try:
        request = _urllib_request.Request(url, method="GET")
        request.add_header("Accept", "application/json")
        with _urllib_request.urlopen(request, timeout=15.0) as response:
            body = response.read().decode("utf-8")
            return _json.loads(body)
    except (OSError, _urllib_error.URLError) as exc:
        return {"error": str(exc), "url": url}
    except Exception as exc:
        return {"error": str(exc), "url": url}
