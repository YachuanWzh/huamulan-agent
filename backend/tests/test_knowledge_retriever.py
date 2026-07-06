"""Tests for KnowledgeRetriever."""
from unittest.mock import MagicMock, AsyncMock

import pytest

from personal_assistant.knowledge.models import KnowledgeRetrievalResult, SearchResult
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from personal_assistant.knowledge.retriever import KnowledgeRetriever


class FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float]:
        return [0.1] * 1024


@pytest.fixture
def store():
    s = MagicMock(spec=QdrantKnowledgeStore)
    s.search.return_value = []
    return s


class TestKnowledgeRetriever:
    @pytest.mark.asyncio
    async def test_retrieve_empty_query(self, store):
        retriever = KnowledgeRetriever(store=store, embedding_provider=FakeEmbeddingProvider())
        result = await retriever.retrieve("")
        assert result.status == "skipped"
        assert result.documents == []

    @pytest.mark.asyncio
    async def test_retrieve_returns_attributed_results(self, store):
        store.search.return_value = [
            SearchResult(
                chunk_id="d#c-000", doc_id="d", score=0.95,
                content="P0/P1 走 webhook...",
                title="双通道分流设计",
                source_attribution="来源：文档《告警分级》，版本 v1.0，更新日期 2026-07-06，片段 1/8",
                metadata={},
            )
        ]
        retriever = KnowledgeRetriever(store=store, embedding_provider=FakeEmbeddingProvider())
        result = await retriever.retrieve("P0告警如何处理")
        assert result.status == "completed"
        assert len(result.documents) == 1
        assert "来源" in result.documents[0].source_attribution
        assert "告警分级" in result.documents[0].source_attribution

    @pytest.mark.asyncio
    async def test_retrieve_handles_embedding_failure(self, store):
        provider = MagicMock()
        provider.embed = AsyncMock(side_effect=RuntimeError("Ollama down"))

        retriever = KnowledgeRetriever(store=store, embedding_provider=provider)
        result = await retriever.retrieve("query")
        assert result.status == "failed"
        assert "embedding error" in result.reason

    @pytest.mark.asyncio
    async def test_retrieve_handles_store_failure(self, store):
        store.search.side_effect = RuntimeError("Qdrant down")

        retriever = KnowledgeRetriever(store=store, embedding_provider=FakeEmbeddingProvider())
        result = await retriever.retrieve("query")
        assert result.status == "failed"
        assert "Qdrant" in result.reason or "search error" in result.reason

    def test_format_for_llm(self, store):
        retriever = KnowledgeRetriever(store=store, embedding_provider=FakeEmbeddingProvider())
        result = KnowledgeRetrievalResult(
            status="completed",
            documents=[
                SearchResult(
                    chunk_id="d#c-000", doc_id="d", score=0.87,
                    content="这是知识内容...",
                    title="双通道分流设计",
                    source_attribution="来源：文档《告警分级》，版本 v1.0，更新日期 2026-07-06，片段 3/7",
                    metadata={},
                )
            ],
        )
        formatted = retriever.format_for_llm(result)
        assert "【相关知识 1】" in formatted
        assert "相似度：0.87" in formatted
        assert "来源" in formatted
        assert "双通道分流设计" in formatted

    def test_format_for_llm_skipped(self, store):
        retriever = KnowledgeRetriever(store=store, embedding_provider=FakeEmbeddingProvider())
        result = KnowledgeRetrievalResult(status="skipped", documents=[])
        assert retriever.format_for_llm(result) == ""

    def test_format_for_llm_empty(self, store):
        retriever = KnowledgeRetriever(store=store, embedding_provider=FakeEmbeddingProvider())
        result = KnowledgeRetrievalResult(status="failed", reason="error")
        assert retriever.format_for_llm(result) == ""
