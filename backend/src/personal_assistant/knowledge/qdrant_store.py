"""Qdrant CRUD store for APM knowledge chunks.

Reuses the same ``urllib.request`` HTTP API pattern as
``QdrantSkillVectorIndex`` in ``router.py``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any

from personal_assistant.knowledge.models import (
    Chunk,
    DocMeta,
    SearchResult,
    SourceAttribution,
)


class QdrantKnowledgeStore:
    """CRUD operations for knowledge chunks in Qdrant."""

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    # ── Collection management ────────────────────────────────────────

    def ensure_collection(self, vector_size: int = 1024) -> None:
        """Create the collection if it does not exist (distance=Cosine)."""
        try:
            self._request_json("GET", f"/collections/{self.collection}")
        except RuntimeError:
            # Collection doesn't exist — create it
            self._request_json(
                "PUT",
                f"/collections/{self.collection}",
                {
                    "vectors": {
                        "size": vector_size,
                        "distance": "Cosine",
                    },
                },
            )

    # ── Upsert (Create / Update) ─────────────────────────────────────

    def upsert_chunks(
        self,
        doc: DocMeta,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> int:
        """Delete existing chunks for *doc*, then insert new ones.

        Returns the number of points upserted.
        """
        if not chunks:
            return 0

        # Delete existing points for this document
        self.delete_by_doc_id(doc.doc_id)

        # Build points
        points: list[dict[str, Any]] = []
        for chunk, vector in zip(chunks, vectors):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id))
            points.append({
                "id": point_id,
                "vector": vector,
                "payload": {
                    "content": chunk.content,
                    "raw_content": chunk.raw_content,
                    "doc_id": doc.doc_id,
                    "title": chunk.title,
                    "chunk_id": chunk.chunk_id,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": len(chunks),
                    "context_preview": chunk.context_preview,
                    "source_file": doc.source_file,
                    "version": doc.version,
                    "updated_at": doc.updated_at,
                    "category": doc.category,
                    "content_hash": doc.content_hash,
                },
            })

        self._upsert_sync(points)
        return len(points)

    # ── Search (Read) ────────────────────────────────────────────────

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[SearchResult]:
        """Vector similarity search, returning results with source attribution."""
        body: dict[str, Any] = {
            "vector": query_vector,
            "limit": max(1, top_k),
            "with_payload": True,
        }
        if score_threshold is not None:
            body["score_threshold"] = score_threshold

        response = self._search_sync(body)
        raw_results = response.get("result", [])
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue

            attribution = SourceAttribution(
                title=str(payload.get("title") or ""),
                version=str(payload.get("version") or "v1.0"),
                updated_at=str(payload.get("updated_at") or ""),
                chunk_index=int(payload.get("chunk_index") or 0),
                total_chunks=int(payload.get("total_chunks") or 1),
            )

            results.append(SearchResult(
                chunk_id=str(payload.get("chunk_id") or ""),
                doc_id=str(payload.get("doc_id") or ""),
                score=float(item.get("score") or 0.0),
                content=str(payload.get("content") or payload.get("raw_content") or ""),
                title=str(payload.get("title") or ""),
                source_attribution=attribution.format(),
                metadata={
                    "source_file": payload.get("source_file", ""),
                    "version": payload.get("version", ""),
                    "updated_at": payload.get("updated_at", ""),
                    "chunk_index": payload.get("chunk_index", 0),
                    "total_chunks": payload.get("total_chunks", 0),
                    "category": payload.get("category", ""),
                },
            ))

        return results

    # ── Delete ───────────────────────────────────────────────────────

    def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all chunks belonging to a document.

        Uses Qdrant's points/delete endpoint with a payload filter.
        Returns the number of deleted points from the response, or 0.
        """
        try:
            response = self._request_json(
                "POST",
                f"/collections/{self.collection}/points/delete",
                {
                    "filter": {
                        "must": [
                            {"key": "doc_id", "match": {"value": doc_id}},
                        ],
                    },
                },
            )
            result = response.get("result", {})
            if isinstance(result, dict):
                status_val = result.get("status")
                if isinstance(status_val, dict):
                    return int(status_val.get("ok", 0) or 0)
                # "ok" as string (Qdrant v1.x response format)
                return 1 if status_val == "ok" else 0
            return 0
        except RuntimeError:
            return 0

    # ── List ─────────────────────────────────────────────────────────

    def scroll_chunks(self) -> list[tuple[str, str]]:
        """Scroll all points and return ``(chunk_id, content)`` pairs.

        Used to populate the BM25 keyword index with actual chunk text.
        Returns raw content (not context-buffered) when available.
        """
        response = self._scroll_sync()
        result = response.get("result", {})
        points = result.get("points", []) if isinstance(result, dict) else []
        if not isinstance(points, list):
            return []

        chunks: list[tuple[str, str]] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            payload = point.get("payload")
            if not isinstance(payload, dict):
                continue
            chunk_id = str(payload.get("chunk_id") or "")
            # Prefer raw_content (no context buffer) for keyword matching
            content = str(
                payload.get("raw_content")
                or payload.get("content")
                or ""
            )
            if chunk_id and content:
                chunks.append((chunk_id, content))
        return chunks

    def list_docs(self) -> list[DocMeta]:
        """Scroll all points and return deduplicated document metadata."""
        response = self._scroll_sync()
        result = response.get("result", {})
        points = result.get("points", []) if isinstance(result, dict) else []
        if not isinstance(points, list):
            return []

        seen: set[str] = set()
        docs: list[DocMeta] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            payload = point.get("payload")
            if not isinstance(payload, dict):
                continue
            doc_id = str(payload.get("doc_id") or "")
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            docs.append(DocMeta(
                doc_id=doc_id,
                title=str(payload.get("title") or ""),
                source_file=str(payload.get("source_file") or ""),
                version=str(payload.get("version") or "v1.0"),
                updated_at=str(payload.get("updated_at") or ""),
                category=str(payload.get("category") or "apm_knowledge"),
                total_chunks=int(payload.get("total_chunks") or 0),
                content_hash=str(payload.get("content_hash") or ""),
            ))
        return docs

    def get_chunk_count(self, doc_id: str) -> int:
        """Count chunks for a specific document."""
        try:
            response = self._request_json(
                "POST",
                f"/collections/{self.collection}/points/count",
                {
                    "filter": {
                        "must": [
                            {"key": "doc_id", "match": {"value": doc_id}},
                        ],
                    },
                },
            )
            result = response.get("result", {})
            if isinstance(result, dict):
                return int(result.get("count", 0))
            return 0
        except RuntimeError:
            return 0

    # ── Internal HTTP helpers ────────────────────────────────────────
    # Follow the same pattern as QdrantSkillVectorIndex in router.py

    def _upsert_sync(self, points: list[dict]) -> dict:
        return self._request_json(
            "PUT",
            f"/collections/{self.collection}/points?wait=true",
            {"points": points},
        )

    def _search_sync(self, body: dict) -> dict:
        return self._request_json(
            "POST",
            f"/collections/{self.collection}/points/search",
            body,
        )

    def _scroll_sync(self) -> dict:
        return self._request_json(
            "POST",
            f"/collections/{self.collection}/points/scroll",
            {
                "limit": 1000,
                "with_payload": True,
                "with_vector": False,
            },
        )

    def _request_json(
        self, method: str, path: str, payload: dict | None = None,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = ""
            if exc.fp:
                body_text = exc.fp.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Qdrant request failed: "
                f"collection={self.collection} endpoint={method} {path} "
                f"url={self.url}{path} HTTP {exc.code} {exc.reason}; body={body_text}"
            ) from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Qdrant request failed: "
                f"collection={self.collection} endpoint={method} {path} "
                f"url={self.url}{path}; error={exc}"
            ) from exc
