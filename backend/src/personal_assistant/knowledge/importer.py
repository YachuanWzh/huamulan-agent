"""Import markdown knowledge documents into Qdrant."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

from personal_assistant.agent.router import OllamaBgeM3EmbeddingProvider
from personal_assistant.knowledge.chunker import ChunkConfig, MarkdownChunker
from personal_assistant.knowledge.models import Chunk, DocMeta
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore

logger = logging.getLogger(__name__)

DOC_ID_NAMESPACE = "apm-knowledge"


def doc_id_from_path(source_file: str | Path) -> str:
    """Generate a deterministic doc_id from file path using UUID5."""
    import uuid as _uuid
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, str(Path(source_file).resolve())))


def _extract_title(markdown_text: str) -> str:
    """Extract the first H1 heading."""
    for line in markdown_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


def _compute_content_hash(content: str) -> str:
    """Compute MD5 hash of file content for change detection."""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


class KnowledgeImporter:
    """Reads markdown files, chunks them, embeds, and stores in Qdrant."""

    def __init__(
        self,
        store: QdrantKnowledgeStore,
        chunker: MarkdownChunker,
        knowledge_dir: str | Path,
        embedding_provider,
        version: str = "v1.0",
    ) -> None:
        self.store = store
        self.chunker = chunker
        self.knowledge_dir = Path(knowledge_dir)
        self.embedding_provider = embedding_provider
        self.version = version

    async def import_all(self) -> dict[str, int]:
        """Import all .md files in knowledge_dir. Returns {doc_id: chunk_count}."""
        self.store.ensure_collection()
        results: dict[str, int] = {}
        for md_file in sorted(self.knowledge_dir.glob("*.md")):
            if md_file.name == "README.md":
                continue
            try:
                count = await self.import_file(md_file)
                if count > 0:
                    results[str(md_file)] = count
            except Exception:
                logger.exception("Failed to import %s", md_file)
        return results

    async def import_file(self, file_path: str | Path) -> int:
        """Import a single file. Returns chunk count."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        markdown_text = path.read_text(encoding="utf-8")
        if not markdown_text.strip():
            logger.warning("Skipping empty file: %s", path)
            return 0

        doc_id = doc_id_from_path(path)
        title = _extract_title(markdown_text)
        content_hash = _compute_content_hash(markdown_text)

        # Check if already up-to-date
        existing = self.store.list_docs()
        for doc in existing:
            if doc.doc_id == doc_id and doc.content_hash == content_hash:
                logger.info("Skipping unchanged: %s (hash=%s)", path.name, content_hash[:8])
                return doc.total_chunks

        # Chunk and embed
        chunks = self.chunker.chunk_document(
            source_file=path,
            doc_id=doc_id,
            markdown_text=markdown_text,
        )
        if not chunks:
            logger.warning("No chunks generated for: %s", path)
            return 0

        vectors = await self._embed_chunks(chunks)
        doc = DocMeta(
            doc_id=doc_id,
            title=title or path.stem,
            source_file=str(path),
            version=self.version,
            category="apm_knowledge",
            total_chunks=len(chunks),
            content_hash=content_hash,
        )
        count = self.store.upsert_chunks(doc, chunks, vectors)
        logger.info(
            "Imported: %s → %d chunks (doc_id=%s, hash=%s)",
            path.name, count, doc_id[:12], content_hash[:8],
        )
        return count

    async def _embed_chunks(self, chunks: list[Chunk]) -> list[list[float]]:
        """Embed all chunks. Each chunk is embedded with its heading as context."""
        vectors: list[list[float]] = []

        async def embed_one(chunk: Chunk) -> None:
            # Include heading in embedding text for better semantic matching
            text = chunk.content
            if chunk.title and chunk.title != "正文":
                text = f"{chunk.title}\n\n{chunk.content}"
            vec = await self.embedding_provider.embed(text)
            vectors.append(vec)

        # Embed sequentially to avoid overwhelming Ollama
        for chunk in chunks:
            await embed_one(chunk)

        return vectors

    async def delete_document(self, doc_id_or_path: str) -> int:
        """Delete a document by doc_id or file path. Returns number of deleted chunks."""
        # Try resolving as path first, then as raw doc_id
        try:
            path = Path(doc_id_or_path)
            if path.exists():
                doc_id = doc_id_from_path(path)
            else:
                doc_id = doc_id_or_path
        except Exception:
            doc_id = doc_id_or_path

        before = self.store.get_chunk_count(doc_id)
        self.store.delete_by_doc_id(doc_id)
        after = self.store.get_chunk_count(doc_id)
        deleted = before - after
        logger.info("Deleted %d chunks for doc_id=%s", deleted, doc_id[:12])
        return deleted

    def list_documents(self) -> list[DocMeta]:
        """List all imported documents."""
        return self.store.list_docs()

    def detect_changes(self) -> list[str]:
        """Detect files that are new or changed compared to Qdrant.

        Returns list of file paths that need re-importing.
        """
        existing = {d.source_file: d.content_hash for d in self.store.list_docs()}
        changed: list[str] = []
        for md_file in sorted(self.knowledge_dir.glob("*.md")):
            if md_file.name == "README.md":
                continue
            local_hash = _compute_content_hash(md_file.read_text(encoding="utf-8"))
            old_hash = existing.get(str(md_file), "")
            if local_hash != old_hash:
                changed.append(str(md_file))
        return changed

    async def sync(self) -> dict[str, int]:
        """Incremental sync: only import changed files."""
        changed = self.detect_changes()
        if not changed:
            logger.info("No changes detected — all documents up to date")
            return {}
        results: dict[str, int] = {}
        for file_path in changed:
            try:
                count = await self.import_file(file_path)
                results[file_path] = count
            except Exception:
                logger.exception("Sync failed for %s", file_path)
        return results
