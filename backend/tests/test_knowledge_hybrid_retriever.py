"""Tests for HybridRetriever."""
from unittest.mock import MagicMock, AsyncMock
import pytest
from personal_assistant.knowledge.hybrid_retriever import HybridRetriever, reciprocal_rank_fusion
from personal_assistant.knowledge.models import (
    KnowledgeRetrievalResult,
    RelevanceFilterResult,
    RelevanceVerdict,
    SearchResult,
)
from personal_assistant.knowledge.retriever import KnowledgeRetriever


class TestRRF:
    def test_rrf_combines_two_lists(self):
        vector = [("c1", 0.9), ("c2", 0.7)]
        bm25 = [("c2", 0.8), ("c3", 0.6)]
        fused = reciprocal_rank_fusion(vector, bm25, k=60)
        # c2 appears in both → should score highest
        assert fused[0][0] == "c2"
        assert len(fused) == 3  # c1, c2, c3

    def test_rrf_deduplicates(self):
        vector = [("c1", 0.9)]
        bm25 = [("c1", 0.8)]
        fused = reciprocal_rank_fusion(vector, bm25, k=60)
        assert len(fused) == 1
        assert fused[0][0] == "c1"

    def test_rrf_empty_vector(self):
        fused = reciprocal_rank_fusion([], [("c1", 0.9)], k=60)
        assert len(fused) == 1
        assert fused[0][0] == "c1"

    def test_rrf_both_empty(self):
        fused = reciprocal_rank_fusion([], [], k=60)
        assert fused == []

    def test_rrf_score_range(self):
        """RRF scores should be positive and small."""
        vector = [("c1", 0.9), ("c2", 0.7), ("c3", 0.5)]
        bm25 = [("c2", 0.8), ("c4", 0.6)]
        fused = reciprocal_rank_fusion(vector, bm25, k=60)
        for _, score in fused:
            assert 0 < score < 0.1  # RRF scores with k=60 are small


class TestHybridRetriever:
    def _make_doc(self, idx: int, title: str) -> SearchResult:
        return SearchResult(
            chunk_id=f"doc-{idx}#chunk-{idx:03d}",
            doc_id=f"doc-{idx}",
            score=0.9 - idx * 0.1,
            content=f"Content {idx}: {title}",
            title=title,
            source_attribution=f"来源：文档《{title}》，版本 v1.0，更新日期 2026-07-06，片段 1/1",
            metadata={"source_file": f"knowledge/0{idx}.md", "category": "apm_knowledge"},
        )

    def _make_doc_tuple(self, idx: int, title: str) -> tuple[str, str]:
        return (f"doc-{idx}#chunk-{idx:03d}", f"Content {idx}: {title}")

    @pytest.mark.asyncio
    async def test_retrieve_hybrid_success(self):
        """Happy path: vector + BM25 → RRF → filter → results."""
        vector_retriever = MagicMock(spec=KnowledgeRetriever)
        vector_retriever.retrieve = AsyncMock(return_value=KnowledgeRetrievalResult(
            status="completed",
            documents=[
                self._make_doc(0, "告警分级"),
                self._make_doc(1, "Trace查询"),
            ],
        ))
        vector_retriever.top_k = 5

        bm25 = MagicMock()
        bm25.search.return_value = [
            ("doc-0#chunk-000", 0.95),
            ("doc-1#chunk-001", 0.7),
        ]

        relevance_filter = MagicMock()
        relevance_filter.filter = AsyncMock(return_value=RelevanceFilterResult(
            all_relevant=True,
            verdicts=[
                RelevanceVerdict(document_index=0, relevant=True, reason="匹配"),
                RelevanceVerdict(document_index=1, relevant=True, reason="匹配"),
            ],
            filtered_documents=[
                self._make_doc(0, "告警分级"),
                self._make_doc(1, "Trace查询"),
            ],
        ))

        hybrid = HybridRetriever(
            vector_retriever=vector_retriever,
            bm25=bm25,
            relevance_filter=relevance_filter,
            top_k=5,
        )

        result = await hybrid.retrieve("P0告警与Trace查询")

        assert result.status == "completed"
        assert len(result.documents) == 2

    @pytest.mark.asyncio
    async def test_retrieve_all_filtered_out(self):
        """All docs filtered out → trust_signal set."""
        vector_retriever = MagicMock(spec=KnowledgeRetriever)
        vector_retriever.retrieve = AsyncMock(return_value=KnowledgeRetrievalResult(
            status="completed",
            documents=[self._make_doc(0, "告警分级")],
        ))
        vector_retriever.top_k = 5

        bm25 = MagicMock()
        bm25.search.return_value = [("doc-0#chunk-000", 0.9)]

        relevance_filter = MagicMock()
        relevance_filter.filter = AsyncMock(return_value=RelevanceFilterResult(
            all_relevant=False,
            verdicts=[RelevanceVerdict(document_index=0, relevant=False, reason="不匹配")],
            filtered_documents=[],
            no_knowledge_signal="⚠️ 知识库中无相关知识...",
        ))

        hybrid = HybridRetriever(
            vector_retriever=vector_retriever,
            bm25=bm25,
            relevance_filter=relevance_filter,
            top_k=5,
        )

        result = await hybrid.retrieve("完全不相关的问题")

        assert result.status == "completed"
        assert len(result.documents) == 0
        assert result.trust_signal == "NO_KNOWLEDGE_FOUND"

    @pytest.mark.asyncio
    async def test_retrieve_empty_query(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = await hybrid.retrieve("")
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_retrieve_no_results_either_source(self):
        """Both vector and BM25 return nothing → NO_KNOWLEDGE_FOUND."""
        vector_retriever = MagicMock(spec=KnowledgeRetriever)
        vector_retriever.retrieve = AsyncMock(return_value=KnowledgeRetrievalResult(
            status="completed", documents=[],
        ))
        vector_retriever.top_k = 5

        bm25 = MagicMock()
        bm25.search.return_value = []

        relevance_filter = MagicMock()

        hybrid = HybridRetriever(
            vector_retriever=vector_retriever,
            bm25=bm25,
            relevance_filter=relevance_filter,
            top_k=5,
        )

        result = await hybrid.retrieve("some query")

        assert result.status == "completed"
        assert result.trust_signal == "NO_KNOWLEDGE_FOUND"

    def test_format_for_llm(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = KnowledgeRetrievalResult(
            status="completed",
            documents=[
                SearchResult(
                    chunk_id="d#c-0", doc_id="d", score=0.9,
                    content="知识内容", title="T",
                    source_attribution="来源：文档《T》，版本 v1.0，更新日期 2026-07-06，片段 1/1",
                    metadata={},
                )
            ],
        )
        formatted = hybrid.format_for_llm(result)
        assert "【相关知识" in formatted

    def test_format_for_llm_no_knowledge(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = KnowledgeRetrievalResult(
            status="completed",
            documents=[],
            trust_signal="NO_KNOWLEDGE_FOUND",
            reason="知识库中无相关知识。",
        )
        formatted = hybrid.format_for_llm(result)
        assert "知识库中无相关知识" in formatted

    def test_format_for_llm_skipped(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = KnowledgeRetrievalResult(status="skipped", documents=[])
        assert hybrid.format_for_llm(result) == ""
