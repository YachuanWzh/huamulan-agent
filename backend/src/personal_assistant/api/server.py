from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from personal_assistant.agent.harness import AgentHarness
from personal_assistant.api.schemas import (
    ApprovalDecision,
    AuditEvent,
    ChatRequest,
    ChatResponse,
    DeleteThreadResponse,
    ReplayResponse,
    SkillInfo,
)
from personal_assistant.config import get_settings
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry


settings = get_settings()
registry = SkillRegistry(settings.skills_dir)
memory = PostgresMemory(settings.database_url)
harness = AgentHarness(settings, registry, memory)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await memory.start()
    registry.start_watching()
    try:
        yield
    finally:
        registry.stop_watching()
        await memory.stop()


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


@app.get("/api/threads/{thread_id}/replay", response_model=ReplayResponse)
async def replay(thread_id: str) -> ReplayResponse:
    return ReplayResponse(thread_id=thread_id, states=await harness.replay(thread_id))


@app.delete("/api/threads/{thread_id}", response_model=DeleteThreadResponse)
async def delete_thread(thread_id: str) -> DeleteThreadResponse:
    await harness.delete_thread(thread_id)
    return DeleteThreadResponse(thread_id=thread_id)


@app.get("/api/audit-events", response_model=list[AuditEvent])
async def list_audit_events(thread_id: str | None = None, limit: int = 100) -> list[AuditEvent]:
    return await harness.list_audit_events(thread_id=thread_id, limit=limit)


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
