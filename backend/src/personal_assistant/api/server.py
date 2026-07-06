import asyncio
import logging
import sys
import json
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from personal_assistant.agent.agent import (
    build_skill_router_components,
)
from personal_assistant.agent.harness import AgentHarness
from personal_assistant.agent.harness import scan_prompt_guard, scan_prompt_guard_with_llm, scan_tool_guard
from personal_assistant.agent.llm import build_llm
from personal_assistant.agent.router import route_skill_names_with_trace
from personal_assistant.apm import (
    FrontendRumEvent,
    ObservabilitySnapshot,
    build_observability_snapshot,
)
from personal_assistant.api.schemas import (
    ApprovalBatchDecision,
    ApprovalDecision,
    AuditEvent,
    AlertManagerWebhook,
    ChatRequest,
    ChatResponse,
    ClearThreadsResponse,
    DeleteThreadResponse,
    ExecutionLog,
    ExecutionSummary,
    LLMConfig,
    ReplayResponse,
    SkillInfo,
    SkillEvaluationDataset,
    SkillEvaluationSummary,
    SkillEvaluationResetResponse,
    SkillEvaluationRunRequest,
    SkillEvaluationRunResponse,
    SkillEvaluationSnapshot,
    ThreadSummary,
    ToolCallApproval,
    ToolError,
)
from personal_assistant.cache import build_cache
from personal_assistant.config import get_settings
from personal_assistant.memory.cached import CachedPostgresMemory
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.models import (
    AgentEvaluationCase,
    RoutingMetrics,
    RuntimeSkillMetrics,
    SkillEvaluationReport,
    SkillEvaluationResult,
)
from personal_assistant.skills.evaluation.report import (
    _weighted_score,
    score_static_metrics,
)
from personal_assistant.skills.evaluation.models import GoldenSkillCase
from personal_assistant.skills.evaluation.diagnostics import build_case_evaluation_detail
from personal_assistant.skills.evaluation.judge import evaluate_case_with_judge
from personal_assistant.api.feishu_notifier import get_feishu_notifier
from personal_assistant.skills.evaluation.quality import (
    evaluate_answer_cases,
    evaluate_hallucination_cases,
    evaluate_tool_cases,
)
from personal_assistant.skills.evaluation.offline import evaluate_multi_agent_intent_cases
from personal_assistant.skills.evaluation.report import evaluate_skill_registry
from personal_assistant.skills.evaluation.safety import evaluate_safety_cases
from personal_assistant.skills.evaluation.static import evaluate_static_skill
from personal_assistant.tracing import build_langfuse_callback


# Make cache hit/miss events visible regardless of how the server is started.
class _CacheFormatter(logging.Formatter):
    """Render extra fields (event, namespace, duration_ms) when present."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", "")
        namespace = getattr(record, "namespace", "")
        duration_ms = getattr(record, "duration_ms", "")
        parts = [
            self.formatTime(record, "%H:%M:%S"),
            f"{record.levelname:<8}",
            f"[{event}]" if event else f"[{record.name}]",
        ]
        if namespace:
            parts.append(f"{namespace:<25}")
        if duration_ms:
            parts.append(f"{duration_ms}ms")
        return "  ".join(parts)


class _CheckpointFormatter(logging.Formatter):
    """Render checkpoint IDs from logger extra fields when present."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, "%H:%M:%S"),
            f"{record.levelname:<8}",
            f"[{record.name}]",
            record.getMessage(),
        ]
        for field in ("thread_id", "checkpoint_id", "source", "write_node", "task_id", "ttl_seconds"):
            value = getattr(record, field, "")
            if value not in ("", None):
                parts.append(f"{field}={value}")
        return "  ".join(parts)


def _ensure_stream_logger(
    logger_name: str,
    *,
    level: int,
    formatter: logging.Formatter,
) -> None:
    logger = logging.getLogger(logger_name)
    if not any(
        isinstance(handler, logging.StreamHandler)
        and getattr(handler, "_personal_assistant_handler", False)
        for handler in logger.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        handler._personal_assistant_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


_ensure_stream_logger(
    "personal_assistant.cache",
    level=logging.DEBUG,
    formatter=_CacheFormatter(),
)
_ensure_stream_logger(
    "personal_assistant.agent.router",
    level=logging.INFO,
    formatter=logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
        datefmt="%H:%M:%S",
    ),
)
_ensure_stream_logger(
    "personal_assistant.checkpoint",
    level=logging.INFO,
    formatter=_CheckpointFormatter(),
)


settings = get_settings()
registry = SkillRegistry(settings.skills_dir)
cache = build_cache(settings)
postgres_memory = PostgresMemory(
    settings.database_url,
    redis_url=settings.redis_url,
    checkpoint_ttl_seconds=settings.checkpoint_ttl_seconds,
    checkpoint_pg_cleanup_enabled=settings.checkpoint_pg_cleanup_enabled,
    checkpoint_redis_lru_enabled=settings.checkpoint_redis_lru_enabled,
    checkpoint_redis_maxmemory_policy=settings.checkpoint_redis_maxmemory_policy,
    checkpoint_skip_nodes=settings.checkpoint_skip_nodes,
)
memory = CachedPostgresMemory(
    postgres_memory,
    cache,
    default_ttl_seconds=settings.cache_default_ttl_seconds,
    log_ttl_seconds=settings.cache_log_ttl_seconds,
)
langfuse_callback = build_langfuse_callback(settings)
frontend_rum_events: list[FrontendRumEvent] = []

