"""Pydantic data models for the RAG knowledge management system."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class DocMeta(BaseModel):
    """Per-document metadata stored in Qdrant payload.

    Used to track document lifecycle: version, update time, content hash
    for detecting changes and triggering re-indexing.
    """

    doc_id: str
    """Stable unique identifier — uuid5(NAMESPACE_URL, source_file)."""

    title: str
    """Document title extracted from the first H1 heading."""

    source_file: str
    """Relative path to the source markdown, e.g. ``knowledge/01-xxx.md``."""

    version: str = "v1.0"
    """Version string, incremented when the document is updated."""

    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    """ISO-8601 timestamp of the last update."""

    category: str = "apm_knowledge"
    """Knowledge category for filtering / grouping."""

    total_chunks: int = 0
    """Number of chunks this document was split into."""

    content_hash: str = ""
    """MD5 hash of the source file content, used for change detection."""


class Chunk(BaseModel):
    """A single chunk of a knowledge document, ready for embedding."""

    chunk_id: str
    """Unique chunk identifier: ``{doc_id}#chunk-{index:03d}``."""

    doc_id: str
    """Parent document ID."""

    chunk_index: int
    """Zero-based index within the document."""

    title: str
    """The closest H2 heading for context identification."""

    content: str
    """Chunk body text, including context buffer from surrounding chunks."""

    context_preview: str = ""
    """First ~120 chars of content for quick browsing / audit."""

    raw_content: str = ""
    """Original chunk content without context buffer, for precise display."""

    def model_post_init(self, __context: Any) -> None:
        if not self.context_preview:
            preview = self.content.strip()[:120]
            self.context_preview = preview


class SourceAttribution(BaseModel):
    """Formatted source citation for a retrieved chunk."""

    title: str
    version: str
    updated_at: str
    chunk_index: int
    total_chunks: int

    def format(self) -> str:
        """Return formatted Chinese citation string.

        Example:
            来源：文档《告警分级体系与路由策略》，版本 v1.0，更新日期 2026-07-06，片段 3/7
        """
        display_index = self.chunk_index + 1  # 1-based for humans
        return (
            f"来源：文档《{self.title}》，"
            f"版本 {self.version}，"
            f"更新日期 {self.updated_at[:10]}，"
            f"片段 {display_index}/{self.total_chunks}"
        )


class SearchResult(BaseModel):
    """One retrieved chunk with score, content, and source attribution."""

    chunk_id: str
    doc_id: str
    score: float
    content: str
    title: str
    source_attribution: str  # Pre-formatted citation string
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRetrievalResult(BaseModel):
    """Full response from a knowledge retrieval operation."""

    status: str  # "completed" | "skipped" | "failed"
    documents: list[SearchResult] = Field(default_factory=list)
    reason: str = ""
    trust_signal: str = ""
    """When set to ``"NO_KNOWLEDGE_FOUND"``, indicates all retrieved docs were
    filtered out by the relevance check. Downstream agents should respect this
    and not produce free-form answers."""


class RetrievalMetrics(BaseModel):
    """RAG retrieval evaluation metrics (reserved for future use)."""

    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0  # Mean Reciprocal Rank
    hit_rate: float = 0.0


class GenerationMetrics(BaseModel):
    """RAG generation evaluation metrics (reserved for future use)."""

    faithfulness: float = 0.0
    answer_relevancy: float = 0.0


class RelevanceVerdict(BaseModel):
    """LLM judgment on whether a single retrieved chunk is relevant to the query."""

    document_index: int
    """Index into the original search results list."""

    relevant: bool
    """True if the chunk's metadata (source, title, category) aligns with the query."""

    reason: str
    """Brief Chinese explanation of the relevance / irrelevance."""


class RelevanceFilterResult(BaseModel):
    """Result of running the relevance filter over a set of retrieved documents."""

    all_relevant: bool
    """True if at least one document passed the filter."""

    verdicts: list[RelevanceVerdict]
    """Per-document verdicts from the LLM."""

    filtered_documents: list["SearchResult"]
    """Subset of input documents that passed the relevance check."""

    no_knowledge_signal: str = ""
    """When ``all_relevant`` is False, this contains the instruction to inject
    into the downstream agent's context to prevent free-form answers."""
