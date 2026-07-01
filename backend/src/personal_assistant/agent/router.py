import asyncio
import json
import logging
import math
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field, ValidationError

from personal_assistant.agent.state import AgentState
from personal_assistant.skills import SkillRegistry

if TYPE_CHECKING:
    from personal_assistant.memory.long_term import LongTermMemoryStore

logger = logging.getLogger(__name__)

_BASE_PROMPT = (
    "You are a personal assistant running as a single ReAct agent. "
    "Basic shell and file tools are always available. "
    "Additional capabilities come from the selected skills below. "
    "Use skill tools only when a selected skill makes them available. "
    "Every tool call is approval-gated by the harness."
)

_DEFAULT_SKILL_REGEXES: dict[str, list[str]] = {
    "weather": [
        r"\b(weather|forecast|temperature|rain|snow|wind|humid(?:ity)?)\b",
        (
            r"(\u5929\u6c14|\u6c14\u6e29|\u6e29\u5ea6|\u4e0b\u96e8|"
            r"\u4e0b\u96ea|\u964d\u96e8|\u964d\u96ea|\u522e\u98ce|"
            r"\u9884\u62a5|\u51b7\u4e0d\u51b7|\u70ed\u4e0d\u70ed)"
        ),
    ],
    "resolve-time": [
        r"\b(today|tomorrow|yesterday|date|time|weekday|next week|this week|last week)\b",
        (
            r"(\u4eca\u5929|\u660e\u5929|\u540e\u5929|\u6628\u5929|"
            r"\u524d\u5929|\u4e0b\u5468|\u8fd9\u5468|\u4e0a\u5468|"
            r"\u661f\u671f|\u5468[\u4e00\u4e8c\u4e09\u56db\u4e94"
            r"\u516d\u65e5\u5929]?|\u51e0\u70b9|\u65e5\u671f|\u65f6\u95f4)"
        ),
    ],
    "find-skills": [
        r"\b(find|search|install|add|discover)\s+(?:an?\s+)?skill\b",
        r"\b(is there|how do i|how can i).{0,80}\bskill\b",
        (
            r"(\u627e.*\u6280\u80fd|\u641c\u7d22.*\u6280\u80fd|"
            r"\u5b89\u88c5.*\u6280\u80fd|\u6709\u6ca1\u6709.*\u6280\u80fd)"
        ),
    ],
    "audit-sop": [
        r"\b(audit|trace|execution log|tool failure|retry chain|token usage|approval|security event)\b",
        (
            r"(\u5ba1\u8ba1|\u6267\u884c\u65e5\u5fd7|\u8c03\u7528\u94fe|"
            r"\u5de5\u5177\u5931\u8d25|\u91cd\u8bd5|token|\u5ba1\u6279|"
            r"\u5b89\u5168\u4e8b\u4ef6)"
        ),
    ],
    "akshare-stock": [
        r"\b(stock|stocks|market|kline|k-line|finance|trading|share price)\b",
        (
            r"(A\u80a1|\u80a1\u7968|\u884c\u60c5|\u5927\u76d8|"
            r"K\u7ebf|\u5206\u65f6|\u6da8\u505c|\u8dcc\u505c|"
            r"\u8d44\u91d1\u6d41|\u8d22\u62a5|\u677f\u5757|"
            r"\u6e2f\u80a1|\u7f8e\u80a1|\u57fa\u91d1|\u53ef\u8f6c\u503a)"
        ),
        r"\b\d{6}\b",
    ],
}


@dataclass(frozen=True)
class SkillSemanticCandidate:
    name: str
    description: str
    score: float


class SkillEmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float]:
        """Return an embedding vector for text."""


class SkillVectorIndex(Protocol):
    async def warmup(self, registry: SkillRegistry) -> None:
        """Prepare skill vectors before the first user query."""

    async def search(
        self,
        registry: SkillRegistry,
        query: str,
        top_k: int,
    ) -> list[SkillSemanticCandidate]:
        """Return semantic skill candidates ordered by descending similarity."""