# ── OTEL Push: in-memory alert store + SSE broadcast ──────────────
_otel_alerts: deque[dict] = deque(maxlen=200)
_otel_alert_subscribers: list[asyncio.Queue] = []
_active_rca_tasks: set[asyncio.Task] = set()  # Track background RCA tasks to prevent GC warnings


async def _broadcast_otel_alert(alert_data: dict) -> None:
    """Push an alert to all active SSE subscribers."""
    stale: list[asyncio.Queue] = []
    for queue in _otel_alert_subscribers:
        try:
            queue.put_nowait(alert_data)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        try:
            _otel_alert_subscribers.remove(queue)
        except ValueError:
            pass


def _find_alert_by_thread_id(thread_id: str) -> dict | None:
    """Find an alert in the in-memory store by its RCA thread ID."""
    for alert_data in _otel_alerts:
        if alert_data.get("rca_thread_id") == thread_id:
            return alert_data
    return None


def _update_alert_rca_status(alert_data: dict, **fields) -> None:
    """Update RCA tracking fields on an alert dict in-place."""
    for key, value in fields.items():
        alert_data[key] = value


def _build_rca_prompt(alert_data: dict) -> str:
    """Build the RCA prompt matching the frontend buildRcaPrompt format.

    References e2e golden cases in evaluation/golden/otel_push.jsonl
    (otel-push-001 through otel-push-005).
    """
    parts = [
        f"\U0001f6a8 {alert_data['level']} Alert received from OTEL push: **{alert_data['alert_name']}**",
        f"- Service: **{alert_data['service_name']}**",
        f"- Severity: {alert_data['severity']}",
        f"- Summary: {alert_data['summary']}",
    ]
    if alert_data.get("description"):
        parts.append(f"- Details: {alert_data['description']}")
    if alert_data.get("starts_at"):
        parts.append(f"- Alert started: {alert_data['starts_at']}")
    parts.extend([
        "",
        "Please run root cause analysis using the otel-query skill:",
        "1. Pull Jaeger traces for the affected service",
        "2. Query Prometheus for correlated metrics",
        "3. Identify the root cause and recommend fixes",
    ])
    return "\n".join(parts)


async def _trigger_rca_background(alert_data: dict) -> None:
    """Run RCA in background for a P0 alert with auto-approval for safe tools.

    Uses :func:`requires_rca_tool_approval` so that only dangerous
    operations (matching ``_TOOL_PATTERNS``) require human approval.
    All routine tools — query_traces, query_metrics, grep, safe bash
    commands — are auto-approved so RCA completes autonomously.
    """
    from personal_assistant.agent.harness import requires_rca_tool_approval

    alert_id = alert_data["id"]
    thread_id = f"rca-{alert_id}"
    _update_alert_rca_status(
        alert_data,
        rca_status="running",
        rca_thread_id=thread_id,
    )
    await _broadcast_otel_alert(alert_data)

    rca_result_text = None
    try:
        prompt = _build_rca_prompt(alert_data)
        response = await harness.run_user_turn(
            thread_id,
            prompt,
            agent_mode="single",
            requires_approval=requires_rca_tool_approval,
        )

        if response.status == "requires_approval":
            _update_alert_rca_status(
                alert_data,
                rca_status="blocked",
                rca_pending_approvals=[
                    a.model_dump() for a in response.approvals
                ],
            )
        else:
            _update_alert_rca_status(
                alert_data,
                rca_status="completed",
                rca_pending_approvals=None,
            )
            rca_result_text = _extract_rca_result_text(response)
    except Exception:
        logger.exception("RCA failed for alert %s", alert_id)
        _update_alert_rca_status(
            alert_data,
            rca_status="failed",
            rca_pending_approvals=None,
        )
    finally:
        await _broadcast_otel_alert(alert_data)
        # Push RCA result to Feishu for P0/P1 (skip P2/P3)
        if alert_data.get("level") in ("P0", "P1"):
            notifier = get_feishu_notifier()
            if notifier.enabled:
                _ = notifier.send_rca_result(
                    alert_data,
                    rca_result=rca_result_text,
                    status=alert_data.get("rca_status", "failed"),
                )


def _extract_rca_result_text(response) -> str | None:
    """Extract the RCA agent's final text response from agent harness output.

    The harness ``run_user_turn`` returns a :class:`ChatResponse` whose
    ``message`` field (singular) carries the final AI reply.  We also
    handle older or streaming return shapes that expose a ``messages``
    iterable (plural) so this helper works regardless of harness version.
    """
    try:
        # New harness ChatResponse — ``message`` is a plain str | None
        message = getattr(response, "message", None)
        if isinstance(message, str) and message.strip():
            return message.strip()

        # Fallback: older/streaming responses with a ``messages`` iterable
        messages = getattr(response, "messages", None)
        if messages and hasattr(messages, "__iter__"):
            for msg in reversed(list(messages)):
                if hasattr(msg, "content") and msg.content:
                    if hasattr(msg, "type") and msg.type == "ai":
                        return str(msg.content)
                    return str(msg.content)
    except Exception:
        logger.debug("Could not extract RCA result text", exc_info=True)
    return None


harness = AgentHarness(
    settings,
    registry,
    memory,
    callbacks=[langfuse_callback] if langfuse_callback else None,
    cache=cache,
)
logger = logging.getLogger(__name__)

