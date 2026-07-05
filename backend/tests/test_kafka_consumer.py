"""Tests for kafka_consumer.py OTLP JSON -> apm.py conversion."""
from personal_assistant.consumers.kafka_consumer import otlp_spans_to_jaeger_trace


# Sample OTLP JSON span batch as produced by OTel Collector kafka exporter
# with encoding: otlp_json — this is what kafka_consumer receives.
SAMPLE_OTLP_SPANS = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "frontend"}},
                    {"key": "service.namespace", "value": {"stringValue": "opentelemetry-demo"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "http"},
                    "spans": [
                        {
                            "traceId": "abcdef1234567890abcdef1234567890",
                            "spanId": "1234567890abcdef",
                            "parentSpanId": "",
                            "name": "GET /api/products",
                            "kind": 2,
                            "startTimeUnixNano": "1700000000000000000",
                            "endTimeUnixNano": "1700000000050000000",
                            "attributes": [
                                {"key": "http.method", "value": {"stringValue": "GET"}},
                                {"key": "http.target", "value": {"stringValue": "/api/products/123"}},
                                {"key": "http.status_code", "value": {"intValue": "200"}},
                            ],
                            "status": {"code": 1},
                        },
                        {
                            "traceId": "abcdef1234567890abcdef1234567891",
                            "spanId": "abcdef1234567890",
                            "parentSpanId": "",
                            "name": "POST /api/checkout",
                            "kind": 2,
                            "startTimeUnixNano": "1700000001000000000",
                            "endTimeUnixNano": "1700000001500000000",
                            "attributes": [
                                {"key": "http.method", "value": {"stringValue": "POST"}},
                                {"key": "http.status_code", "value": {"intValue": "500"}},
                                {"key": "error", "value": {"boolValue": True}},
                            ],
                            "status": {"code": 2},
                        },
                    ],
                }
            ],
        }
    ]
}


def test_otlp_spans_to_jaeger_trace_converts_basic_span():
    trace = otlp_spans_to_jaeger_trace(SAMPLE_OTLP_SPANS)
    assert trace["traceID"] == "abcdef1234567890abcdef1234567890"
    # Only spans from the first trace in the batch are returned
    assert len(trace["spans"]) == 1

    span = trace["spans"][0]
    assert span["operationName"] == "GET /api/products"
    assert span["spanID"] == "1234567890abcdef"
    assert span["duration"] == 50000  # 50ms in microseconds
    assert len(span["tags"]) >= 3  # http.method, http.target, http.status_code

    # Tags include service name from resource
    tag_keys = {t["key"] for t in span["tags"]}
    assert "service.name" in tag_keys


def test_otlp_spans_to_jaeger_trace_detects_error_span():
    """Error spans (OTLP status=2) get error=True tag."""
    # Create a batch where the first trace is the error one
    error_batch = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "http"},
                        "spans": [
                            {
                                "traceId": "deadbeef000000000000000000000001",
                                "spanId": "error001",
                                "parentSpanId": "",
                                "name": "POST /api/checkout",
                                "kind": 2,
                                "startTimeUnixNano": "1700000001000000000",
                                "endTimeUnixNano": "1700000001500000000",
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.status_code", "value": {"intValue": "500"}},
                                    {"key": "error", "value": {"boolValue": True}},
                                ],
                                "status": {"code": 2},
                            },
                        ],
                    }
                ],
            }
        ]
    }
    trace = otlp_spans_to_jaeger_trace(error_batch)
    error_span = trace["spans"][0]
    error_tags = {t["key"]: t["value"] for t in error_span["tags"]}
    assert error_tags.get("error") is True
    assert error_tags.get("http.status_code") == 500


def test_otlp_spans_to_jaeger_empty_returns_empty_dict():
    result = otlp_spans_to_jaeger_trace({"resourceSpans": []})
    assert result == {"traceID": "", "spans": []}


def test_otlp_spans_multi_trace_returns_first():
    """When batch has multiple traces, return the first one's spans only."""
    result = otlp_spans_to_jaeger_trace(SAMPLE_OTLP_SPANS)
    # Two different traceIds in SAMPLE → first trace's ID and its spans
    assert result["traceID"] == "abcdef1234567890abcdef1234567890"
