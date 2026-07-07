"""BM25 keyword search for hybrid retrieval.

Uses ``rank_bm25.BM25Okapi`` for BM25 scoring and ``jieba`` for Chinese
tokenization. The index is kept in-memory and rebuilt on each import/upsert
of the knowledge base.
"""

from __future__ import annotations

import logging
import re

import jieba
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

# Regex to detect English/tech terms that should stay as-is tokens
_EN_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text.

    Strategy:
    1. Extract English/tech tokens (trace_id, P99, Jaeger) via regex
    2. Remove them from the text temporarily
    3. Run jieba on the remaining Chinese text
    4. Combine results, lowercase everything
    """
    if not text or not text.strip():
        return []

    # Extract English/technical terms
    en_terms = _EN_WORD_RE.findall(text)
    # Remove extracted terms from text
    chinese_part = _EN_WORD_RE.sub(" ", text)
    # Tokenize Chinese part
    cn_tokens = [t.strip() for t in jieba.lcut(chinese_part) if t.strip()]
    # Combine
    all_tokens = [t.lower() for t in en_terms] + [t.lower() for t in cn_tokens]
    # Filter empty and single-space tokens, single-char tokens
    return [t for t in all_tokens if t and t != " " and len(t) >= 1]


class BM25Searcher:
    """In-memory BM25 keyword search index.

    Maintains independent BM25 indices per collection name. The index is
    rebuilt each time ``index()`` is called for a given collection.
    """

    def __init__(self) -> None:
        self._index: dict[str, BM25Okapi] = {}
        self._corpus: dict[str, list[list[str]]] = {}  # tokenized docs
        self._chunk_ids: dict[str, list[str]] = {}      # parallel to corpus

    def index(self, collection: str, chunks: list[tuple[str, str]]) -> None:
        """Build (or replace) the BM25 index for *collection*.

        Args:
            collection: Qdrant collection name (e.g. ``"apm_knowledge"``).
            chunks: List of ``(chunk_id, text_content)`` pairs.
        """
        if not chunks:
            self._index.pop(collection, None)
            self._corpus.pop(collection, None)
            self._chunk_ids.pop(collection, None)
            return

        tokenized = [_tokenize(text) for _, text in chunks]
        self._corpus[collection] = tokenized
        self._chunk_ids[collection] = [cid for cid, _ in chunks]
        self._index[collection] = BM25Okapi(tokenized)
        logger.info(
            "BM25 index built: collection=%s docs=%d",
            collection, len(tokenized),
        )

    def search(
        self, collection: str, query: str, top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """Keyword search against the BM25 index.

        Args:
            collection: Qdrant collection name.
            query: Raw user query string.
            top_k: Max number of results.

        Returns:
            List of ``(chunk_id, score)`` sorted descending by score.
            Returns empty list if the collection has no index.
        """
        index = self._index.get(collection)
        if index is None:
            return []

        tokenized_query = _tokenize(query)
        if not tokenized_query:
            return []

        scores = index.get_scores(tokenized_query)

        chunk_ids = self._chunk_ids[collection]
        assert len(scores) == len(chunk_ids), \
            f"Score count {len(scores)} != chunk count {len(chunk_ids)}"

        # Clamp negative scores to 0 (BM25 IDF can go negative when a term
        # appears in >50% of documents)
        clamped = [max(0.0, float(s)) for s in scores]

        scored = list(zip(chunk_ids, clamped))
        scored.sort(key=lambda x: x[1], reverse=True)

        # Normalize BM25 scores to [0, 1] range for easier fusion
        max_score = scored[0][1] if scored else 1.0
        if max_score > 0:
            scored = [(cid, s / max_score) for cid, s in scored]

        return scored[:max(top_k, 1)]
