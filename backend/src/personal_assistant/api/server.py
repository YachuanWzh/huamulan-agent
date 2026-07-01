import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

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

from personal_assistant.agent.agent import warmup_skill_routing
from personal_assistant.agent.harness import AgentHarness
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
    ThreadSummary,
    ToolError,
    ToolCallApproval,
)
from personal_assistant.config import get_settings
from personal_assistant.cache import build_cache
from personal_assistant.memory.cached import CachedPostgresMemory
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry
from personal_assistant.tracing import build_langfuse_callback


settings = get_settings()
registry = SkillRegistry(settings.skills_dir)
cache = build_cache(settings)
postgres_memory = PostgresMemory(settings.database_url)
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
    return [_skill_info(skill) for skill in registry.skills.values()]


@app.post("/api/skills/reload", response_model=list[SkillInfo])
async def reload_skills() -> list[SkillInfo]:
    return [_skill_info(skill) for skill in registry.reload()]


def _skill_info(skill) -> SkillInfo:
    return SkillInfo(
        name=skill.name,
        description=skill.description,
        tool_names=skill.tool_names,
        path=str(skill.path),
        loaded=skill.loaded,
    )
