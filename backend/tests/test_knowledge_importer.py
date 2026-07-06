"""Tests for KnowledgeImporter."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personal_assistant.knowledge.chunker import ChunkConfig, MarkdownChunker
from personal_assistant.knowledge.importer import (
    KnowledgeImporter,
    _compute_content_hash,
    _extract_title,
    doc_id_from_path,
)
from personal_assistant.knowledge.models import Chunk, DocMeta
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore


class FakeEmbeddingProvider:
    async def embed(self, text: str) -> list[float]:
        return [0.1] * 1024


@pytest.fixture
def store():
    s = MagicMock(spec=QdrantKnowledgeStore)
    s.list_docs.return_value = []
    s.upsert_chunks.return_value = 3
    s.get_chunk_count.return_value = 0
    s.ensure_collection.return_value = None
    return s


@pytest.fixture
def chunker():
    return MarkdownChunker(ChunkConfig(context_buffer_tokens=0))


@pytest.fixture
def emb():
    return FakeEmbeddingProvider()


@pytest.fixture
def tmp_knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    (d / "01-test.md").write_text(
        "# 测试文档\n\n## 第一节\n\n这是测试内容。\n\n## 第二节\n\n更多内容。",
        encoding="utf-8",
    )
    return d


class TestDocIdFromPath:
    def test_deterministic(self) -> None:
        id1 = doc_id_from_path("knowledge/01.md")
        id2 = doc_id_from_path("knowledge/01.md")
        assert id1 == id2

    def test_different_paths(self) -> None:
        id1 = doc_id_from_path("knowledge/01.md")
        id2 = doc_id_from_path("knowledge/02.md")
        assert id1 != id2


class TestExtractTitle:
    def test_extracts_h1(self) -> None:
        assert _extract_title("# 告警分级体系\n\n内容") == "告警分级体系"

    def test_returns_empty_if_no_h1(self) -> None:
        assert _extract_title("## 二级标题\n\n内容") == ""

    def test_skips_h2(self) -> None:
        assert _extract_title("## 简介\n\n正文") == ""


class TestContentHash:
    def test_deterministic(self) -> None:
        assert _compute_content_hash("hello") == _compute_content_hash("hello")

    def test_different(self) -> None:
        assert _compute_content_hash("hello") != _compute_content_hash("world")


class TestKnowledgeImporter:
    @pytest.mark.asyncio
    async def test_import_file(self, store, chunker, emb, tmp_knowledge_dir):
        importer = KnowledgeImporter(store, chunker, tmp_knowledge_dir, emb)
        count = await importer.import_file(tmp_knowledge_dir / "01-test.md")
        assert count > 0
        store.upsert_chunks.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_skips_readme(self, store, chunker, emb, tmp_knowledge_dir):
        (tmp_knowledge_dir / "README.md").write_text("# README", encoding="utf-8")
        importer = KnowledgeImporter(store, chunker, tmp_knowledge_dir, emb)
        results = await importer.import_all()
        # README should be skipped
        assert "README.md" not in str(results)

    @pytest.mark.asyncio
    async def test_import_skips_unchanged(self, store, chunker, emb, tmp_knowledge_dir):
        path = tmp_knowledge_dir / "01-test.md"
        doc_id = doc_id_from_path(path)
        content_hash = _compute_content_hash(path.read_text(encoding="utf-8"))

        # Simulate existing doc with same hash
        store.list_docs.return_value = [
            DocMeta(
                doc_id=doc_id, title="T", source_file=str(path),
                content_hash=content_hash, total_chunks=3,
            )
        ]
        importer = KnowledgeImporter(store, chunker, tmp_knowledge_dir, emb)
        count = await importer.import_file(path)
        # Should return existing chunk count without re-importing
        assert count == 3
        store.upsert_chunks.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_replaces_changed(self, store, chunker, emb, tmp_knowledge_dir):
        path = tmp_knowledge_dir / "01-test.md"
        doc_id = doc_id_from_path(path)

        # Simulate existing doc with DIFFERENT hash
        store.list_docs.return_value = [
            DocMeta(
                doc_id=doc_id, title="T", source_file=str(path),
                content_hash="old_hash", total_chunks=2,
            )
        ]
        store.upsert_chunks.return_value = 5
        importer = KnowledgeImporter(store, chunker, tmp_knowledge_dir, emb)
        count = await importer.import_file(path)
        # Should re-import with new chunks
        assert count == 5
        store.upsert_chunks.assert_called_once()

    def test_list_documents(self, store, chunker, emb, tmp_knowledge_dir):
        store.list_docs.return_value = [
            DocMeta(doc_id="d1", title="T1", source_file="a.md"),
            DocMeta(doc_id="d2", title="T2", source_file="b.md"),
        ]
        importer = KnowledgeImporter(store, chunker, tmp_knowledge_dir, emb)
        docs = importer.list_documents()
        assert len(docs) == 2

    def test_detect_changes(self, store, chunker, emb, tmp_knowledge_dir):
        # No existing docs → all files are new
        store.list_docs.return_value = []
        importer = KnowledgeImporter(store, chunker, tmp_knowledge_dir, emb)
        changed = importer.detect_changes()
        assert len(changed) >= 1  # At least the test file
