from personal_assistant.apm import (
    FrontendRumEvent,
    build_observability_snapshot,
    detect_anomalies,
    infer_root_cause,
)
from personal_assistant.api.schemas import ExecutionLog


def _log(**overrides):
    data = {
        "id": 1,
        "created_at": "2026-07-03T01:00:00Z",
        "thread_id": "thread-1",
        "run_id": None,
        "parent_id": None,
        "event_type": "tool",
        "status": "completed",
        "name": "lookup",
        "input": {},
        "output": {},
        "error": {},
        "duration_ms": 50,
        "token_usage": {},
        "metadata": {},
    }
    data.update(overrides)
    return ExecutionLog.model_validate(data)


def test_detect_anomalies_flags_iqr_and_zscore_outliers() -> None:
    anomalies = detect_anomalies([100, 102, 98, 101, 99, 1200], metric="lcp")

    assert anomalies
    assert anomalies[0].metric == "lcp"
    assert anomalies[0].value == 1200
    assert anomalies[0].method in {"iqr", "zscore"}


def test_root_cause_prioritizes_js_errors_over_slow_metrics() -> None:
    report = infer_root_cause(
        [
            FrontendRumEvent(
                type="js_error",
                name="TypeError",
                value=1,
                url="/chat",
                metadata={"message": "Cannot read properties of undefined"},
            ),
            FrontendRumEvent(type="web_vital", name="LCP", value=3800, url="/chat"),
        ],
        [_log(event_type="tool_retry", status="retrying", name="lookup")],
    )

    assert report.category == "frontend_error"
    assert "TypeError" in report.evidence[0]
    assert "guard" in report.recommendation.lower()


def test_observability_snapshot_combines_rum_logs_anomalies_and_rca() -> None:
    snapshot = build_observability_snapshot(
        [
            FrontendRumEvent(type="web_vital", name="LCP", value=1200, url="/chat"),
            FrontendRumEvent(type="web_vital", name="LCP", value=4300, url="/chat"),
            FrontendRumEvent(type="resource_error", name="script", value=1, url="/assets/app.js"),
        ],
        [
            _log(id=1, duration_ms=75),
            _log(id=2, event_type="tool_retry", status="retrying", duration_ms=90),
        ],
    )

    assert snapshot.frontend.total_events == 3
    assert snapshot.frontend.error_count == 1
    assert snapshot.frontend.web_vitals["LCP"]["p95"] == 4300
    assert snapshot.backend.tool_retries == 1
    assert snapshot.root_cause.category in {"frontend_resource", "frontend_performance"}
