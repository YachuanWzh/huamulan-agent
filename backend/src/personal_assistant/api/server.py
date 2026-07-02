import logging
import sys
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from personal_assistant.agent.agent import warmup_skill_routing
from personal_assistant.agent.harness import AgentHarness
from personal_assistant.agent.router import route_skill_names
from personal_assistant.api.schemas import (
    ApprovalBatchDecision,
    ApprovalDecision,
    AuditEvent,
    ChatRequest,
    ChatResponse,
    ClearThreadsResponse,
    DeleteThreadResponse,
    ExecutionLog,
    ExecutionSummary,
    ReplayResponse,
    SkillInfo,
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
    RoutingMetrics,
    RuntimeSkillMetrics,
    SkillEvaluationReport,
    SkillEvaluationResult,
)
from personal_assistant.skills.evaluation.report import (
    _score_components,
    _weighted_score,
    score_static_metrics,
)
from personal_assistant.skills.evaluation.models import GoldenSkillCase
from personal_assistant.skills.evaluation.offline import evaluate_routing_cases
from personal_assistant.skills.evaluation.report import evaluate_skill_registry
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
harness = AgentHarness(
    settings,
    registry,
    memory,
    callbacks=[langfuse_callback] if langfuse_callback else None,
    cache=cache,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await postgres_memory.start()
    registry.start_watching()
    await warmup_skill_routing(settings, registry)
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
    return await harness.run_user_turn(request.thread_id, request.message, request.llm)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        harness.run_user_turn_stream(request.thread_id, request.message, request.llm),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/approve", response_model=ChatResponse)
async def approve(request: ApprovalDecision) -> ChatResponse:
    return await harness.resume_after_approval(
        request.thread_id,
        request.approval_id,
        request.approved,
    )


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
            harness=harness,
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
    harness: AgentHarness | None = None,
):
    path = _resolve_golden_path(golden_path)
    cases = _load_golden_cases(path)
    skills = list(registry.skills.values())
    total = len(cases)
    source = f"golden:{path}"
    yield {"type": "started", "mode": mode, "total": total, "completed": 0, "source": source}

    case_results = []
    run_id = f"skill-eval-{mode}-{uuid4().hex[:8]}"
    for index, case in enumerate(cases, start=1):
        if mode == "e2e":
            if harness is None:
                raise HTTPException(status_code=500, detail="harness is required for e2e evaluation")
            outcome = await _run_e2e_case(harness, case, run_id)
        else:
            outcome = await _run_quick_case(registry, case)
        case_results.append(outcome)
        yield {
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
        }

    results: list[SkillEvaluationResult] = []
    for skill in skills:
        static_metrics = evaluate_static_skill(skill)
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
        routing=RoutingMetrics(total_cases=len(cases)),
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
    }


async def _run_quick_case(registry: SkillRegistry, case: GoldenSkillCase) -> dict:
    selected = await route_skill_names(registry, case.query)
    return {
        "case": case,
        "selected_skills": selected,
        "tool_completed": False,
        "tool_failed": False,
    }


async def _run_e2e_case(harness: AgentHarness, case: GoldenSkillCase, run_id: str) -> dict:
    thread_id = f"{run_id}-{case.id}"
    await harness.run_user_turn(thread_id, case.query)
    logs = await harness.list_execution_logs(thread_id, limit=500)
    selected = _selected_skills_from_logs(logs)
    return {
        "case": case,
        "selected_skills": selected,
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


def _selected_skills_from_logs(logs) -> list[str]:
    for log in reversed(list(logs)):
        metadata = _log_value(log, "metadata") or {}
        selected = metadata.get("selected_skills") if isinstance(metadata, dict) else None
        if isinstance(selected, list):
            return [str(item) for item in selected]
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
        exact_matches = sum(
            set(item["selected_skills"]) == set(item["case"].expected_skills)
            for item in expected_cases
        )
        routing_score = exact_matches / len(expected_cases)
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


def _resolve_golden_path(
    golden_path: str | Path | None,
    *,
    search_roots: list[Path] | None = None,
) -> Path:
    if not golden_path:
        raise HTTPException(status_code=400, detail="golden_path is required")

    raw_path = Path(golden_path).expanduser()
    backend_root = Path(__file__).resolve().parents[3]
    roots = search_roots or [Path.cwd(), backend_root]
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


def _load_golden_cases(path: Path) -> list[GoldenSkillCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cases.append(GoldenSkillCase.model_validate(json.loads(stripped)))
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
