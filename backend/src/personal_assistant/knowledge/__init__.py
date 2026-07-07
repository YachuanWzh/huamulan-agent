"""RAG knowledge management system for APM alert analysis.

Provides document chunking, Qdrant CRUD, import, retrieval with source
attribution, and evaluation interfaces for the knowledge base.
"""

from personal_assistant.knowledge.chunker import ChunkConfig, MarkdownChunker
from personal_assistant.knowledge.evaluation import NoopEvaluator, RAGEvaluator
from personal_assistant.knowledge.importer import KnowledgeImporter
from personal_assistant.knowledge.models import (
    Chunk,
    DocMeta,
    GenerationMetrics,
    KnowledgeRetrievalResult,
    RetrievalMetrics,
    SearchResult,
    SourceAttribution,
)
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from personal_assistant.knowledge.retriever import KnowledgeRetriever

__all__ = [
    "Chunk",
    "ChunkConfig",
    "DocMeta",
    "GenerationMetrics",
    "KnowledgeImporter",
    "KnowledgeRetrievalResult",
    "KnowledgeRetriever",
    "MarkdownChunker",
    "NoopEvaluator",
    "QdrantKnowledgeStore",
    "RAGEvaluator",
    "RetrievalMetrics",
    "SearchResult",
    "SourceAttribution",
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
