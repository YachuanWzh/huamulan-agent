from pathlib import Path
from textwrap import dedent
from urllib.error import HTTPError
import io
import json
import logging

import pytest

from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.agent.router import (
    OllamaBgeM3Reranker,
    QdrantSkillVectorIndex,
    RerankUnavailableError,
    SkillSemanticCandidate,
    build_skill_router,
    build_system_prompt,
    route_skill_names,
    _keyword_route,
)
from personal_assistant.memory.long_term import LongTermMemoryStore


def _make_triggered_skill(tmp_path: Path) -> Path:
    """A skill whose trigger words do NOT appear in its name or description."""
    d = tmp_path / "cal"
    d.mkdir()
    (d / "SKILL.md").write_text(
        dedent(
            """
            ---
            name: cal
            description: Performs calendar arithmetic.
            triggers:
              - 今天
              - tomorrow
              - 星期几
            ---

            # Cal
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_named_skill(tmp_path: Path, name: str, description: str = "Test skill") -> None:
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )


class TestKeywordRoute:
    def test_matches_by_name_and_description(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # "alpha" appears in skill-a's name and description
        result = _keyword_route(registry, "I need help with alpha tasks")
        assert "skill-a" in result

    def test_no_match_returns_empty(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        result = _keyword_route(registry, "completely unrelated xyz query")
        assert result == []

    def test_matches_multiple_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # "skill" appears in all skills' descriptions (meta only)
        result = _keyword_route(registry, "I need a skill for this")
        assert len(result) == 3

    def test_does_not_match_on_full_instructions_only(self, multi_skill_dir: Path):
        """Keywords that appear only in full SKILL.md (not meta) should not match."""
        registry = SkillRegistry(multi_skill_dir)
        # "handles" appears in full SKILL.md ("Handles alpha tasks") but not in meta
        result = _keyword_route(registry, "please handle this request")
        assert result == []


class TestTriggerRouting:
    def test_matches_via_triggers_when_present(self, tmp_path: Path):
        """A trigger word not in name/description still routes the skill."""
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        # "今天" is a trigger but does not appear in name "cal" or description
        assert _keyword_route(registry, "今天是几号") == ["cal"]

    def test_matches_english_trigger(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "what about tomorrow") == ["cal"]

    def test_no_trigger_match_returns_empty(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "completely unrelated xyz") == []

    def test_skills_without_triggers_fall_back_to_token_match(self, multi_skill_dir: Path):
        """Skills without a triggers list still match on name/description tokens."""
        registry = SkillRegistry(multi_skill_dir)
        result = _keyword_route(registry, "I need help with alpha tasks")
        assert "skill-a" in result


class TestChineseRegexRouting:
    @pytest.mark.parametrize(
        ("skill_name", "query"),
        [
            ("weather", "北京明天会下雨吗"),
            ("resolve-time", "今天是几号"),
            ("find-skills", "帮我找一个股票分析技能"),
            ("audit-sop", "审计一下这个线程的执行日志"),
            ("akshare-stock", "看一下600519的K线和资金流"),
        ],
    )
    def test_current_skill_regexes_match_chinese_queries(
        self,
        tmp_path: Path,
        skill_name: str,
        query: str,
    ):
        _make_named_skill(tmp_path, skill_name)
        registry = SkillRegistry(tmp_path)

        assert _keyword_route(registry, query) == [skill_name]


class FakeSemanticIndex:
    def __init__(self, candidates: list[SkillSemanticCandidate]):
        self.candidates = candidates
        self.calls: list[str] = []

    async def search(self, registry: SkillRegistry, query: str, top_k: int):
        self.calls.append(query)
        return self.candidates[:top_k]


class FailingSemanticIndex:
    async def search(self, registry: SkillRegistry, query: str, top_k: int):
        raise RuntimeError("embedding service unavailable")


class FakeReranker:
    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, candidates: list[SkillSemanticCandidate]):
        self.calls.append((query, [candidate.name for candidate in candidates]))
        reranked = [
            SkillSemanticCandidate(
                name=candidate.name,
                description=candidate.description,
                score=self.scores.get(candidate.name, candidate.score),
            )
            for candidate in candidates
        ]
        return sorted(reranked, key=lambda candidate: candidate.score, reverse=True)


class FailingReranker:
    async def rerank(self, query: str, candidates: list[SkillSemanticCandidate]):
        raise RuntimeError("rerank service unavailable")


class UnavailableReranker:
    async def rerank(self, query: str, candidates: list[SkillSemanticCandidate]):
        raise RerankUnavailableError("model lacks embedding capability")


class CountingEmbeddingProvider:
    def __init__(self):
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0, 0.0, 0.0]


class FakeQdrantSkillVectorIndex(QdrantSkillVectorIndex):
    def __init__(
        self,
        embedding_provider,
        existing_points: list[dict] | None = None,
        search_results: list[dict] | None = None,
    ):
        super().__init__(
            embedding_provider,
            url="http://qdrant.example.test:6333",
            collection="skill_routes",
        )
        self.existing_points = existing_points or []
        self.search_results = search_results or []
        self.upserted_points: list[dict] = []

    def _scroll_sync(self) -> dict:
        return {"result": {"points": self.existing_points}}

    def _upsert_sync(self, points: list[dict]) -> dict:
        self.upserted_points.extend(points)
        return {"result": {"operation_id": 1}}

    def _search_sync(self, vector: list[float], top_k: int) -> dict:
        return {"result": self.search_results[:top_k]}


class FakeStructuredLLM:
    def __init__(self, outputs: list[object]):
        self.outputs = outputs
        self.payloads: list[dict] = []
        self.structured_output_calls = 0

    def with_structured_output(self, schema):
        self.structured_output_calls += 1
        self.schema = schema
        return self

    async def ainvoke(self, payload: dict):
        self.payloads.append(payload)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class TestSkillRoutingFunnel:
    @pytest.mark.asyncio
    async def test_regex_match_short_circuits_semantic_and_llm(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="other", description="Other", score=0.99)]
        )
        llm = FakeStructuredLLM([{"selectedSkill": "other", "confidence": 1.0, "reason": "x"}])

        result = await route_skill_names(
            registry,
            "what about tomorrow",
            semantic_index=semantic,
            llm=llm,
        )

        assert result == ["cal"]
        assert semantic.calls == []
        assert llm.payloads == []

    @pytest.mark.asyncio
    async def test_semantic_match_selects_top_candidate_above_threshold(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: other\ndescription: Other stuff.\n---\n# Other\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="other", description="Other stuff.", score=0.91)]
        )
        llm = FakeStructuredLLM([{"selectedSkill": "cal", "confidence": 1.0, "reason": "x"}])

        result = await route_skill_names(
            registry,
            "semantically related but no regex hit",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result == ["other"]
        assert semantic.calls == ["semantically related but no regex hit"]
        assert llm.payloads == []

    @pytest.mark.asyncio
    async def test_rerank_reorders_semantic_candidates_before_threshold_selection(
        self,
        tmp_path: Path,
    ):
        _make_triggered_skill(tmp_path)
        _make_named_skill(tmp_path, "other", "Other stuff.")
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [
                SkillSemanticCandidate(name="other", description="Other stuff.", score=0.77),
                SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.74),
            ]
        )
        reranker = FakeReranker({"cal": 0.94, "other": 0.12})
        llm = FakeStructuredLLM(
            [{"selectedSkill": "other", "confidence": 1.0, "reason": "not needed"}]
        )

        result = await route_skill_names(
            registry,
            "semantically related but no regex hit",
            semantic_index=semantic,
            reranker=reranker,
            semantic_threshold=0.8,
            rerank_threshold=0.9,
            llm=llm,
        )

        assert result == ["cal"]
        assert reranker.calls == [
            ("semantically related but no regex hit", ["other", "cal"])
        ]
        assert llm.payloads == []

    @pytest.mark.asyncio
    async def test_rerank_failure_keeps_semantic_candidates_for_llm_fallback(
        self,
        tmp_path: Path,
    ):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": "cal", "confidence": 0.75, "reason": "calendar intent"}]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            reranker=FailingReranker(),
            semantic_threshold=0.8,
            rerank_threshold=0.8,
            llm=llm,
        )

        assert result == ["cal"]
        assert len(llm.payloads) == 1
        assert "cal: Calendar arithmetic." in llm.payloads[0]

    @pytest.mark.asyncio
    async def test_rerank_unavailable_logs_without_traceback(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                reranker=UnavailableReranker(),
                semantic_threshold=0.8,
                rerank_threshold=0.8,
            )

        assert result == []
        assert "Skill routing rerank stage unavailable" in caplog.text
        assert "model lacks embedding capability" in caplog.text
        assert "Traceback" not in caplog.text

    @pytest.mark.asyncio
    async def test_logs_rerank_stage_when_enabled(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        _make_named_skill(tmp_path, "other", "Other stuff.")
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [
                SkillSemanticCandidate(name="other", description="Other stuff.", score=0.77),
                SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.74),
            ]
        )
        reranker = FakeReranker({"cal": 0.94, "other": 0.12})

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "semantically related but no regex hit",
                semantic_index=semantic,
                reranker=reranker,
                semantic_threshold=0.8,
                rerank_threshold=0.9,
                rerank_top_k=2,
            )

        assert result == ["cal"]
        assert "Skill routing rerank stage started" in caplog.text
        assert "input_candidates=other:0.7700, cal:0.7400" in caplog.text
        assert "top_k=2" in caplog.text
        assert "Skill routing rerank stage completed" in caplog.text
        assert "output_candidates=cal:0.9400, other:0.1200" in caplog.text

    @pytest.mark.asyncio
    async def test_low_semantic_score_falls_back_to_llm_judge(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": "cal", "confidence": 0.75, "reason": "calendar intent"}]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result == ["cal"]
        assert len(llm.payloads) == 1
        assert llm.structured_output_calls == 0
        assert isinstance(llm.payloads[0], str)
        assert '"userInput": "ambiguous date wording"' in llm.payloads[0]
        assert '"relatedFind": [' in llm.payloads[0]
        assert "cal: Calendar arithmetic." in llm.payloads[0]

    @pytest.mark.asyncio
    async def test_llm_judge_accepts_chat_message_json_content(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [FakeMessage('{"selectedSkill":"cal","confidence":0.75,"reason":"calendar intent"}')]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result == ["cal"]
        assert llm.structured_output_calls == 0

    @pytest.mark.asyncio
    async def test_llm_api_error_degrades_to_no_match(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM([RuntimeError("Thinking mode does not support this tool_choice")])

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                llm=llm,
                semantic_threshold=0.8,
            )

        assert result == []
        assert llm.structured_output_calls == 0
        assert "Skill routing LLM judge request failed" in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_llm_output_is_retried_with_error_context(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [
                {"skill": "cal"},
                {"selectedSkill": "cal", "confidence": 0.83, "reason": "fixed json"},
            ]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
            llm_retry_count=1,
        )

        assert result == ["cal"]
        assert len(llm.payloads) == 2
        assert "previousError" in llm.payloads[1]
        assert "previousOutput" in llm.payloads[1]
        assert '\\"skill\\": \\"cal\\"' in llm.payloads[1]

    @pytest.mark.asyncio
    async def test_semantic_failure_degrades_to_no_match(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)

        result = await route_skill_names(
            registry,
            "no deterministic match",
            semantic_index=FailingSemanticIndex(),
            llm=FakeStructuredLLM(
                [{"selectedSkill": "cal", "confidence": 0.9, "reason": "would match"}]
            ),
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_logs_semantic_threshold_and_llm_no_match(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": None, "confidence": 0.22, "reason": "not enough evidence"}]
        )

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                llm=llm,
                semantic_threshold=0.8,
            )

        assert result == []
        assert "Skill routing regex stage missed" in caplog.text
        assert "Skill routing semantic stage candidates=cal:0.3100 threshold=0.8000" in caplog.text
        assert "Skill routing semantic top candidate below threshold" in caplog.text
        assert "Skill routing LLM judge rejected candidates" in caplog.text
        assert "selected=None" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_llm_validation_error_before_retry(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM([{"skill": "cal"}])

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                llm=llm,
                semantic_threshold=0.8,
                llm_retry_count=0,
            )

        assert result == []
        assert "Skill routing LLM judge validation failed" in caplog.text
        assert "attempt=1" in caplog.text


class TestQdrantSkillVectorIndexWarmup:
    @pytest.mark.asyncio
    async def test_warmup_skips_embedding_when_qdrant_has_same_source_hash(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["cal"]
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(
            embedding_provider,
            existing_points=[
                {
                    "payload": {
                        "skill_name": "cal",
                        "source_hash": skill.source_hash,
                    }
                }
            ],
        )

        await index.warmup(registry)

        assert embedding_provider.texts == []
        assert index.upserted_points == []

    @pytest.mark.asyncio
    async def test_warmup_embeds_and_upserts_changed_skill(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(
            embedding_provider,
            existing_points=[
                {
                    "payload": {
                        "skill_name": "cal",
                        "source_hash": "old-hash",
                    }
                }
            ],
        )

        await index.warmup(registry)

        assert embedding_provider.texts == ["cal\nPerforms calendar arithmetic.\n今天, tomorrow, 星期几"]
        assert len(index.upserted_points) == 1
        assert index.upserted_points[0]["payload"]["skill_name"] == "cal"

    @pytest.mark.asyncio
    async def test_warmup_logs_qdrant_sync_summary(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(embedding_provider, existing_points=[])

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            await index.warmup(registry)

        assert "Qdrant skill vector sync started" in caplog.text
        assert "Qdrant skill vector sync completed" in caplog.text
        assert "upserted=1" in caplog.text

    @pytest.mark.asyncio
    async def test_search_logs_qdrant_recall_results(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["cal"]
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(
            embedding_provider,
            existing_points=[
                {
                    "payload": {
                        "skill_name": "cal",
                        "source_hash": skill.source_hash,
                    }
                }
            ],
            search_results=[
                {
                    "score": 0.91,
                    "payload": {
                        "skill_name": "cal",
                        "description": "Performs calendar arithmetic.",
                    },
                }
            ],
        )

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            candidates = await index.search(registry, "明天是星期几", top_k=3)

        assert [candidate.name for candidate in candidates] == ["cal"]
        assert "Qdrant skill vector search started" in caplog.text
        assert "Qdrant skill vector search completed" in caplog.text
        assert "candidates=cal:0.9100" in caplog.text

    def test_qdrant_http_error_includes_collection_and_endpoint(self, monkeypatch):
        embedding_provider = CountingEmbeddingProvider()
        index = QdrantSkillVectorIndex(
            embedding_provider,
            url="http://qdrant.example.test:6333",
            collection="skill_routes",
        )

        def raise_404(request, timeout):
            raise HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"status":{"error":"Not found: Collection skill_routes"}}'),
            )

        monkeypatch.setattr("urllib.request.urlopen", raise_404)

        with pytest.raises(RuntimeError) as exc_info:
            index._scroll_sync()

        message = str(exc_info.value)
        assert "Qdrant request failed" in message
        assert "collection=skill_routes" in message
        assert "POST /collections/skill_routes/points/scroll" in message
        assert "HTTP 404" in message
        assert "Collection skill_routes" in message


class TestOllamaBgeM3Reranker:
    def test_reranker_scores_candidates_with_ollama_embed_response(self, monkeypatch):
        requests: list[dict] = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"embeddings": [[0.91]]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            requests.append(
                {
                    "url": request.full_url,
                    "body": json.loads(request.data.decode("utf-8")),
                    "timeout": timeout,
                }
            )
            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        reranker = OllamaBgeM3Reranker(
            base_url="http://ollama.example.test:11434",
            model="custom-reranker",
            timeout_seconds=3,
        )

        result = reranker._rerank_sync(
            "date question",
            [SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.12)],
        )

        assert result == [
            SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.91)
        ]
        assert requests == [
            {
                "url": "http://ollama.example.test:11434/api/embed",
                "body": {
                    "model": "custom-reranker",
                    "input": "query: date question\npassage: cal\nCalendar math.",
                },
                "timeout": 3,
            }
        ]

    def test_reranker_rejects_model_without_embedding_capability(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "models": [
                            {
                                "name": "qllama/bge-reranker-v2-m3:latest",
                                "model": "qllama/bge-reranker-v2-m3:latest",
                                "capabilities": ["completion"],
                            }
                        ]
                    }
                ).encode("utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
        reranker = OllamaBgeM3Reranker(
            base_url="http://ollama.example.test:11434",
            model="qllama/bge-reranker-v2-m3",
            timeout_seconds=3,
        )

        with pytest.raises(RuntimeError) as exc_info:
            reranker._rerank_sync(
                "date question",
                [SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.12)],
            )

        message = str(exc_info.value)
        assert "does not advertise embedding capability" in message
        assert "capabilities=completion" in message

    def test_reranker_http_error_includes_response_body(self, monkeypatch):
        def raise_500(request, timeout):
            raise HTTPError(
                request.full_url,
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b'{"error":"llama-server process has terminated"}'),
            )

        def fake_urlopen(request, timeout):
            if request.full_url.endswith("/api/tags"):
                class FakeTagsResponse:
                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, traceback):
                        return False

                    def read(self):
                        return json.dumps(
                            {
                                "models": [
                                    {
                                        "name": "custom-reranker:latest",
                                        "model": "custom-reranker:latest",
                                        "capabilities": ["embedding"],
                                    }
                                ]
                            }
                        ).encode("utf-8")

                return FakeTagsResponse()
            raise_500(request, timeout)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        reranker = OllamaBgeM3Reranker(
            base_url="http://ollama.example.test:11434",
            model="custom-reranker",
        )

        with pytest.raises(RuntimeError) as exc_info:
            reranker._rerank_sync(
                "date question",
                [SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.12)],
            )

        message = str(exc_info.value)
        assert "HTTP 500 Internal Server Error" in message
        assert "llama-server process has terminated" in message


class TestRouteSkillsNoFallback:
    """route_skills must not force-load all skills when nothing matches."""

    @pytest.mark.asyncio
    async def test_no_match_loads_no_skills(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        state = await router({"messages": [], "selected_skills": []})
        # Nothing matched "random unrelated text"
        assert state["selected_skills"] == []
        # Skill stays unloaded — only meta overview is in the system prompt
        assert not registry.skills["cal"].loaded

    @pytest.mark.asyncio
    async def test_match_loads_only_matched_skill(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        # add a second skill that won't match
        other = tmp_path / "other"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: other\ndescription: Other stuff.\n---\n# Other\n", encoding="utf-8"
        )
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        from langchain_core.messages import HumanMessage

        state = await router({"messages": [HumanMessage(content="今天怎么样")]})
        assert state["selected_skills"] == ["cal"]
        assert registry.skills["cal"].loaded
        assert not registry.skills["other"].loaded

    @pytest.mark.asyncio
    async def test_system_prompt_omits_skill_meta_when_unmatched(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        state = await router({"messages": [], "selected_skills": []})
        system = state["messages"][0]
        assert "Available Skills" not in system.content
        assert "- **cal**" not in system.content
        assert "Performs calendar arithmetic." not in system.content


class TestBuildSystemPrompt:
    def test_omits_skill_meta_when_none_selected(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[])
        content = msg.content
        assert "Available Skills" not in content
        assert "skill-a" not in content
        assert "Skill Alpha" not in content
        assert "skill-b" not in content
        assert "Skill Beta" not in content

    def test_includes_full_instructions_for_selected(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        msg = build_system_prompt(registry, selected=["skill-a"])
        content = msg.content
        assert "## Available Skills" in content
        assert "- **skill-a**: Skill Alpha" in content
        assert "Available tools:" in content  # from SKILL.md full content
        assert "## Skill: skill-a" in content
        assert "skill-b" not in content
        assert "skill-c" not in content

    def test_base_preamble_present(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[])
        assert "personal assistant" in msg.content.lower()

    def test_unselected_skills_are_not_in_prompt(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        msg = build_system_prompt(registry, selected=["skill-a"])
        content = msg.content
        # skill-a should have full instructions section
        assert "## Skill: skill-a" in content
        assert "- **skill-a**: Skill Alpha" in content
        assert "skill-b" not in content
        assert "Skill Beta" not in content
        assert "## Skill: skill-b" not in content


class TestMemoryInjection:
    def test_system_prompt_includes_memory_when_store_provided(self, multi_skill_dir: Path, tmp_path: Path):
        registry = SkillRegistry(multi_skill_dir)
        memory_store = LongTermMemoryStore(tmp_path / ".memory")
        memory_store.ensure_files()
        (tmp_path / ".memory" / "USER.md").write_text(
            "# User\n\nCall me Yazuki.\n", encoding="utf-8"
        )

        msg = build_system_prompt(registry, selected=[], long_term_memory=memory_store)
        assert "Yazuki" in msg.content

    def test_system_prompt_works_without_memory_store(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[], long_term_memory=None)
        assert "personal assistant" in msg.content.lower()

    def test_memory_appears_before_skills_in_prompt(self, multi_skill_dir: Path, tmp_path: Path):
        registry = SkillRegistry(multi_skill_dir)
        memory_store = LongTermMemoryStore(tmp_path / ".memory")
        memory_store.add_memory(
            slug="test-mem",
            title="Test Memory",
            summary="A test memory",
            body="This is a test memory entry.",
        )

        msg = build_system_prompt(registry, selected=[], long_term_memory=memory_store)
        content = msg.content
        assert "Test Memory" in content
        assert "Available Skills" not in content
