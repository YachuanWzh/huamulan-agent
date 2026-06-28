from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personal_assistant.agent.harness import AgentHarness
from personal_assistant.api.schemas import (
    ApprovalDecision,
    ChatRequest,
    ChatResponse,
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
    try:
        yield
    finally:
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


@app.post("/api/approve", response_model=ChatResponse)
async def approve(request: ApprovalDecision) -> ChatResponse:
    return await harness.resume_after_approval(
        request.thread_id,
        request.approval_id,
        request.approved,
    )


@app.get("/api/threads/{thread_id}/replay", response_model=ReplayResponse)
async def replay(thread_id: str) -> ReplayResponse:
    return ReplayResponse(thread_id=thread_id, states=await harness.replay(thread_id))


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
    )
