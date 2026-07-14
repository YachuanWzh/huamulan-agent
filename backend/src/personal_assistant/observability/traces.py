from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


_REDACTED = "[REDACTED]"
_TRUNCATED = "…[truncated]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "refresh_token",
    "secret",
    "token",
}


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    run_id: str
    span_id: str
    parent_span_id: str | None
    thread_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        thread_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> TraceContext:
        return cls(
            trace_id=uuid4().hex,
            run_id=uuid4().hex,
            span_id=uuid4().hex,
            parent_span_id=None,
            thread_id=thread_id,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TraceContext:
        return cls(
            trace_id=str(value["trace_id"]),
            run_id=str(value["run_id"]),
            span_id=str(value["span_id"]),
            parent_span_id=(
                str(value["parent_span_id"])
                if value.get("parent_span_id") is not None
                else None
            ),
            thread_id=str(value["thread_id"]),
            metadata=dict(value.get("metadata") or {}),
        )

    def child(self, **metadata: Any) -> TraceContext:
        return replace(
            self,
            span_id=uuid4().hex,
            parent_span_id=self.span_id,
            metadata={**self.metadata, **metadata},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceSpan(BaseModel):
    id: int
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    thread_id: str
    kind: str
    status: str
    name: str | None = None
    created_at: datetime
    duration_ms: int | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceNode(BaseModel):
    span: TraceSpan
    children: list[TraceNode] = Field(default_factory=list)
    orphaned: bool = False


class TraceSummary(BaseModel):
    trace_id: str
    total_spans: int = 0
    total_tokens: int = 0
    error_count: int = 0
    retry_count: int = 0
    tool_calls: int = 0
    duration_ms: int = 0
    slowest_spans: list[TraceSpan] = Field(default_factory=list)
    failed_spans: list[TraceSpan] = Field(default_factory=list)


class TraceView(BaseModel):
    summary: TraceSummary
    spans: list[TraceSpan] = Field(default_factory=list)
    roots: list[TraceNode] = Field(default_factory=list)


def context_from_config(config: Any) -> TraceContext | None:
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    raw = configurable.get("trace_context")
    if not isinstance(raw, dict):
        return None
    try:
        return TraceContext.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return None


def trace_metadata(context: TraceContext | None) -> dict[str, Any]:
    if context is None:
        return {}
    return {
        **redact_payload(context.metadata),
        "trace_id": context.trace_id,
        "agent_run_id": context.run_id,
        "span_id": context.span_id,
        "parent_span_id": context.parent_span_id,
    }


def redact_payload(value: Any, *, max_string_length: int = 2000) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if _is_sensitive_key(normalized):
                redacted[str(key)] = _REDACTED
            else:
                redacted[str(key)] = redact_payload(
                    item,
                    max_string_length=max_string_length,
                )
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_payload(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, str) and len(value) > max_string_length:
        keep = max(0, max_string_length - len(_TRUNCATED))
        return f"{value[:keep]}{_TRUNCATED}"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_trace_view(logs: list[Any], trace_id: str) -> TraceView:
    lifecycle_groups: dict[str, list[Any]] = {}
    for log in logs:
        metadata = _dict_field(log, "metadata")
        if metadata.get("trace_id") != trace_id:
            continue
        span_id = str(metadata.get("span_id") or _field(log, "run_id") or f"log-{_field(log, 'id')}")
        lifecycle_groups.setdefault(span_id, []).append(log)

    spans = [_span_from_lifecycle(trace_id, span_id, rows) for span_id, rows in lifecycle_groups.items()]
    spans.sort(key=lambda span: (span.created_at, span.id))
    nodes = {span.span_id: TraceNode(span=span) for span in spans}
    roots: list[TraceNode] = []
    for span in spans:
        node = nodes[span.span_id]
        if span.parent_span_id and span.parent_span_id in nodes:
            nodes[span.parent_span_id].children.append(node)
        else:
            node.orphaned = bool(span.parent_span_id)
            roots.append(node)
    for node in nodes.values():
        node.children.sort(key=lambda child: (child.span.created_at, child.span.id))

    failed = [span for span in spans if span.status in {"failed", "blocked"}]
    root_durations = [span.duration_ms or 0 for span in spans if span.parent_span_id is None]
    summary = TraceSummary(
        trace_id=trace_id,
        total_spans=len(spans),
        total_tokens=sum(_total_tokens(span.token_usage) for span in spans),
        error_count=len(failed),
        retry_count=sum(1 for span in spans if span.kind == "tool_retry"),
        tool_calls=sum(1 for span in spans if span.kind == "tool"),
        duration_ms=max(root_durations, default=0),
        slowest_spans=sorted(
            spans,
            key=lambda span: span.duration_ms or 0,
            reverse=True,
        )[:5],
        failed_spans=failed,
    )
    return TraceView(summary=summary, spans=spans, roots=roots)


def _span_from_lifecycle(trace_id: str, span_id: str, rows: list[Any]) -> TraceSpan:
    ordered = sorted(rows, key=lambda row: (_field(row, "created_at"), _field(row, "id")))
    first = ordered[0]
    last = ordered[-1]
    first_metadata = _dict_field(first, "metadata")
    last_metadata = _dict_field(last, "metadata")
    return TraceSpan(
        id=int(_field(last, "id") or 0),
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=(
            str(_field(last, "parent_id") or last_metadata.get("parent_span_id"))
            if (_field(last, "parent_id") or last_metadata.get("parent_span_id"))
            else None
        ),
        thread_id=str(_field(last, "thread_id") or ""),
        kind=str(_field(last, "event_type") or "unknown"),
        status=str(_field(last, "status") or "unknown"),
        name=_field(last, "name"),
        created_at=_field(first, "created_at"),
        duration_ms=_field(last, "duration_ms"),
        token_usage=_dict_field(last, "token_usage"),
        input=redact_payload(_dict_field(first, "input")),
        output=redact_payload(_dict_field(last, "output")),
        error=redact_payload(_dict_field(last, "error")),
        metadata=redact_payload({**first_metadata, **last_metadata}),
    )


def _is_sensitive_key(key: str) -> bool:
    return (
        key in _SENSITIVE_KEYS
        or key.endswith("_key")
        or key.endswith("_password")
        or key.endswith("_secret")
        or key.endswith("_token")
    )


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _dict_field(value: Any, name: str) -> dict[str, Any]:
    result = _field(value, name)
    return result if isinstance(result, dict) else {}


def _total_tokens(token_usage: dict[str, Any]) -> int:
    value = token_usage.get("total_tokens", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
