from datetime import UTC, datetime, timedelta

from personal_assistant.api.schemas import ExecutionLog
from personal_assistant.observability.traces import (
    TraceContext,
    build_trace_view,
    context_from_config,
    redact_payload,
    trace_metadata,
)


def _log(
    log_id: int,
    *,
    trace_id: str,
    span_id: str,
    parent_id: str | None = None,
    name: str,
    event_type: str = "llm",
    status: str = "completed",
    duration_ms: int | None = None,
    total_tokens: int = 0,
    created_offset_ms: int = 0,
) -> ExecutionLog:
    return ExecutionLog(
        id=log_id,
        created_at=datetime(2026, 7, 14, tzinfo=UTC)
        + timedelta(milliseconds=created_offset_ms),
        thread_id="thread-1",
        run_id=span_id,
        parent_id=parent_id,
        event_type=event_type,
        status=status,
        name=name,
        duration_ms=duration_ms,
        token_usage={"total_tokens": total_tokens},
        metadata={"trace_id": trace_id, "span_id": span_id},
    )


def test_trace_context_child_preserves_trace_and_sets_parent() -> None:
    root = TraceContext.create("thread-1", metadata={"agent_mode": "single"})

    child = root.child(node="agent")

    assert child.trace_id == root.trace_id
    assert child.run_id == root.run_id
    assert child.parent_span_id == root.span_id
    assert child.span_id != root.span_id
    assert child.metadata == {"agent_mode": "single", "node": "agent"}


def test_trace_context_round_trips_through_runnable_config() -> None:
    root = TraceContext.create("thread-1")
    config = {"configurable": {"thread_id": "thread-1", "trace_context": root.to_dict()}}

    restored = context_from_config(config)

    assert restored == root
    assert trace_metadata(restored)["trace_id"] == root.trace_id
    assert trace_metadata(restored)["span_id"] == root.span_id


def test_redact_payload_hides_nested_secrets_and_truncates_text() -> None:
    value = redact_payload(
        {
            "api_key": "secret",
            "nested": {"password": "pw", "safe": "visible"},
            "text": "x" * 2100,
        }
    )

    assert value["api_key"] == "[REDACTED]"
    assert value["nested"]["password"] == "[REDACTED]"
    assert value["nested"]["safe"] == "visible"
    assert value["text"].endswith("…[truncated]")
    assert len(value["text"]) < 2100


def test_build_trace_view_reconstructs_children_and_aggregates() -> None:
    logs = [
        _log(
            1,
            trace_id="trace-1",
            span_id="root",
            name="user_turn",
            event_type="turn",
            duration_ms=120,
        ),
        _log(
            2,
            trace_id="trace-1",
            span_id="llm",
            parent_id="root",
            name="agent",
            duration_ms=70,
            total_tokens=42,
            created_offset_ms=10,
        ),
        _log(
            3,
            trace_id="trace-1",
            span_id="tool",
            parent_id="llm",
            name="query_metrics",
            event_type="tool",
            status="failed",
            duration_ms=30,
            created_offset_ms=20,
        ),
    ]

    view = build_trace_view(logs, "trace-1")

    assert view.summary.total_spans == 3
    assert view.summary.total_tokens == 42
    assert view.summary.error_count == 1
    assert view.summary.tool_calls == 1
    assert view.summary.duration_ms == 120
    assert view.roots[0].span.span_id == "root"
    assert view.roots[0].children[0].span.span_id == "llm"
    assert view.roots[0].children[0].children[0].span.span_id == "tool"


def test_build_trace_view_marks_orphaned_spans() -> None:
    view = build_trace_view(
        [
            _log(
                1,
                trace_id="trace-1",
                span_id="orphan",
                parent_id="missing",
                name="tool",
            )
        ],
        "trace-1",
    )

    assert view.roots[0].orphaned is True
    assert view.roots[0].span.span_id == "orphan"


def test_build_trace_view_merges_started_and_completed_lifecycle_rows() -> None:
    view = build_trace_view(
        [
            _log(
                1,
                trace_id="trace-1",
                span_id="root",
                name="user_turn",
                event_type="turn",
                status="started",
            ),
            _log(
                2,
                trace_id="trace-1",
                span_id="root",
                name="user_turn",
                event_type="turn",
                status="completed",
                duration_ms=90,
                created_offset_ms=90,
            ),
        ],
        "trace-1",
    )

    assert view.summary.total_spans == 1
    assert view.roots[0].span.status == "completed"
    assert view.roots[0].span.duration_ms == 90
