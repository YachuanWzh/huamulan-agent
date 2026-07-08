"""Hybrid retriever combining vector search, BM25 keyword search, and LLM relevance filtering.

The retrieval pipeline:
1. Embed query → vector search in Qdrant
2. BM25 keyword search (in-memory index)
3. RRF (Reciprocal Rank Fusion) to merge results
4. Relevance filter (deepseek-v4-flash) to verify metadata alignment
5. Return results with ``trust_signal`` for downstream agents
"""

from __future__ import annotations

import logging
from typing import Any

from personal_assistant.knowledge.bm25_searcher import BM25Searcher
from personal_assistant.knowledge.models import (
    KnowledgeRetrievalResult,
    SearchResult,
)
from personal_assistant.knowledge.relevance_filter import RelevanceFilter
from personal_assistant.knowledge.retriever import KnowledgeRetriever

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    vector_results: list[tuple[str, float]],
    bm25_results: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Combine two ranked result lists using Reciprocal Rank Fusion.

    RRF score = Σ 1/(k + rank_in_list) for each list the item appears in.

    Args:
        vector_results: ``(chunk_id, score)`` from vector search.
        bm25_results: ``(chunk_id, score)`` from BM25 search.
        k: RRF constant (default 60, standard value).

    Returns:
        Combined ``(chunk_id, rrf_score)`` sorted descending.
    """
    scores: dict[str, float] = {}

    for rank, (chunk_id, _) in enumerate(vector_results, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

    for rank, (chunk_id, _) in enumerate(bm25_results, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused


class HybridRetriever:
    """Retriever that fuses vector + BM25 results and filters by relevance.

    Designed as a drop-in replacement for ``KnowledgeRetriever`` in the
    multi-agent flow. The same ``retrieve() → KnowledgeRetrievalResult``
    interface, plus ``format_for_llm()`` aware of the trust signal.
    """

    def __init__(
        self,
        *,
        vector_retriever: KnowledgeRetriever,
        bm25: BM25Searcher,
        relevance_filter: RelevanceFilter | None = None,
        top_k: int = 5,
        collection: str = "apm_knowledge",
    ) -> None:
        self._vector = vector_retriever
        self._bm25 = bm25
        self._filter = relevance_filter
        self.top_k = top_k
        self.collection = collection

    async def retrieve(self, query: str) -> KnowledgeRetrievalResult:
        """Run the hybrid retrieval pipeline.

        Steps:
        1. Vector search (via KnowledgeRetriever)
        2. BM25 keyword search
        3. RRF fusion
        4. Relevance filter (if configured)
        """
        if not query.strip():
            return KnowledgeRetrievalResult(status="skipped", reason="empty query")

        # Step 1: Vector search
        vector_docs: list[SearchResult] = []
        try:
            vec_result = await self._vector.retrieve(query)
            vector_docs = vec_result.documents
        except Exception as exc:
            logger.warning("Vector retrieval failed: %s", exc)

        # Step 2: BM25 keyword search
        bm25_scored: list[tuple[str, float]] = []
        try:
            bm25_scored = self._bm25.search(self.collection, query, top_k=self.top_k)
        except Exception as exc:
            logger.warning("BM25 search failed: %s", exc)

        # Step 3: RRF fusion
        vector_scored = [(d.chunk_id, d.score) for d in vector_docs]
        fused = reciprocal_rank_fusion(vector_scored, bm25_scored)

        bm25_ids = {cid for cid, _ in bm25_scored}
        vector_ids = {cid for cid, _ in vector_scored}
        bm25_only_ids = bm25_ids - vector_ids
        overlap_ids = bm25_ids & vector_ids

        # Step 4: Map fused back to SearchResult objects
        doc_map: dict[str, SearchResult] = {d.chunk_id: d for d in vector_docs}
        merged: list[SearchResult] = []
        bm25_only_merged = 0
        overlap_merged = 0
        for chunk_id, rrf_score in fused[:self.top_k]:
            if chunk_id in doc_map:
                doc = doc_map[chunk_id]
                is_overlap = chunk_id in overlap_ids
                if is_overlap:
                    overlap_merged += 1
                merged.append(SearchResult(
                    chunk_id=doc.chunk_id,
                    doc_id=doc.doc_id,
                    score=round(rrf_score, 6),
                    content=doc.content,
                    title=doc.title,
                    source_attribution=doc.source_attribution,
                    metadata=doc.metadata,
                ))
            else:
                # BM25-only result: limited metadata
                bm25_only_merged += 1
                merged.append(SearchResult(
                    chunk_id=chunk_id,
                    doc_id=chunk_id.rsplit("#", 1)[0] if "#" in chunk_id else chunk_id,
                    score=round(rrf_score, 6),
                    content="",
                    title="(BM25 keyword match)",
                    source_attribution="",
                    metadata={},
                ))

        logger.info(
            "Hybrid retrieval: vector=%d BM25=%d fused=%d | "
            "BM25-only=%d overlap=%d | top_k_final=(bm25_only=%d overlap=%d vector_only=%d)",
            len(vector_scored), len(bm25_scored), len(fused),
            len(bm25_only_ids), len(overlap_ids),
            bm25_only_merged, overlap_merged,
            len(merged) - bm25_only_merged - overlap_merged,
        )

        # Step 5: Relevance filter
        if self._filter and merged:
            try:
                filter_result = await self._filter.filter(query, merged)
                if not filter_result.all_relevant:
                    return KnowledgeRetrievalResult(
                        status="completed",
                        documents=[],
                        trust_signal="NO_KNOWLEDGE_FOUND",
                        reason=filter_result.no_knowledge_signal,
                    )
                merged = filter_result.filtered_documents
            except Exception as exc:
                logger.warning("Relevance filter failed, passing all docs through: %s", exc)

        # Step 6: All-empty check
        if not merged and not vector_docs and not bm25_scored:
            return KnowledgeRetrievalResult(
                status="completed",
                documents=[],
                trust_signal="NO_KNOWLEDGE_FOUND",
                reason="no results from either vector or BM25 search",
            )

        return KnowledgeRetrievalResult(status="completed", documents=merged)

    def format_for_llm(self, result: KnowledgeRetrievalResult) -> str:
        """Format retrieval results for LLM context, respecting trust_signal.

        When trust_signal is NO_KNOWLEDGE_FOUND, returns the signal message
        instead of an empty string so the agent sees the constraint.
        """
        if result.trust_signal == "NO_KNOWLEDGE_FOUND":
            return result.reason or "知识库中无相关知识。"

        if result.status != "completed" or not result.documents:
            return ""

        parts: list[str] = []
        for i, doc in enumerate(result.documents, 1):
            parts.append(
                f"【相关知识 {i}】（综合得分：{doc.score:.4f}）\n"
                f"{doc.source_attribution}\n"
                f"章节：{doc.title}\n"
                f"---\n"
                f"{doc.content}\n"
                f"---"
            )
        return "\n\n".join(parts)