# Quick evaluation uses full three-layer routing funnel: regex → vector retrieval (Qdrant/in-memory) → rerank → LLM judge
# We intentionally skip STARTUP-TIME Qdrant warmup/sync to make eval boot faster.
# Qdrant index will lazily perform necessary sync on first search automatically, so vector comparison and LLM judge work normally.
# Full end-to-end routing with long-term memory/cache is tested in e2e mode.
_full_router_kwargs = build_skill_router_components(
    settings,
    long_term_memory=None,
    cache=None,
)
quick_eval_router_kwargs = {
    k: v for k, v in _full_router_kwargs.items()
    if k not in ("long_term_memory", "cache", "memory_cache_ttl_seconds")
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await postgres_memory.start()
    registry.start_watching()
    # Production agent harness initializes its own routing components internally, no extra warmup needed here.
    # Quick eval skips startup Qdrant sync intentionally; sync happens lazily on first semantic search if needed.
    try:
        yield
    finally:
        registry.stop_watching()
        await cache.close()
        await postgres_memory.stop()


app = FastAPI(title="LangGraph Personal Assistant", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await harness.run_user_turn(
        request.thread_id,
        request.message,
        request.llm,
        agent_mode=request.agent_mode,
    )


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        harness.run_user_turn_stream(
            request.thread_id,
            request.message,
            request.llm,
            agent_mode=request.agent_mode,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/approve", response_model=ChatResponse)
async def approve(request: ApprovalDecision) -> ChatResponse:
    response = await harness.resume_after_approval(
        request.thread_id,
        request.approval_id,
        request.approved,
    )

    # Sync RCA alert status if this thread is an RCA thread
    alert_data = _find_alert_by_thread_id(request.thread_id)
    if alert_data is not None:
        if not request.approved:
            _update_alert_rca_status(alert_data, rca_status="failed", rca_pending_approvals=None)
        elif response.status == "requires_approval":
            _update_alert_rca_status(
                alert_data,
                rca_status="blocked",
                rca_pending_approvals=[
                    a.model_dump() for a in response.approvals
                ],
            )
        else:
            _update_alert_rca_status(alert_data, rca_status="completed", rca_pending_approvals=None)
        await _broadcast_otel_alert(alert_data)

    return response


@app.post("/api/approve/stream")
async def approve_stream(request: ApprovalDecision) -> StreamingResponse:
    return StreamingResponse(
        harness.resume_after_approval_stream(
            request.thread_id,
            request.approval_id,
            request.approved,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/approvals/stream")
async def approve_batch_stream(request: ApprovalBatchDecision) -> StreamingResponse:
    return StreamingResponse(
        harness.resume_after_approvals_stream(
            request.thread_id,
            [decision.model_dump() for decision in request.decisions],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/threads/{thread_id}/replay", response_model=ReplayResponse)
async def replay(thread_id: str) -> ReplayResponse:
    return ReplayResponse(thread_id=thread_id, states=await harness.replay(thread_id))


@app.get("/api/threads/{thread_id}/execution-logs", response_model=list[ExecutionLog])
async def list_execution_logs(thread_id: str, limit: int = 500) -> list[ExecutionLog]:
    return await harness.list_execution_logs(thread_id=thread_id, limit=limit)


@app.get("/api/threads/{thread_id}/execution-summary", response_model=ExecutionSummary)
async def execution_log_summary(thread_id: str) -> ExecutionSummary:
    return await harness.execution_log_summary(thread_id=thread_id)


@app.get("/api/threads/{thread_id}/pending-approvals", response_model=list[ToolCallApproval])
async def list_pending_approvals(thread_id: str) -> list[ToolCallApproval]:
    return [
        ToolCallApproval(**approval)
        for approval in await harness.list_pending_approvals(thread_id)
    ]


@app.get("/api/threads", response_model=list[ThreadSummary])
async def list_threads(limit: int = 100) -> list[ThreadSummary]:
    return await harness.list_threads(limit=limit)


@app.delete("/api/threads", response_model=ClearThreadsResponse)
async def clear_threads() -> ClearThreadsResponse:
    thread_ids = await harness.clear_threads()
    return ClearThreadsResponse(thread_ids=thread_ids, deleted=len(thread_ids))


@app.delete("/api/threads/{thread_id}", response_model=DeleteThreadResponse)
async def delete_thread(thread_id: str) -> DeleteThreadResponse:
    await harness.delete_thread(thread_id)
    return DeleteThreadResponse(thread_id=thread_id)


@app.get("/api/audit-events", response_model=list[AuditEvent])
async def list_audit_events(thread_id: str | None = None, limit: int = 100) -> list[AuditEvent]:
    return await harness.list_audit_events(thread_id=thread_id, limit=limit)


@app.get("/api/tool-errors", response_model=list[ToolError])
async def list_tool_errors(thread_id: str | None = None, limit: int = 100) -> list[ToolError]:
    return await harness.list_tool_errors(thread_id=thread_id, limit=limit)


@app.post("/api/observability/frontend/events", response_model=FrontendRumEvent)
async def record_frontend_rum_event(event: FrontendRumEvent) -> FrontendRumEvent:
    frontend_rum_events.append(event)
    del frontend_rum_events[:-1000]
    return event


@app.get("/api/observability/frontend/summary", response_model=ObservabilitySnapshot)
async def frontend_observability_summary(
    thread_id: str | None = None,
    limit: int = 500,
) -> ObservabilitySnapshot:
    logs = await harness.list_execution_logs(thread_id=thread_id, limit=limit) if thread_id else []
    return build_observability_snapshot(frontend_rum_events[-limit:], logs)


# ── Severity label → display level mapping ──────────────────────────
_SEVERITY_LEVEL_MAP: dict[str, str] = {
    "critical": "P0",
    "warning": "P1",
    "info": "P2",
    "none": "P3",
}


def _severity_to_level(severity: str) -> str:
    """Map an AlertManager severity label to a P0-P3 display level."""
    return _SEVERITY_LEVEL_MAP.get(severity, "P3")



@app.post("/api/otel/alerts")
async def handle_otel_alert(payload: AlertManagerWebhook):
    """Receive AlertManager webhook for P0-P3 alerts.

    All severity levels are accepted, stored in the in-memory alert
    deque, and broadcast via SSE. Only P0 (critical) triggers automatic
    RCA in the background.

    Level mapping:
      critical → P0 (auto-RCA)
      warning  → P1 (stored + SSE)
      info     → P2 (stored + SSE)
      none     → P3 (stored + SSE)
      other    → P3 (default fallback)
    """
    processed = 0
    for alert in payload.alerts:
        severity = alert.labels.get("severity", "")
        level = _severity_to_level(severity)

        service = alert.labels.get("service_name", "unknown")
        alert_name = alert.labels.get("alertname", "unknown")
        summary = alert.annotations.get("summary", "")
        starts_at = alert.starts_at

        logger.info(
            "OTEL alert received: level=%s severity=%s service=%s alert=%s summary=%s starts_at=%s",
            level,
            severity,
            service,
            alert_name,
            summary,
            starts_at,
        )

        # Store in memory for SSE broadcast and history
        alert_data = {
            "id": uuid4().hex[:12],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "level": level,
            "service_name": service,
            "alert_name": alert_name,
            "summary": summary,
            "description": alert.annotations.get("description", ""),
            "starts_at": starts_at,
            "status": alert.status,
            # RCA tracking fields — populated by background task for P0
            "rca_status": "pending",
            "rca_thread_id": None,
            "rca_pending_approvals": None,
        }
        _otel_alerts.appendleft(alert_data)
        await _broadcast_otel_alert(alert_data)

        # Push brief alert to Feishu for P0/P1 (skip P2/P3)
        if level in ("P0", "P1"):
            notifier = get_feishu_notifier()
            if notifier.enabled:
                _ = notifier.send_alert(alert_data)

        # P0 (critical): auto-trigger RCA in background with auto-approval
        if level == "P0":
            task = asyncio.create_task(_trigger_rca_background(alert_data))
            _active_rca_tasks.add(task)
            task.add_done_callback(_active_rca_tasks.discard)

        processed += 1

    return {"status": "accepted", "alerts": processed}


@app.get("/api/otel/alerts/history")
async def list_otel_alerts(limit: int = Query(default=50, le=200)) -> list[dict]:
    """Return recent P0/P1 alerts received via webhook."""
    return list(_otel_alerts)[:limit]


@app.get("/api/otel/alerts/stream")
async def stream_otel_alerts():
    """SSE endpoint for real-time OTEL alert notifications."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _otel_alert_subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                try:
                    alert_data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: alert\ndata: {json.dumps(alert_data)}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: ping\ndata: {json.dumps({'ts': datetime.now(timezone.utc).isoformat()})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _otel_alert_subscribers.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── OTEL RCA endpoints ───────────────────────────────────────────


@app.get("/api/otel/alerts/{alert_id}/rca/status")
async def get_otel_rca_status(alert_id: str):
    """Query RCA status for a specific alert.

    Returns ``rca_status``, ``rca_thread_id``, and ``rca_pending_approvals``
    for the given alert, or 404 if the alert is not found.
    """
    for alert_data in _otel_alerts:
        if alert_data.get("id") == alert_id:
            return {
                "alert_id": alert_id,
                "rca_status": alert_data.get("rca_status", "pending"),
                "rca_thread_id": alert_data.get("rca_thread_id"),
                "rca_pending_approvals": alert_data.get("rca_pending_approvals"),
            }
    raise HTTPException(status_code=404, detail=f"Alert not found: {alert_id}")


@app.post("/api/otel/alerts/{alert_id}/rca/approve")
async def approve_otel_rca(alert_id: str, request: ApprovalDecision):
    """Approve or deny a dangerous tool during P0 RCA.

    Resumes the RCA thread after the user's decision and broadcasts
    the updated alert status via SSE.
    """
    alert_data = None
    for item in _otel_alerts:
        if item.get("id") == alert_id:
            alert_data = item
            break

    if alert_data is None:
        raise HTTPException(status_code=404, detail=f"Alert not found: {alert_id}")

    rca_thread_id = alert_data.get("rca_thread_id")
    if not rca_thread_id:
        raise HTTPException(status_code=400, detail="Alert has no RCA thread")

    # Update approval decision in harness
    harness.decisions[request.approval_id] = request.approved

    # Resume the RCA thread
    response = await harness.resume_after_approval(
        rca_thread_id,
        request.approval_id,
        request.approved,
    )

    # Sync alert RCA status
    if not request.approved:
        _update_alert_rca_status(alert_data, rca_status="failed", rca_pending_approvals=None)
    elif response.status == "requires_approval":
        _update_alert_rca_status(
            alert_data,
            rca_status="blocked",
            rca_pending_approvals=[
                a.model_dump() for a in response.approvals
            ],
        )
    else:
        _update_alert_rca_status(alert_data, rca_status="completed", rca_pending_approvals=None)

    await _broadcast_otel_alert(alert_data)
    return response


@app.get("/api/skills", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    latest = {
        item.skill_name: item
        for item in await memory.list_latest_skill_evaluations()
    }
    return [
        _skill_info(skill, latest_evaluation=latest.get(skill.name))
        for skill in registry.skills.values()
    ]


@app.post("/api/skills/reload", response_model=list[SkillInfo])
async def reload_skills() -> list[SkillInfo]:
    latest = {
        item.skill_name: item
        for item in await memory.list_latest_skill_evaluations()
    }
    return [
        _skill_info(skill, latest_evaluation=latest.get(skill.name))
        for skill in registry.reload()
    ]


@app.get("/api/skills/evaluation/latest", response_model=list[SkillEvaluationSnapshot])
async def latest_skill_evaluations() -> list[SkillEvaluationSnapshot]:
    return await memory.list_latest_skill_evaluations()


@app.get("/api/skills/evaluation/history", response_model=list[SkillEvaluationSnapshot])
async def skill_evaluation_history(
    skill_name: str | None = None,
    limit: int = 100,
) -> list[SkillEvaluationSnapshot]:
    return await _list_skill_evaluation_history(
        memory,
        skill_name=skill_name,
        limit=limit,
    )


@app.get("/api/skills/evaluation/golden-datasets", response_model=list[SkillEvaluationDataset])
async def list_skill_evaluation_datasets(
    agent_mode: str = "single",
) -> list[SkillEvaluationDataset]:
    return _list_golden_datasets(agent_mode=agent_mode)


@app.delete("/api/skills/evaluation", response_model=SkillEvaluationResetResponse)
async def reset_skill_evaluations() -> SkillEvaluationResetResponse:
    return await _reset_skill_evaluations(memory)


@app.post("/api/skills/evaluation/run", response_model=SkillEvaluationRunResponse)
async def run_skill_evaluation(
    request: SkillEvaluationRunRequest,
) -> SkillEvaluationRunResponse:
    return await _run_skill_evaluation_and_persist(
        registry,
        memory,
        request.golden_path,
    )


@app.post("/api/skills/evaluation/run/stream")
async def run_skill_evaluation_stream(
    request: SkillEvaluationRunRequest,
) -> StreamingResponse:
    path = _resolve_golden_path(request.golden_path)

    async def event_stream():
        async for event in _iter_skill_evaluation_events(
            registry,
            memory,
            path,
            mode=request.evaluation_mode,
            agent_mode=request.agent_mode,
            harness=harness,
            judge_client=_build_evaluation_judge(settings)
            if request.evaluation_mode == "e2e"
            else None,
            judge_model=settings.evaluation_judge_model,
        ):
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _reset_skill_evaluations(memory) -> SkillEvaluationResetResponse:
    deleted = await memory.reset_skill_evaluation_results()
    return SkillEvaluationResetResponse(deleted=deleted, results=[])


async def _list_skill_evaluation_history(
    memory,
    skill_name: str | None = None,
    limit: int = 100,
) -> list[SkillEvaluationSnapshot]:
    return await memory.list_skill_evaluation_history(
        skill_name=skill_name,
        limit=limit,
    )


async def _run_skill_evaluation_and_persist(
    registry: SkillRegistry,
    memory,
    golden_path: str | None,
) -> SkillEvaluationRunResponse:
    path = _resolve_golden_path(golden_path)
    cases = _load_golden_cases(path)
    report = await evaluate_skill_registry(registry, cases=cases)
    source = f"golden:{path}"
    await memory.record_skill_evaluation_results(report, source=source)
    return SkillEvaluationRunResponse(
        source=source,
        results=await memory.list_latest_skill_evaluations(),
    )


async def _iter_skill_evaluation_events(
    registry: SkillRegistry,
    memory,
    golden_path: str | Path | None,
    *,
    mode: str = "quick",
    agent_mode: str = "single",
    harness: AgentHarness | None = None,
    judge_client=None,
    judge_model: str | None = None,
):
    path = _resolve_golden_path(golden_path)
    cases = _load_golden_cases(path)
    skills = list(registry.skills.values())
    total = len(cases)
    source = f"golden:{path}"
    yield {"type": "started", "mode": mode, "total": total, "completed": 0, "source": source}

    case_results = []
    case_details = []
    run_id = f"skill-eval-{mode}-{uuid4().hex[:8]}"
    # 快检模式构建LLM安全判定实例
    quick_guard_llm = None
    if mode == "quick" and settings.prompt_guard_llm_enabled:
        try:
            from personal_assistant.agent.llm import build_llm
            quick_guard_llm = build_llm(
                settings,
                LLMConfig(
                    model=settings.prompt_guard_llm_model,
                    temperature=0.0,
                ),
            )
        except Exception as exc:
            logger.warning("Failed to build LLM prompt guard for quick evaluation: %s", exc, exc_info=True)
    for index, case in enumerate(cases, start=1):
        try:
            if mode == "e2e":
                if harness is None:
                    raise HTTPException(status_code=500, detail="harness is required for e2e evaluation")
                outcome = await _run_e2e_case(harness, case, run_id, agent_mode=agent_mode)
            else:
                outcome = await _run_quick_case(registry, case, guard_llm=quick_guard_llm, agent_mode=agent_mode)
        except Exception as exc:
            logger.error("Case %s evaluation failed: %s", case.id, exc)
            yield {
                "type": "case_error",
                "mode": mode,
                "case_id": case.id,
                "message": f"id:{case.id}评测失败，请稍后重试",
            }
            continue
        case_results.append(outcome)
        judge = None
        if mode == "e2e" and judge_client is not None:
            judge = await evaluate_case_with_judge(
                case,
                outcome,
                judge_client=judge_client,
                model=judge_model or settings.evaluation_judge_model,
            )
        detail = build_case_evaluation_detail(case, outcome, mode=mode, judge=judge)
        case_details.append(detail)
        progress_event: dict = {
            "type": "case_progress",
            "mode": mode,
            "source": source,
            "total": total,
            "completed": index,
            "percent": round((index / total) * 100) if total else 100,
            "case_id": case.id,
            "expected_skills": case.expected_skills,
            "selected_skills": outcome["selected_skills"],
            "tool_failed": outcome["tool_failed"],
            "tool_completed": outcome["tool_completed"],
            "detail": detail.model_dump(mode="json"),
        }
        if agent_mode == "multi":
            intent_slots = outcome.get("intent_slots", {})
            progress_event["expected_intent"] = case.expected_intent
            progress_event["actual_intent"] = intent_slots.get("intent", "general")
            progress_event["intent_slots"] = intent_slots
        yield progress_event

    results: list[SkillEvaluationResult] = []
    for skill in skills:
        static_metrics = evaluate_static_skill(skill)
        if agent_mode == "multi":
            # Multi-agent: per-skill routing is meaningless; score only static quality
            static_score = score_static_metrics(static_metrics)
            components = {"static": static_score}
            result = SkillEvaluationResult(
                skill_name=skill.name,
                overall_score=static_score,
                static=static_metrics,
                runtime=None,
                score_components=components,
            )
        else:
            components = _case_score_components(skill.name, case_results, static_metrics, mode)
            runtime_metrics = (
                _runtime_metrics_from_cases(skill.name, case_results, static_metrics)
                if mode == "e2e"
                else None
            )
            result = SkillEvaluationResult(
                skill_name=skill.name,
                overall_score=_weighted_score(components),
                static=static_metrics,
                runtime=runtime_metrics,
                score_components=components,
            )
        results.append(result)

    report = SkillEvaluationReport(
        skills=results,
        routing=_routing_metrics_from_case_results(case_results) if agent_mode == "single" else None,
        multi_agent_routing=(
            evaluate_multi_agent_intent_cases(cases) if agent_mode == "multi" else None
        ),
        safety=evaluate_safety_cases(cases, case_results),  # 快检也需要Prompt Guard安全检测
        tools=evaluate_tool_cases(cases, case_results) if mode == "e2e" else None,
        answers=evaluate_answer_cases(cases, case_results) if mode == "e2e" else None,
        hallucinations=(
            evaluate_hallucination_cases(cases, case_results) if mode == "e2e" else None
        ),
        case_details=case_details,
    )
    await memory.record_skill_evaluation_results(report, source=source)
    latest = await memory.list_latest_skill_evaluations()
    yield {
        "type": "done",
        "mode": mode,
        "source": source,
        "total": total,
        "completed": total,
        "percent": 100,
        "results": [item.model_dump(mode="json") for item in latest],
        "report": report.model_dump(mode="json"),
    }


def _build_evaluation_judge(settings):
    if not settings.evaluation_judge_enabled:
        return None
    return build_llm(
        settings,
        LLMConfig(
            base_url=settings.evaluation_judge_base_url or settings.llm_base_url,
            api_key=settings.evaluation_judge_api_key or settings.llm_api_key,
            model=settings.evaluation_judge_model,
            temperature=0,
        ),
    )


async def _run_quick_case(registry: SkillRegistry, case: GoldenSkillCase, guard_llm=None, *, agent_mode: str = "single") -> dict:
    query = _case_query(case)
    logs: list[dict] = []
    # Layer 1: 正则快速拦截
    guard_match = scan_prompt_guard(query)
    # Layer 2: LLM语义安全判定
    if not guard_match and guard_llm is not None:
        guard_match = await scan_prompt_guard_with_llm(query, guard_llm)
    if guard_match:
        logs.append({
            "event_type": "security",
            "status": "blocked",
            "name": guard_match.category,
            "input": {"message": query[:200]},
            "error": {"reason": guard_match.reason},
            "metadata": {"severity": guard_match.severity, "source": f"{guard_match.source}_prompt_guard"},
        })
        # Prompt Guard命中时直接拦截，不进入路由
        return {
            "case": case,
            "selected_skills": [],
            "logs": logs,
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

    if agent_mode == "multi":
        # Multi-agent: use rewrite_query_and_slots for intent+slot routing
        from personal_assistant.agent.multi_agent import rewrite_query_and_slots
        payload = rewrite_query_and_slots(query)
        return {
            "case": case,
            "selected_skills": [],
            "intent_slots": payload["slots"],
            "rewritten_query": payload["rewritten_query"],
            "logs": logs,
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

    # 快检模式使用完整三层漏斗路由：正则→语义检索→LLM判定
    # 服务不可用时自动降级，和生产环境逻辑一致
    try:
        routing = await route_skill_names_with_trace(registry, query, **quick_eval_router_kwargs)
    except Exception as exc:
        logger.warning("Quick evaluation full routing failed, falling back to regex only: %s", exc)
        routing = await route_skill_names_with_trace(registry, query)
    return {
        "case": case,
        "selected_skills": routing.selected_skills,
        "routing_trace": routing.trace,
        "logs": logs,
        "final_answer": "",
        "tool_names": [],
        "tool_calls": [],
        "tool_completed": False,
        "tool_failed": False,
    }


async def _run_e2e_case(
    harness: AgentHarness,
    case: GoldenSkillCase,
    run_id: str,
    *,
    agent_mode: str = "single",
) -> dict:
    thread_id = f"{run_id}-{case.id}"
    response = None
    for message in _case_messages(case):
        if agent_mode == "single":
            response = await harness.run_user_turn(thread_id, message)
        else:
            response = await harness.run_user_turn(thread_id, message, agent_mode=agent_mode)
        response = await _auto_resolve_eval_approvals(harness, thread_id, response)
    logs = await harness.list_execution_logs(thread_id, limit=500)
    selected = _selected_skills_from_logs(logs)
    routing_trace = _routing_trace_from_logs(logs)
    final_answer = _final_answer_from_response(response) or _final_answer_from_logs(logs)
    return {
        "case": case,
        "selected_skills": selected,
        "routing_trace": routing_trace,
        "logs": logs,
        "final_answer": final_answer,
        "tool_names": _tool_names_from_logs(logs),
        "tool_calls": _tool_calls_from_logs(logs),
        "tool_completed": any(
            _log_value(log, "event_type") == "tool" and _log_value(log, "status") == "completed"
            for log in logs
        ),
        "tool_failed": any(
            _log_value(log, "event_type") in {"tool", "tool_retry"}
            and _log_value(log, "status") in {"failed", "retrying"}
            for log in logs
        ),
    }


async def _auto_resolve_eval_approvals(harness: AgentHarness, thread_id: str, response):
    current = response
    resume = getattr(harness, "resume_after_approval", None)
    if not callable(resume):
        return current

    guard = 0
    while _log_value(current, "status") == "requires_approval":
        approvals = _log_value(current, "approvals") or []
        if not approvals:
            break
        for approval in approvals:
            approval_id = _log_value(approval, "approval_id")
            if not isinstance(approval_id, str):
                continue
            current = await resume(
                thread_id,
                approval_id,
                _eval_approval_is_safe(approval),
            )
        guard += 1
        if guard >= 20:
            break
    return current


def _eval_approval_is_safe(approval) -> bool:
    tool_name = _log_value(approval, "name")
    args = _log_value(approval, "args") or {}
    if not isinstance(tool_name, str):
        return False
    return scan_tool_guard(tool_name, args) is None


def _case_messages(case: GoldenSkillCase) -> list[str]:
    turns = _log_value(case, "turns")
    if isinstance(turns, list) and turns:
        return [str(turn) for turn in turns]
    return [_case_query(case)]


def _case_query(case: GoldenSkillCase) -> str:
    query = _log_value(case, "query")
    if isinstance(query, str) and query:
        return query
    turns = _log_value(case, "turns")
    if isinstance(turns, list) and turns:
        return "\n".join(str(turn) for turn in turns)
    return ""


def _final_answer_from_response(response) -> str:
    message = _log_value(response, "message")
    return message if isinstance(message, str) else ""


def _final_answer_from_logs(logs) -> str:
    for log in reversed(list(logs)):
        output = _log_value(log, "output") or {}
        if isinstance(output, dict):
            for key in ("message", "content", "answer"):
                value = output.get(key)
                if isinstance(value, str):
                    return value
    return ""


def _tool_names_from_logs(logs) -> list[str]:
    names = []
    for log in logs:
        if _log_value(log, "event_type") != "tool":
            continue
        name = _log_value(log, "name")
        if isinstance(name, str):
            names.append(name)
    return names


def _tool_calls_from_logs(logs) -> list[dict]:
    calls = []
    for log in logs:
        if _log_value(log, "event_type") != "tool":
            continue
        name = _log_value(log, "name")
        if not isinstance(name, str):
            continue
        args = _log_value(log, "input") or {}
        if not isinstance(args, dict):
            args = {}
        calls.append({"name": name, "args": args})
    return calls


def _selected_skills_from_logs(logs) -> list[str]:
    for log in reversed(list(logs)):
        metadata = _log_value(log, "metadata") or {}
        selected = metadata.get("selected_skills") if isinstance(metadata, dict) else None
        if isinstance(selected, list):
            return [str(item) for item in selected]
    return []


def _routing_trace_from_logs(logs) -> list[dict]:
    for log in reversed(list(logs)):
        metadata = _log_value(log, "metadata") or {}
        trace = metadata.get("routing_trace") if isinstance(metadata, dict) else None
        if isinstance(trace, list):
            return [item for item in trace if isinstance(item, dict)]
    return []


def _case_score_components(
    skill_name: str,
    case_results: list[dict],
    static_metrics,
    mode: str,
) -> dict[str, float]:
    expected_cases = [
        item for item in case_results if skill_name in item["case"].expected_skills
    ]
    negative_cases = [
        item for item in case_results if not item["case"].expected_skills
    ]
    routing_score = None
    if expected_cases:
        selected_hits = sum(skill_name in item["selected_skills"] for item in expected_cases)
        routing_score = selected_hits / len(expected_cases)
        if negative_cases:
            false_positives = sum(skill_name in item["selected_skills"] for item in negative_cases)
            routing_score *= 1.0 - (false_positives / len(negative_cases))

    components = {"static": score_static_metrics(static_metrics)}
    if routing_score is not None:
        components["routing"] = max(0.0, min(1.0, routing_score))
    if mode == "e2e" and expected_cases:
        runtime_passes = sum(
            skill_name in item["selected_skills"]
            and _runtime_case_passed(item, static_metrics)
            for item in expected_cases
        )
        components["runtime"] = runtime_passes / len(expected_cases)
    return components


def _routing_metrics_from_case_results(case_results: list[dict]) -> RoutingMetrics:
    positive_total = 0
    positive_exact_matches = 0
    negative_total = 0
    false_positives = 0
    true_positive_skills = 0
    selected_positive_skills = 0
    expected_positive_skills = 0
    over_selected_cases = 0
    under_selected_cases = 0
    for item in case_results:
        expected_set = set(item["case"].expected_skills)
        selected_set = set(item["selected_skills"])
        if expected_set:
            positive_total += 1
            true_positive_skills += len(selected_set & expected_set)
            selected_positive_skills += len(selected_set)
            expected_positive_skills += len(expected_set)
            if selected_set == expected_set:
                positive_exact_matches += 1
            if selected_set - expected_set:
                over_selected_cases += 1
            if expected_set - selected_set:
                under_selected_cases += 1
        else:
            negative_total += 1
            if selected_set:
                false_positives += 1
                over_selected_cases += 1
    precision = (
        true_positive_skills / selected_positive_skills
        if selected_positive_skills
        else None
    )
    recall = (
        true_positive_skills / expected_positive_skills
        if expected_positive_skills
        else None
    )
    return RoutingMetrics(
        total_cases=len(case_results),
        selection_accuracy=(
            positive_exact_matches / positive_total if positive_total else None
        ),
        false_positive_rate=false_positives / negative_total if negative_total else None,
        skill_selection_precision=precision,
        skill_selection_recall=recall,
        skill_selection_f1=_f1(precision, recall),
        skill_over_selection_rate=(
            over_selected_cases / len(case_results) if case_results else None
        ),
        skill_under_selection_rate=(
            under_selected_cases / positive_total if positive_total else None
        ),
    )


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _runtime_metrics_from_cases(
    skill_name: str,
    case_results: list[dict],
    static_metrics,
) -> RuntimeSkillMetrics | None:
    expected_cases = [
        item for item in case_results if skill_name in item["case"].expected_skills
    ]
    if not expected_cases:
        return None
    successful = sum(
        skill_name in item["selected_skills"]
        and _runtime_case_passed(item, static_metrics)
        for item in expected_cases
    )
    failed = len(expected_cases) - successful
    return RuntimeSkillMetrics(
        skill_name=skill_name,
        tool_calls=sum(item["tool_completed"] or item["tool_failed"] for item in expected_cases),
        successful_calls=successful,
        failed_calls=failed,
        execution_success_rate=successful / len(expected_cases),
    )


def _runtime_case_passed(case_result: dict, static_metrics) -> bool:
    if case_result["tool_failed"]:
        return False
    if getattr(static_metrics, "tool_count", 0) > 0:
        return bool(case_result["tool_completed"])
    return True


def _log_value(log, name: str):
    if isinstance(log, dict):
        return log.get(name)
    return getattr(log, name, None)


def _golden_dataset_root() -> Path:
    return Path(__file__).resolve().parents[3] / "evaluation" / "golden"


def _list_golden_datasets(
    *,
    golden_root: Path | None = None,
    agent_mode: str = "single",
) -> list[SkillEvaluationDataset]:
    root = golden_root or _golden_dataset_root()
    if not root.exists():
        return []
    datasets = []
    for path in sorted(root.glob("*.jsonl"), key=lambda item: item.name):
        if not path.is_file():
            continue
        if agent_mode == "multi" and not _dataset_supports_multi_agent(path):
            continue
        datasets.append(
            SkillEvaluationDataset(
                name=path.stem,
                path=path.stem,
                label=path.stem.replace("_", " "),
            )
        )
    return datasets


def _dataset_supports_multi_agent(path: Path) -> bool:
    """Check if the first case in a golden dataset is compatible with multi-agent evaluation.

    Multi-agent compatible datasets must have at least one of:
    - expected_intent (multi-agent intent routing)
    - expected_behavior (security / prompt guard, runs before routing in all modes)
    """
    try:
        first_line = ""
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    first_line = stripped
                    break
        if not first_line:
            return False
        case = json.loads(first_line)
        return (
            case.get("expected_intent") is not None
            or case.get("expected_behavior") is not None
        )
    except Exception:
        return False


def _resolve_golden_path(
    golden_path: str | Path | None,
    *,
    search_roots: list[Path] | None = None,
) -> Path:
    if not golden_path:
        raise HTTPException(status_code=400, detail="golden_path is required")

    raw_path = Path(golden_path).expanduser()
    backend_root = Path(__file__).resolve().parents[3]
    roots = search_roots or [
        Path.cwd(),
        backend_root,
        _golden_dataset_root(),
    ]
    candidates = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
        if raw_path.suffix == "":
            candidates.append(raw_path.with_suffix(".jsonl"))

    for root in roots:
        if raw_path.is_absolute():
            break
        rooted = root / raw_path
        candidates.append(rooted)
        if rooted.suffix == "":
            candidates.append(rooted.with_suffix(".jsonl"))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail=f"Golden dataset not found: {golden_path}")


def _load_golden_cases(path: Path) -> list[AgentEvaluationCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cases.append(AgentEvaluationCase.model_validate(json.loads(stripped)))
    return cases


def _skill_info(
    skill,
    latest_evaluation: SkillEvaluationSnapshot | None = None,
) -> SkillInfo:
    static_metrics = evaluate_static_skill(skill)
    return SkillInfo(
        name=skill.name,
        description=skill.description,
        tool_names=skill.tool_names,
        path=str(skill.path),
        loaded=skill.loaded,
        latest_evaluation=latest_evaluation,
        evaluation=SkillEvaluationSummary(
            overall_score=score_static_metrics(static_metrics),
            description_tokens=static_metrics.description_tokens,
            skill_md_lines=static_metrics.skill_md_lines,
            python_lines=static_metrics.python_lines,
            max_cyclomatic_complexity=static_metrics.max_cyclomatic_complexity,
            tool_count=static_metrics.tool_count,
        ),
    )
