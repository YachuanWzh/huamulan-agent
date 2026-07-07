"""RAG knowledge management system for APM alert analysis.

Provides document chunking, Qdrant CRUD, import, retrieval with source
attribution, hybrid (vector + BM25) retrieval, LLM relevance filtering,
and evaluation interfaces for the knowledge base.
"""

from personal_assistant.knowledge.bm25_searcher import BM25Searcher
from personal_assistant.knowledge.chunker import ChunkConfig, MarkdownChunker
from personal_assistant.knowledge.evaluation import NoopEvaluator, RAGEvaluator
from personal_assistant.knowledge.hybrid_retriever import HybridRetriever, reciprocal_rank_fusion
from personal_assistant.knowledge.importer import KnowledgeImporter
from personal_assistant.knowledge.models import (
    Chunk,
    DocMeta,
    GenerationMetrics,
    KnowledgeRetrievalResult,
    RelevanceFilterResult,
    RelevanceVerdict,
    RetrievalMetrics,
    SearchResult,
    SourceAttribution,
)
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from personal_assistant.knowledge.relevance_filter import NO_KNOWLEDGE_MESSAGE, RelevanceFilter
from personal_assistant.knowledge.retriever import KnowledgeRetriever

__all__ = [
    "BM25Searcher",
    "Chunk",
    "ChunkConfig",
    "DocMeta",
    "GenerationMetrics",
    "HybridRetriever",
    "KnowledgeImporter",
    "KnowledgeRetrievalResult",
    "KnowledgeRetriever",
    "MarkdownChunker",
    "NO_KNOWLEDGE_MESSAGE",
    "NoopEvaluator",
    "QdrantKnowledgeStore",
    "RAGEvaluator",
    "RelevanceFilter",
    "RelevanceFilterResult",
    "RelevanceVerdict",
    "RetrievalMetrics",
    "SearchResult",
    "SourceAttribution",
    "reciprocal_rank_fusion",
]


def build_knowledge_retriever(settings) -> "KnowledgeRetriever | None":
    """Build a KnowledgeRetriever from application settings.

    Returns ``None`` when ``KNOWLEDGE_RAG_ENABLED`` is ``False`` or the
    Qdrant URL is not configured.
    """
    enabled = bool(getattr(settings, "knowledge_rag_enabled", False))
    if not enabled:
        return None
    qdrant_url = getattr(settings, "knowledge_qdrant_url", None)
    if not qdrant_url:
        return None

    from personal_assistant.agent.router import OllamaBgeM3EmbeddingProvider

    embedding_provider = OllamaBgeM3EmbeddingProvider(
        base_url=getattr(
            settings,
            "skill_routing_ollama_base_url",
            "http://localhost:11434",
        ),
        model=getattr(
            settings,
            "skill_routing_embedding_model",
            "bge-m3",
        ),
    )
    store = QdrantKnowledgeStore(
        url=qdrant_url,
        collection=getattr(settings, "knowledge_qdrant_collection", "apm_knowledge"),
        api_key=getattr(settings, "knowledge_qdrant_api_key", None),
    )
    return KnowledgeRetriever(
        store=store,
        embedding_provider=embedding_provider,
        top_k=int(getattr(settings, "knowledge_retrieval_top_k", 5) or 5),
    )


def build_hybrid_retriever(settings, llm=None) -> "HybridRetriever | None":
    """Build a HybridRetriever when hybrid retrieval is enabled.

    Returns ``None`` when ``KNOWLEDGE_HYBRID_ENABLED`` is ``False`` or the
    base KnowledgeRetriever cannot be built.

    Args:
        settings: Application settings.
        llm: Optional LangChain chat model for relevance filter (deepseek-v4-flash).
    """
    enabled = bool(getattr(settings, "knowledge_hybrid_enabled", False))
    if not enabled:
        return None

    base = build_knowledge_retriever(settings)
    if base is None:
        return None

    bm25 = BM25Searcher()

    # Populate BM25 index from existing Qdrant chunk content
    try:
        collection = getattr(settings, "knowledge_qdrant_collection", "apm_knowledge")
        chunks = base.store.scroll_chunks()
        if chunks:
            bm25.index(collection, chunks)
    except Exception:
        # BM25 index population is best-effort; hybrid degrades to
        # vector-only search when the index is cold
        pass

    relevance_filter = None
    filter_enabled = bool(getattr(settings, "knowledge_relevance_filter_enabled", True))
    if filter_enabled and llm is not None:
        relevance_filter = RelevanceFilter(llm=llm)

    return HybridRetriever(
        vector_retriever=base,
        bm25=bm25,
        relevance_filter=relevance_filter,
        top_k=int(getattr(settings, "knowledge_retrieval_top_k", 5) or 5),
        collection=getattr(settings, "knowledge_qdrant_collection", "apm_knowledge"),
    )
