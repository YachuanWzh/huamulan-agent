"""Knowledge retrieval with source attribution for multi-agent APM flow.

Replaces the raw Qdrant search in ``_retrieve_user_vector_context()`` with
structured, attributed results ready for LLM context injection.
"""
from __future__ import annotations

import logging
from typing import Any

from personal_assistant.knowledge.models import KnowledgeRetrievalResult
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """High-level retriever that searches the APM knowledge base and formats
    results with Chinese source citations.

    Designed as a drop-in enhancement for ``_retrieve_user_vector_context()``
    in ``multi_agent.py``. Same return shape: ``{status, documents}``.
    """

    def __init__(
        self,
        *,
        store: QdrantKnowledgeStore,
        embedding_provider,
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> None:
        self.store = store
        self.embedding_provider = embedding_provider
        self.top_k = top_k
        self.score_threshold = score_threshold

    async def retrieve(self, query: str) -> KnowledgeRetrievalResult:
        """Embed query → search Qdrant → return attributed results."""
        if not query.strip():
            return KnowledgeRetrievalResult(
                status="skipped", reason="empty query",
            )

        try:
            vector = await self.embedding_provider.embed(query)
        except Exception as exc:
            logger.warning("Embedding failed for knowledge retrieval: %s", exc)
            return KnowledgeRetrievalResult(
                status="failed", reason=f"embedding error: {exc}",
            )

        try:
            results = self.store.search(
                query_vector=vector,
                top_k=self.top_k,
                score_threshold=self.score_threshold,
            )
        except Exception as exc:
            logger.warning("Knowledge search failed: %s", exc)
            return KnowledgeRetrievalResult(
                status="failed", reason=f"Qdrant search error: {exc}",
            )

        return KnowledgeRetrievalResult(
            status="completed",
            documents=list(results),
        )

    def format_for_llm(self, result: KnowledgeRetrievalResult) -> str:
        """Format retrieval results as a string for injection into LLM context.

        Example output::

            【相关知识 1】（相似度：0.87）
            来源：文档《告警分级体系与路由策略》，版本 v1.0，更新日期 2026-07-06，片段 3/7
            章节：双通道分流设计
            ---
            [chunk content]
            ---
        """
        if result.status != "completed" or not result.documents:
            return ""

        parts: list[str] = []
        for i, doc in enumerate(result.documents, 1):
            parts.append(
                f"【相关知识 {i}】（相似度：{doc.score:.2f}）\n"
                f"{doc.source_attribution}\n"
                f"章节：{doc.title}\n"
                f"---\n"
                f"{doc.content}\n"
                f"---"
            )
        return "\n\n".join(parts)
