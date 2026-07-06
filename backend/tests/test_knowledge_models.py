"""Tests for knowledge models (DocMeta, Chunk, SearchResult, SourceAttribution)."""
import json
import uuid

import pytest
from pydantic import ValidationError

from personal_assistant.knowledge.models import (
    Chunk,
    DocMeta,
    GenerationMetrics,
    KnowledgeRetrievalResult,
    RetrievalMetrics,
    SearchResult,
    SourceAttribution,
)


class TestDocMeta:
    def test_create_minimal(self) -> None:
        meta = DocMeta(
            doc_id=str(uuid.uuid4()),
            title="Test Doc",
            source_file="knowledge/test.md",
        )
        assert meta.version == "v1.0"
        assert meta.category == "apm_knowledge"
        assert meta.updated_at is not None
        assert meta.total_chunks == 0
        assert meta.content_hash == ""

    def test_create_full(self) -> None:
        meta = DocMeta(
            doc_id="doc-001",
            title="告警分级体系",
            source_file="knowledge/01-alert.md",
            version="v2.0",
            updated_at="2026-07-06",
            category="apm_knowledge",
            total_chunks=8,
            content_hash="abc123",
        )
        assert meta.version == "v2.0"
        assert meta.total_chunks == 8

    def test_serialization(self) -> None:
        meta = DocMeta(
            doc_id="doc-001",
            title="Test",
            source_file="knowledge/test.md",
            updated_at="2026-07-06T10:00:00",
        )
        d = meta.model_dump()
        assert d["doc_id"] == "doc-001"
        assert d["title"] == "Test"
        assert d["category"] == "apm_knowledge"

    def test_doc_id_required(self) -> None:
        with pytest.raises(ValidationError):
            DocMeta(title="No ID", source_file="test.md")  # type: ignore[arg-type]

    def test_title_required(self) -> None:
        with pytest.raises(ValidationError):
            DocMeta(doc_id="d1", source_file="test.md")  # type: ignore[arg-type]


class TestChunk:
    def test_create(self) -> None:
        chunk = Chunk(
            chunk_id="doc-001#chunk-003",
            doc_id="doc-001",
            chunk_index=3,
            title="双通道分流设计",
            content="P0/P1 和 P2/P3 走两条不同的数据通道...",
            context_preview="P0/P1 和 P2/P3 走两条不同的...",
            raw_content="原始内容无缓冲区",
        )
        assert chunk.chunk_index == 3
        assert "doc-001" in chunk.chunk_id

    def test_context_preview_truncation(self) -> None:
        """context_preview should be reasonably short."""
        long_content = "A" * 500
        chunk = Chunk(
            chunk_id="d#c-0",
            doc_id="d",
            chunk_index=0,
            title="T",
            content=long_content,
        )
        assert len(chunk.context_preview) <= 120

    def test_defaults(self) -> None:
        chunk = Chunk(
            chunk_id="d#c-0",
            doc_id="d",
            chunk_index=0,
            title="T",
            content="content",
        )
        assert chunk.raw_content == ""
        assert len(chunk.context_preview) <= 120


class TestSearchResult:
    def test_create(self) -> None:
        attribution = SourceAttribution(
            title="告警分级体系与路由策略",
            version="v1.0",
            updated_at="2026-07-06",
            chunk_index=2,
            total_chunks=7,
        )
        result = SearchResult(
            chunk_id="doc-001#chunk-002",
            doc_id="doc-001",
            score=0.87,
            content="这是召回的内容...",
            title="双通道分流设计",
            source_attribution=attribution.format(),
            metadata={"category": "apm_knowledge"},
        )
        assert result.score == 0.87
        assert "来源" in result.source_attribution
        assert "告警分级体系与路由策略" in result.source_attribution

    def test_source_attribution_format(self) -> None:
        attr = SourceAttribution(
            title="测试文档",
            version="v2.0",
            updated_at="2026-07-06",
            chunk_index=4,
            total_chunks=10,
        )
        formatted = attr.format()
        assert "来源" in formatted
        assert "测试文档" in formatted
        assert "v2.0" in formatted
        assert "2026-07-06" in formatted
        assert "5/10" in formatted  # chunk_index 4 → display "5/10"


class TestKnowledgeRetrievalResult:
    def test_create_empty(self) -> None:
        result = KnowledgeRetrievalResult(status="completed", documents=[])
        assert result.status == "completed"
        assert result.documents == []

    def test_create_with_results(self) -> None:
        attr = SourceAttribution(
            title="T", version="v1.0", updated_at="2026-07-06",
            chunk_index=0, total_chunks=1,
        )
        sr = SearchResult(
            chunk_id="d#c-0", doc_id="d", score=0.9,
            content="C", title="T",
            source_attribution=attr.format(),
            metadata={},
        )
        result = KnowledgeRetrievalResult(status="completed", documents=[sr])
        assert len(result.documents) == 1

    def test_failed_status(self) -> None:
        result = KnowledgeRetrievalResult(
            status="failed", documents=[], reason="Qdrant 连接失败",
        )
        assert result.status == "failed"
        assert result.reason == "Qdrant 连接失败"

    def test_skipped_status(self) -> None:
        result = KnowledgeRetrievalResult(
            status="skipped", documents=[], reason="KNOWLEDGE_RAG_ENABLED=false",
        )
        assert result.status == "skipped"


class TestRetrievalMetrics:
    def test_create(self) -> None:
        m = RetrievalMetrics(precision_at_k=0.8, recall_at_k=0.75, mrr=0.9, hit_rate=1.0)
        assert m.precision_at_k == 0.8


class TestGenerationMetrics:
    def test_create(self) -> None:
        m = GenerationMetrics(faithfulness=0.9, answer_relevancy=0.85)
        assert m.faithfulness == 0.9
