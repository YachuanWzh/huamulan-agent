from __future__ import annotations

from collections import Counter, defaultdict
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
        "FID": 100,
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
