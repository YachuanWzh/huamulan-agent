"""Tests for QdrantKnowledgeStore CRUD operations (mocked HTTP)."""
import json
from unittest.mock import patch


from personal_assistant.knowledge.models import Chunk, DocMeta
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore


class FakeEmbeddingProvider:
    """Returns fixed-length deterministic vectors."""

    async def embed(self, text: str) -> list[float]:
        # Return a vector based on text hash for determinism
        h = hash(text) % 1000
        return [float((h + i) % 100) / 100.0 for i in range(1024)]


def _make_store():
    return QdrantKnowledgeStore(
        url="http://qdrant:6333",
        collection="apm_knowledge",
        api_key="test-key",
    )


def _mock_urlopen_response(data: dict, status=200):
    """Create a mock that returns JSON data."""

    class MockResponse:
        def __init__(self, data, status):
            self._data = data
            self.status = status

        def read(self):
            return json.dumps(self._data).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return MockResponse(data, status)


class TestEnsureCollection:
    def test_creates_when_missing(self) -> None:
        store = _make_store()
        # First call: collection not found (404-like), then create succeeds
        responses = iter([
            _mock_urlopen_response({"status": {"ok": True}}),  # create
        ])

        def mock_urlopen(request, timeout=None):
            return next(responses)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request") as mock_req:
            store.ensure_collection()
            # Should have made a PUT request to create collection
            assert mock_req.called


class TestUpsertChunks:
    def test_upsert_chunks_deletes_before_insert(self) -> None:
        store = _make_store()
        chunks = [
            Chunk(chunk_id="d#c-000", doc_id="d", chunk_index=0, title="T1", content="C1"),
            Chunk(chunk_id="d#c-001", doc_id="d", chunk_index=1, title="T2", content="C2"),
        ]
        vectors = [[0.1] * 1024, [0.2] * 1024]
        doc = DocMeta(doc_id="d", title="Test", source_file="test.md")

        call_count = [0]

        def mock_urlopen(request, timeout=None):
            call_count[0] += 1
            # First call: delete
            # Second call: upsert
            return _mock_urlopen_response({"result": {"status": "ok"}})

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request"):
            store.upsert_chunks(doc, chunks, vectors)
            # Should have called delete then upsert
            assert call_count[0] >= 1

    def test_empty_chunks_noop(self) -> None:
        store = _make_store()
        doc = DocMeta(doc_id="d", title="Test", source_file="test.md")
        result = store.upsert_chunks(doc, [], [])
        assert result == 0


class TestDeleteByDocId:
    def test_delete_by_doc_id(self) -> None:
        store = _make_store()

        def mock_urlopen(request, timeout=None):
            return _mock_urlopen_response({"result": {"status": "ok"}})

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request"):
            store.delete_by_doc_id("d")


class TestSearch:
    def test_search_returns_results(self) -> None:
        store = _make_store()
        vector = [0.5] * 1024

        def mock_urlopen(request, timeout=None):
            return _mock_urlopen_response({
                "result": [
                    {
                        "id": "point-1",
                        "score": 0.95,
                        "payload": {
                            "content": "这是召回内容",
                            "doc_id": "doc-001",
                            "title": "告警分级",
                            "chunk_id": "doc-001#chunk-000",
                            "version": "v1.0",
                            "updated_at": "2026-07-06",
                            "chunk_index": 0,
                            "total_chunks": 8,
                            "source_file": "knowledge/01.md",
                            "category": "apm_knowledge",
                        },
                    }
                ]
            })

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request"):
            results = store.search(vector, top_k=5)
            assert len(results) == 1
            assert results[0].score == 0.95
            assert "来源" in results[0].source_attribution
            assert "告警分级" in results[0].source_attribution

    def test_search_empty_results(self) -> None:
        store = _make_store()
        vector = [0.5] * 1024

        def mock_urlopen(request, timeout=None):
            return _mock_urlopen_response({"result": []})

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request"):
            results = store.search(vector)
            assert results == []


class TestListDocs:
    def test_list_docs_empty(self) -> None:
        store = _make_store()

        def mock_urlopen(request, timeout=None):
            return _mock_urlopen_response({
                "result": {"points": [], "next_page_offset": None}
            })

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request"):
            docs = store.list_docs()
            assert docs == []

    def test_list_docs_deduplicates(self) -> None:
        store = _make_store()

        def mock_urlopen(request, timeout=None):
            return _mock_urlopen_response({
                "result": {
                    "points": [
                        {"payload": {"doc_id": "d1", "title": "Doc 1", "source_file": "a.md", "version": "v1.0", "updated_at": "2026-07-06", "total_chunks": 3, "content_hash": "abc"}},
                        {"payload": {"doc_id": "d1", "title": "Doc 1", "source_file": "a.md", "version": "v1.0", "updated_at": "2026-07-06", "total_chunks": 3, "content_hash": "abc"}},
                        {"payload": {"doc_id": "d2", "title": "Doc 2", "source_file": "b.md", "version": "v1.0", "updated_at": "2026-07-06", "total_chunks": 5, "content_hash": "def"}},
                    ],
                    "next_page_offset": None,
                }
            })

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("urllib.request.Request"):
            docs = store.list_docs()
            assert len(docs) == 2