class LLMSkillRouteDecision(BaseModel):
    selectedSkill: str | None = Field(default=None)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class OllamaBgeM3EmbeddingProvider:
    """Embedding provider for a local Ollama bge-m3 model."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "bge-m3",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    def _embed_sync(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama embedding request failed: {exc}") from exc
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError("Ollama embedding response did not include an embedding")
        return [float(value) for value in embedding]


class InMemorySkillVectorIndex:
    """Small default vector index; replace with a vector DB adapter when configured."""

    def __init__(self, embedding_provider: SkillEmbeddingProvider) -> None:
        self.embedding_provider = embedding_provider
        self._skill_vectors: dict[tuple[str, str | None], list[float]] = {}

    async def warmup(self, registry: SkillRegistry) -> None:
        for skill in registry.skills.values():
            cache_key = (skill.name, skill.source_hash)
            if cache_key not in self._skill_vectors:
                self._skill_vectors[cache_key] = await self.embedding_provider.embed(
                    _skill_semantic_document(skill)
                )

    async def search(
        self,
        registry: SkillRegistry,
        query: str,
        top_k: int,
    ) -> list[SkillSemanticCandidate]:
        if top_k <= 0:
            return []
        query_vector = await self.embedding_provider.embed(query)
        candidates: list[SkillSemanticCandidate] = []
        for skill in registry.skills.values():
            cache_key = (skill.name, skill.source_hash)
            vector = self._skill_vectors.get(cache_key)
            if vector is None:
                vector = await self.embedding_provider.embed(_skill_semantic_document(skill))
                self._skill_vectors[cache_key] = vector
            candidates.append(
                SkillSemanticCandidate(
                    name=skill.name,
                    description=skill.description,
                    score=_cosine_similarity(query_vector, vector),
                )
            )
        return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)[:top_k]


class QdrantSkillVectorIndex:
    """Qdrant-backed skill vector index using the HTTP API."""

    def __init__(
        self,
        embedding_provider: SkillEmbeddingProvider,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.url = url.rstrip("/")
        self.collection = collection
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._synced_hashes: dict[str, str | None] = {}
        self._remote_hashes_loaded = False

    async def warmup(self, registry: SkillRegistry) -> None:
        await self._sync_skills(registry)

    async def search(
        self,
        registry: SkillRegistry,
        query: str,
        top_k: int,
    ) -> list[SkillSemanticCandidate]:
        if top_k <= 0:
            return []
        await self._sync_skills(registry)
        logger.info(
            "Qdrant skill vector search started: collection=%s top_k=%s query_chars=%s",
            self.collection,
            top_k,
            len(query),
        )
        query_vector = await self.embedding_provider.embed(query)
        response = await asyncio.to_thread(self._search_sync, query_vector, top_k)
        results = response.get("result", [])
        if not isinstance(results, list):
            logger.info(
                "Qdrant skill vector search completed: collection=%s results=invalid candidates=none",
                self.collection,
            )
            return []

        candidates: list[SkillSemanticCandidate] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            name = payload.get("skill_name")
            if not isinstance(name, str) or name not in registry.skills:
                continue
            candidates.append(
                SkillSemanticCandidate(
                    name=name,
                    description=str(payload.get("description") or ""),
                    score=float(item.get("score") or 0.0),
                )
            )
        candidates = candidates[:top_k]
        candidate_summary = (
            ", ".join(
                f"{candidate.name}:{candidate.score:.4f}" for candidate in candidates
            )
            if candidates
            else "none"
        )
        logger.info(
            "Qdrant skill vector search completed: collection=%s results=%s candidates=%s",
            self.collection,
            len(results),
            candidate_summary,
        )
        return candidates

    async def _sync_skills(self, registry: SkillRegistry) -> None:
        logger.info(
            "Qdrant skill vector sync started: collection=%s skills=%s",
            self.collection,
            len(registry.skills),
        )
        await self._load_remote_hashes()
        points = []
        skipped = 0
        for skill in registry.skills.values():
            if self._synced_hashes.get(skill.name) == skill.source_hash:
                skipped += 1
                continue
            logger.info(
                "Generating skill embedding for Qdrant: skill=%s collection=%s",
                skill.name,
                self.collection,
            )
            vector = await self.embedding_provider.embed(_skill_semantic_document(skill))
            points.append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, str(skill.path))),
                    "vector": vector,
                    "payload": {
                        "skill_name": skill.name,
                        "description": skill.description,
                        "source_hash": skill.source_hash,
                    },
                }
            )
        if not points:
            logger.info(
                "Qdrant skill vector sync completed: collection=%s upserted=0 skipped=%s",
                self.collection,
                skipped,
            )
            return
        await asyncio.to_thread(self._upsert_sync, points)
        for skill in registry.skills.values():
            self._synced_hashes[skill.name] = skill.source_hash
        logger.info(
            "Qdrant skill vector sync completed: collection=%s upserted=%s skipped=%s",
            self.collection,
            len(points),
            skipped,
        )

    async def _load_remote_hashes(self) -> None:
        if self._remote_hashes_loaded:
            return
        response = await asyncio.to_thread(self._scroll_sync)
        result = response.get("result", {})
        points = result.get("points", []) if isinstance(result, dict) else []
        if isinstance(points, list):
            for point in points:
                if not isinstance(point, dict):
                    continue
                payload = point.get("payload")
                if not isinstance(payload, dict):
                    continue
                skill_name = payload.get("skill_name")
                source_hash = payload.get("source_hash")
                if isinstance(skill_name, str):
                    self._synced_hashes[skill_name] = (
                        source_hash if isinstance(source_hash, str) else None
                    )
        self._remote_hashes_loaded = True

    def _scroll_sync(self) -> dict:
        return self._request_json(
            "POST",
            f"/collections/{self.collection}/points/scroll",
            {
                "limit": 1000,
                "with_payload": True,
                "with_vector": False,
            },
        )

    def _upsert_sync(self, points: list[dict]) -> dict:
        return self._request_json(
            "PUT",
            f"/collections/{self.collection}/points?wait=true",
            {"points": points},
        )

    def _search_sync(self, vector: list[float], top_k: int) -> dict:
        return self._request_json(
            "POST",
            f"/collections/{self.collection}/points/search",
            {
                "vector": vector,
                "limit": top_k,
                "with_payload": True,
            },
        )

    def _request_json(self, method: str, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = ""
            if exc.fp:
                body_text = exc.fp.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                "Qdrant request failed: "
                f"collection={self.collection} endpoint={method} {path} "
                f"url={self.url}{path} HTTP {exc.code} {exc.reason}; body={body_text}"
            ) from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Qdrant request failed: "
                f"collection={self.collection} endpoint={method} {path} "
                f"url={self.url}{path}; error={exc}"
            ) from exc


def build_skill_router(
    registry: SkillRegistry,
    long_term_memory: "LongTermMemoryStore | None" = None,
    cache=None,
    memory_cache_ttl_seconds: int = 60,
    semantic_index: SkillVectorIndex | None = None,
    llm=None,
    semantic_threshold: float = 0.72,
    semantic_top_k: int = 3,
    llm_retry_count: int = 1,
):
    async def route_skills(state: AgentState) -> AgentState:
        user_text = "\n".join(
            getattr(message, "content", "")
            for message in state.get("messages", [])
            if getattr(message, "type", "") == "human"
        )[-4000:]

        selected = await route_skill_names(
            registry,
            user_text,
            semantic_index=semantic_index,
            llm=llm,
            semantic_threshold=semantic_threshold,
            semantic_top_k=semantic_top_k,
            llm_retry_count=llm_retry_count,
        )

        for name in selected:
            registry.load_skill(name)

        memory_text = None
        if long_term_memory is not None and cache is not None:
            memory_text = await long_term_memory.read_all_cached(
                cache,
                ttl_seconds=memory_cache_ttl_seconds,
            )
        system = build_system_prompt(
            registry,
            selected,
            long_term_memory=long_term_memory,
            memory_text=memory_text,
        )
        return {
            "messages": [system],
            "selected_skills": selected,
            "allowed_tools": list(registry.tool_map_for_skills(selected)),
        }

    return route_skills


async def route_skill_names(
    registry: SkillRegistry,
    user_text: str,
    semantic_index: SkillVectorIndex | None = None,
    llm=None,
    semantic_threshold: float = 0.72,
    semantic_top_k: int = 3,
    llm_retry_count: int = 1,
) -> list[str]:
    """Route skills through regex, semantic retrieval, then optional LLM judgment."""
    regex_selected = _regex_route(registry, user_text)
    if regex_selected:
        logger.info("Skill routing regex stage selected=%s", ",".join(regex_selected))
        return regex_selected
    logger.info("Skill routing regex stage missed")

    semantic_candidates: list[SkillSemanticCandidate] = []
    if semantic_index is not None and user_text.strip():
        try:
            semantic_candidates = await semantic_index.search(registry, user_text, semantic_top_k)
        except Exception:
            logger.exception("Skill routing semantic stage failed")
            semantic_candidates = []
        candidate_summary = _semantic_candidate_summary(semantic_candidates)
        logger.info(
            "Skill routing semantic stage candidates=%s threshold=%.4f",
            candidate_summary,
            semantic_threshold,
        )
        if semantic_candidates and semantic_candidates[0].score >= semantic_threshold:
            logger.info(
                "Skill routing semantic stage selected=%s score=%.4f",
                semantic_candidates[0].name,
                semantic_candidates[0].score,
            )
            return [semantic_candidates[0].name]
        if semantic_candidates:
            logger.info(
                "Skill routing semantic top candidate below threshold: top=%s score=%.4f threshold=%.4f",
                semantic_candidates[0].name,
                semantic_candidates[0].score,
                semantic_threshold,
            )
    elif semantic_index is None:
        logger.info("Skill routing semantic stage skipped: no semantic index")
    else:
        logger.info("Skill routing semantic stage skipped: empty user text")

    if llm is None or not semantic_candidates:
        logger.info(
            "Skill routing completed without selected skill: llm_available=%s semantic_candidates=%s",
            llm is not None,
            len(semantic_candidates),
        )
        return []

    return await _llm_route(
        registry,
        user_text,
        semantic_candidates,
        llm,
        retry_count=llm_retry_count,
    )


def _semantic_candidate_summary(candidates: list[SkillSemanticCandidate]) -> str:
    if not candidates:
        return "none"
    return ", ".join(f"{candidate.name}:{candidate.score:.4f}" for candidate in candidates)


def build_system_prompt(
    registry: SkillRegistry,
    selected: list[str],
    long_term_memory: "LongTermMemoryStore | None" = None,
    memory_text: str | None = None,
) -> SystemMessage:
    """Build a progressive system prompt with meta overview + detailed selected skills."""
    sections: list[str] = []

    if long_term_memory is not None:
        if memory_text is None:
            memory_text = long_term_memory.read_all()
        if memory_text:
            sections.append(memory_text)

    sections.append(_BASE_PROMPT)

    selected_set = set(selected)
    selected_skills = [
        skill for skill in registry.skills.values() if skill.name in selected_set
    ]
    if selected_skills:
        meta_lines = [
            f"- **{skill.name}**: {skill.description}"
            for skill in selected_skills
        ]
        sections.append("## Available Skills\n" + "\n".join(meta_lines))

    detail_parts = []
    for name in selected:
        skill = registry.skills.get(name)
        if skill and skill.loaded and skill.instructions:
            detail_parts.append(f"## Skill: {skill.name}\n{skill.instructions}")
    if detail_parts:
        sections.append("\n\n".join(detail_parts))

    return SystemMessage(content="\n\n".join(sections))


def _keyword_route(registry: SkillRegistry, user_text: str) -> list[str]:
    """Backward-compatible first-stage deterministic routing."""
    return _regex_route(registry, user_text)


def _regex_route(registry: SkillRegistry, user_text: str) -> list[str]:
    normalized = user_text.lower()
    selected: list[str] = []
    for skill in registry.skills.values():
        if any(_regex_match(pattern, user_text) for pattern in _DEFAULT_SKILL_REGEXES.get(skill.name, [])):
            selected.append(skill.name)
            continue
        if skill.triggers:
            if any(_trigger_match(t, normalized) for t in skill.triggers):
                selected.append(skill.name)
            continue

        haystack = f"{skill.name}\n{skill.description}".lower()
        tokens = {
            token.strip(".,:;()[]{}#`*_-/")
            for token in haystack.split()
            if len(token.strip(".,:;()[]{}#`*_-/")) >= 3
        }
        if any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in tokens):
            selected.append(skill.name)
    return selected


async def _llm_route(
    registry: SkillRegistry,
    user_text: str,
    semantic_candidates: list[SkillSemanticCandidate],
    llm,
    retry_count: int,
) -> list[str]:
    payload = {
        "userInput": user_text,
        "relatedFind": [
            f"{candidate.name}: {candidate.description}" for candidate in semantic_candidates
        ],
    }
    attempts = max(0, retry_count) + 1
    last_output = None
    for attempt in range(1, attempts + 1):
        try:
            raw = await llm.ainvoke(_llm_route_prompt(payload))
            last_output = raw
            decision = _parse_llm_route_decision(raw)
        except Exception as exc:
            if isinstance(exc, (ValidationError, TypeError, ValueError)):
                logger.info(
                    "Skill routing LLM judge validation failed: attempt=%s error=%s output=%r",
                    attempt,
                    exc,
                    last_output,
                )
                payload = {
                    **payload,
                    "previousError": str(exc),
                    "previousOutput": _stringify_llm_output(last_output),
                }
                continue
            logger.exception(
                "Skill routing LLM judge request failed: attempt=%s error=%s",
                attempt,
                exc,
            )
            return []
        if decision.selectedSkill in registry.skills:
            logger.info(
                "Skill routing LLM judge selected=%s confidence=%.4f reason=%s",
                decision.selectedSkill,
                decision.confidence,
                decision.reason,
            )
            return [decision.selectedSkill]
        logger.info(
            "Skill routing LLM judge rejected candidates: selected=%s confidence=%.4f reason=%s",
            decision.selectedSkill,
            decision.confidence,
            decision.reason,
        )
        return []
    logger.info("Skill routing LLM judge exhausted retries without valid decision")
    return []


def _llm_route_prompt(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "你是 Skill 路由判定器。请只在 relatedFind 中选择一个确实适合处理 userInput 的 skill；"
        "如果没有足够把握，selectedSkill 返回 null。"
        "必须只返回 JSON，不要返回 Markdown，不要解释。"
        "JSON 字段固定为 selectedSkill、confidence、reason。\n\n"
        f"路由输入 JSON:\n{payload_json}"
    )


def _parse_llm_route_decision(raw) -> LLMSkillRouteDecision:
    if isinstance(raw, LLMSkillRouteDecision):
        return raw
    if isinstance(raw, dict):
        return LLMSkillRouteDecision.model_validate(raw)
    if isinstance(raw, str):
        return LLMSkillRouteDecision.model_validate_json(_extract_json_object(raw))
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        return LLMSkillRouteDecision.model_validate_json(_extract_json_object(content))
    raise TypeError(f"Unsupported LLM route decision type: {type(raw).__name__}")


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return stripped
    return stripped[start : end + 1]


def _stringify_llm_output(raw) -> str | None:
    if raw is None:
        return None
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, ensure_ascii=False)
    except TypeError:
        return repr(raw)


def _regex_match(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    except re.error:
        return re.search(re.escape(pattern), text, flags=re.IGNORECASE) is not None


def _trigger_match(trigger: str, normalized_text: str) -> bool:
    """A trigger matches if it appears as a substring."""
    return trigger.lower() in normalized_text


def _skill_semantic_document(skill) -> str:
    triggers = ", ".join(skill.triggers)
    return f"{skill.name}\n{skill.description}\n{triggers}".strip()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
