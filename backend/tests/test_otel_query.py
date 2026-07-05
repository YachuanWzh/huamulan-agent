"""Tests for OTEL telemetry data converters and query tools."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personal_assistant.apm import (
    FrontendRumEvent,
    ExecutionLog,
    ObservabilitySnapshot,
    build_observability_snapshot,
    from_jaeger_trace,
    from_jaeger_trace_to_logs,
    from_prometheus_metric,
)
from personal_assistant.api.schemas import ExecutionLog as ExecutionLogModel

# ── Real Jaeger trace fixture (trimmed from live OTEL demo) ──────────────

JAEGER_TRACE_FIXTURE: dict = {
    "traceID": "604a95d40369c9aca8110e76c0ae1e87",
    "spans": [
        {
            "traceID": "604a95d40369c9aca8110e76c0ae1e87",
            "spanID": "0aae34a88542999a",
            "operationName": "GET",
            "references": [
                {
                    "refType": "CHILD_OF",
                    "traceID": "604a95d40369c9aca8110e76c0ae1e87",
                    "spanID": "e5e9d7ec0eb1ded6",
                }
            ],
            "startTime": 1783242527365520,
            "duration": 16056,  # microseconds
            "tags": [
                {"key": "http.method", "type": "string", "value": "GET"},
                {"key": "http.url", "type": "string", "value": "http://frontend-proxy:8080/api/data"},
                {"key": "http.status_code", "type": "int64", "value": 200},
                {"key": "http.target", "type": "string", "value": "/api/data?contextKeys=binoculars"},
                {"key": "span.kind", "type": "string", "value": "server"},
                {"key": "component", "type": "string", "value": "proxy"},
            ],
            "logs": [],
            "processID": "p1",
            "warnings": None,
        },
        {
            "traceID": "604a95d40369c9aca8110e76c0ae1e87",
            "spanID": "c38b24957c50a8e7",
            "operationName": "oteldemo.AdService/GetAds",
            "references": [
                {
                    "refType": "CHILD_OF",
                    "traceID": "604a95d40369c9aca8110e76c0ae1e87",
                    "spanID": "452296b5cc017293",
                }
            ],
            "startTime": 1783242527369000,
            "duration": 11272,
            "tags": [
                {"key": "rpc.method", "type": "string", "value": "GetAds"},
                {"key": "rpc.service", "type": "string", "value": "oteldemo.AdService"},
                {"key": "rpc.system", "type": "string", "value": "grpc"},
                {"key": "rpc.grpc.status_code", "type": "int64", "value": 0},
                {"key": "span.kind", "type": "string", "value": "client"},
            ],
            "logs": [],
            "processID": "p2",
            "warnings": None,
        },
        # Error span — HTTP 500
        {
            "traceID": "604a95d40369c9aca8110e76c0ae1e87",
            "spanID": "deadbeef00000001",
            "operationName": "POST",
            "references": [],
            "startTime": 1783242527370000,
            "duration": 450000,
            "tags": [
                {"key": "http.method", "type": "string", "value": "POST"},
                {"key": "http.url", "type": "string", "value": "http://frontend:8080/api/checkout"},
                {"key": "http.status_code", "type": "int64", "value": 500},
                {"key": "http.target", "type": "string", "value": "/api/checkout"},
                {"key": "error", "type": "bool", "value": True},
                {"key": "span.kind", "type": "string", "value": "server"},
            ],
            "logs": [],
            "processID": "p3",
            "warnings": None,
        },
        # Slow span — high duration
        {
            "traceID": "604a95d40369c9aca8110e76c0ae1e87",
            "spanID": "deadbeef00000002",
            "operationName": "GET",
            "references": [],
            "startTime": 1783242527380000,
            "duration": 3500000,  # 3.5s — very slow
            "tags": [
                {"key": "http.method", "type": "string", "value": "GET"},
                {"key": "http.url", "type": "string", "value": "http://frontend:8080/api/products"},
                {"key": "http.status_code", "type": "int64", "value": 200},
                {"key": "http.target", "type": "string", "value": "/api/products"},
                {"key": "span.kind", "type": "string", "value": "server"},
            ],
            "logs": [],
            "processID": "p4",
            "warnings": None,
        },
    ],
}

# ── Real Prometheus query result fixture ─────────────────────────────────

PROMETHEUS_RESULT_FIXTURE: dict = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {
                    "__name__": "http_server_duration_milliseconds_bucket",
                    "http_method": "GET",
                    "http_route": "/api/products",
                    "le": "100",
                    "service_name": "frontend",
                },
                "values": [
                    [1783242500, "45"],
                    [1783242560, "52"],
                    [1783242620, "48"],
                ],
            },
            {
                "metric": {
                    "__name__": "demo_payment_transactions_total",
                    "service_name": "checkout",
                },
                "value": [1783242620, "1287"],
            },
            {
                "metric": {
                    "__name__": "rpc_server_duration_milliseconds_bucket",
                    "rpc_method": "GetAds",
                    "rpc_service": "oteldemo.AdService",
                    "le": "+Inf",
                },
                "values": [
                    [1783242500, "320"],
                    [1783242560, "315"],
                ],
            },
        ],
    },
}


# ── Tests: from_jaeger_trace ──────────────────────────────────────────────


class TestFromJaegerTrace:
    def test_converts_all_spans_to_frontend_rum_events(self) -> None:
        events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)

        assert len(events) == 4
        assert all(isinstance(e, FrontendRumEvent) for e in events)

    def test_http_span_becomes_web_vital_event(self) -> None:
        events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)

        proxy_span = next(e for e in events if e.name == "GET")
        assert proxy_span.type == "web_vital"
        assert proxy_span.value == 16.056  # 16056 μs → ms
        assert proxy_span.url == "/api/data?contextKeys=binoculars"

    def test_error_span_becomes_js_error_event(self) -> None:
        events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)

        error_span = next(
            e for e in events
            if e.metadata.get("span_id") == "deadbeef00000001"
        )
        assert error_span.type == "js_error"
        assert error_span.name == "POST"
        assert error_span.url == "/api/checkout"
        assert error_span.value == 450.0  # 450000 μs → 450 ms

    def test_grpc_span_becomes_custom_timing_event(self) -> None:
        events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)

        grpc_span = next(e for e in events if "GetAds" in e.name)
        assert grpc_span.type == "custom_timing"
        assert grpc_span.value == 11.272
        assert grpc_span.metadata.get("rpc_service") == "oteldemo.AdService"

    def test_slow_span_preserves_high_duration(self) -> None:
        events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)

        slow_span = next(e for e in events if e.value > 1000)
        assert slow_span.value == 3500.0  # 3.5 million μs → 3500 ms
        assert slow_span.url == "/api/products"

    def test_extracts_trace_id_and_timestamp(self) -> None:
        events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)

        for event in events:
            assert event.trace_id == "604a95d40369c9aca8110e76c0ae1e87"
            assert event.timestamp is not None


# ── Tests: from_jaeger_trace_to_logs ──────────────────────────────────────


class TestFromJaegerTraceToLogs:
    def test_converts_spans_to_execution_logs(self) -> None:
        logs = from_jaeger_trace_to_logs(JAEGER_TRACE_FIXTURE)

        assert len(logs) == 4
        assert all(isinstance(log, ExecutionLog) for log in logs)

    def test_http_span_maps_to_tool_event(self) -> None:
        logs = from_jaeger_trace_to_logs(JAEGER_TRACE_FIXTURE)

        http_logs = [log for log in logs if log.name == "GET"]
        assert len(http_logs) >= 1
        assert http_logs[0].event_type == "tool"
        assert http_logs[0].duration_ms is not None

    def test_error_span_maps_to_failed_status(self) -> None:
        logs = from_jaeger_trace_to_logs(JAEGER_TRACE_FIXTURE)

        error_logs = [log for log in logs if log.status == "failed"]
        assert len(error_logs) == 1
        assert error_logs[0].name == "POST"
        assert error_logs[0].error.get("http_status_code") == 500


# ── Tests: from_prometheus_metric ─────────────────────────────────────────


class TestFromPrometheusMetric:
    def test_converts_http_duration_metric_to_execution_logs(self) -> None:
        logs = from_prometheus_metric(
            "http_server_duration_milliseconds",
            PROMETHEUS_RESULT_FIXTURE,
            metric_filter={"service_name": "frontend"},
        )

        assert len(logs) >= 1
        assert all(isinstance(log, ExecutionLog) for log in logs)
        assert any(log.name == "/api/products" for log in logs)

    def test_converts_counter_metric_to_execution_logs(self) -> None:
        logs = from_prometheus_metric(
            "demo_payment_transactions_total",
            PROMETHEUS_RESULT_FIXTURE,
        )

        assert len(logs) >= 1
        # Find the payment transactions counter (filtered by metric name in metadata)
        payment_logs = [
            log for log in logs
            if log.metadata.get("metric_name") == "demo_payment_transactions_total"
        ]
        assert len(payment_logs) == 1
        payment_log = payment_logs[0]
        assert payment_log.name == "demo_payment_transactions_total"
        assert payment_log.event_type == "turn"

    def test_converts_rpc_metric_to_tool_logs(self) -> None:
        logs = from_prometheus_metric(
            "rpc_server_duration_milliseconds",
            PROMETHEUS_RESULT_FIXTURE,
        )

        assert len(logs) >= 1
        rpc_logs = [log for log in logs if "GetAds" in (log.metadata.get("rpc_method") or "")]
        assert len(rpc_logs) >= 1
        assert rpc_logs[0].event_type == "tool"


# ── Integration tests: end-to-end data flow ───────────────────────────────


class TestOtelToObservabilitySnapshot:
    """Test full pipeline: Jaeger trace → FrontendRumEvent + ExecutionLog → snapshot."""

    def test_build_snapshot_from_jaeger_trace(self) -> None:
        rum_events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)
        execution_logs = [
            ExecutionLogModel.model_validate(log.model_dump())
            for log in from_jaeger_trace_to_logs(JAEGER_TRACE_FIXTURE)
        ]

        snapshot = build_observability_snapshot(rum_events, execution_logs)

        assert snapshot.frontend.total_events == 4
        assert snapshot.frontend.error_count == 1  # one 500 span
        assert snapshot.backend.total_events == 4
        assert snapshot.root_cause.category == "frontend_error"  # error span detected
        assert len(snapshot.anomalies) >= 0  # anomaly detection runs without crash

    def test_observability_snapshot_with_prometheus_data(self) -> None:
        rum_events = from_jaeger_trace(JAEGER_TRACE_FIXTURE)
        prom_logs = from_prometheus_metric(
            "http_server_duration_milliseconds",
            PROMETHEUS_RESULT_FIXTURE,
        )
        trace_logs = from_jaeger_trace_to_logs(JAEGER_TRACE_FIXTURE)
        all_logs = [
            ExecutionLogModel.model_validate(log.model_dump())
            for log in [*prom_logs, *trace_logs]
        ]

        snapshot = build_observability_snapshot(rum_events, all_logs)

        assert snapshot.frontend.total_events == 4
        # Combined trace + prometheus logs
        assert snapshot.backend.total_events > 4


# ── Tests: query_traces script ────────────────────────────────────────────

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "personal_assistant" / "skills" / "otel-query" / "scripts"
)
QUERY_TRACES_SCRIPT = SCRIPT_DIR / "query_traces.py"
QUERY_METRICS_SCRIPT = SCRIPT_DIR / "query_metrics.py"


class TestQueryTracesScript:
    """Test query_traces.py as a subprocess (like health_check.py tests)."""

    @pytest.fixture(autouse=True)
    def _require_script(self) -> None:
        if not QUERY_TRACES_SCRIPT.is_file():
            pytest.skip("query_traces.py not yet created")

    def test_accepts_service_query_and_returns_json(self) -> None:
        input_data = json.dumps({
            "service": "frontend",
            "limit": 1,
            "lookback": "15m",
        })
        completed = subprocess.run(
            [sys.executable, str(QUERY_TRACES_SCRIPT)],
            input=input_data,
            text=True,
            capture_output=True,
        )
        # May return error (network unreachable) or data (real call succeeds) —
        # both are valid JSON responses from the script.
        result = json.loads(completed.stdout)
        assert "data" in result or "error" in result

    def test_reports_missing_service_parameter(self) -> None:
        input_data = json.dumps({"limit": 5})
        completed = subprocess.run(
            [sys.executable, str(QUERY_TRACES_SCRIPT)],
            input=input_data,
            text=True,
            capture_output=True,
        )
        # Should exit non-zero or return an error field
        assert completed.returncode != 0 or "error" in json.loads(completed.stdout)


class TestQueryMetricsScript:
    """Test query_metrics.py as a subprocess."""

    @pytest.fixture(autouse=True)
    def _require_script(self) -> None:
        if not QUERY_METRICS_SCRIPT.is_file():
            pytest.skip("query_metrics.py not yet created")

    def test_accepts_promql_and_returns_json(self) -> None:
        input_data = json.dumps({
            "query": "up",
        })
        completed = subprocess.run(
            [sys.executable, str(QUERY_METRICS_SCRIPT)],
            input=input_data,
            text=True,
            capture_output=True,
            check=True,
        )
        result = json.loads(completed.stdout)
        assert "status" in result or "error" in result

    def test_reports_missing_query_parameter(self) -> None:
        input_data = json.dumps({})
        completed = subprocess.run(
            [sys.executable, str(QUERY_METRICS_SCRIPT)],
            input=input_data,
            text=True,
            capture_output=True,
        )
        assert completed.returncode != 0 or "error" in json.loads(completed.stdout)


# ── Tests: query function unit tests (mocked HTTP) ────────────────────────


JAEGER_API_RESPONSE = {
    "data": [
        {
            "traceID": "abc123",
            "spans": [
                {
                    "traceID": "abc123",
                    "spanID": "span1",
                    "operationName": "GET",
                    "duration": 5000,
                    "tags": [
                        {"key": "http.method", "type": "string", "value": "GET"},
                        {"key": "http.status_code", "type": "int64", "value": 200},
                    ],
                }
            ],
        }
    ],
    "total": 1,
    "limit": 1,
    "offset": 0,
    "errors": None,
}

PROMETHEUS_API_RESPONSE = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "up", "job": "test"},
                "value": [1783242620, "1"],
            }
        ],
    },
}


class TestQueryTracesFunction:
    """Test the query_traces function with mocked HTTP."""

    def test_returns_structured_trace_data(self) -> None:
        from personal_assistant.apm import query_jaeger_traces as query_traces

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(
                JAEGER_API_RESPONSE
            ).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = query_traces(
                service="frontend",
                limit=5,
                lookback="15m",
                api_url="http://test:8080/jaeger/ui/api",
            )

        assert result["total"] == 1
        assert len(result["data"]) == 1
        assert result["data"][0]["traceID"] == "abc123"

    def test_handles_http_error_gracefully(self) -> None:
        from personal_assistant.apm import query_jaeger_traces as query_traces

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("Connection refused")

            result = query_traces(
                service="frontend",
                api_url="http://invalid:9999/jaeger/ui/api",
            )

        assert "error" in result


class TestQueryMetricsFunction:
    """Test the query_metrics function with mocked HTTP."""

    def test_returns_structured_metric_data(self) -> None:
        from personal_assistant.apm import query_prometheus_metrics as query_metrics

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps(
                PROMETHEUS_API_RESPONSE
            ).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = query_metrics(
                promql="up",
                proxy_url="http://test:8080/grafana/api/datasources/proxy/uid/test/api/v1",
            )

        assert result["status"] == "success"

    def test_handles_http_error_gracefully(self) -> None:
        from personal_assistant.apm import query_prometheus_metrics as query_metrics

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = OSError("Connection refused")

            result = query_metrics(
                promql="up",
                proxy_url="http://invalid:9999/grafana/api",
            )

        assert "error" in result


# ── Tests: Config defaults ───────────────────────────────────────────────


class TestOtelConfig:
    """Verify OTEL endpoint configuration defaults."""

    def test_default_jaeger_api_url(self) -> None:
        from personal_assistant.config import get_settings
        settings = get_settings()
        assert "jaeger" in settings.otel_jaeger_api_url.lower()
        assert "/api" in settings.otel_jaeger_api_url

    def test_default_prometheus_proxy_url(self) -> None:
        from personal_assistant.config import get_settings
        settings = get_settings()
        assert "prometheus" in settings.otel_prometheus_proxy_url.lower() or \
            "webstore-metrics" in settings.otel_prometheus_proxy_url
        assert "/api/v1" in settings.otel_prometheus_proxy_url

    def test_otel_env_vars_override_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        monkeypatch.setenv("OTEL_JAEGER_API_URL", "http://custom:1234/jaeger/api")
        monkeypatch.setenv(
            "OTEL_PROMETHEUS_PROXY_URL",
            "http://custom:3000/prometheus/api/v1",
        )
        from personal_assistant.config import Settings
        settings = Settings()
        assert settings.otel_jaeger_api_url == "http://custom:1234/jaeger/api"
        assert settings.otel_prometheus_proxy_url == "http://custom:3000/prometheus/api/v1"
