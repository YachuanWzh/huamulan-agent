"""Tests for BM25Searcher."""
import pytest
from personal_assistant.knowledge.bm25_searcher import BM25Searcher, _tokenize


class TestTokenize:
    def test_tokenize_chinese(self):
        tokens = _tokenize("P0告警如何处理")
        assert "p0" in tokens
        assert "告警" in tokens
        assert "如何" in tokens
        assert "处理" in tokens

    def test_tokenize_english_terms(self):
        tokens = _tokenize("trace_id span_id P99 LCP")
        assert "trace_id" in tokens
        assert "span_id" in tokens
        assert "p99" in tokens
        assert "lcp" in tokens

    def test_tokenize_mixed(self):
        tokens = _tokenize("Jaeger查询 trace_id 的延迟")
        assert "jaeger" in tokens
        assert "trace_id" in tokens
        assert "延迟" in tokens

    def test_tokenize_empty(self):
        tokens = _tokenize("")
        assert tokens == []

    def test_tokenize_whitespace_only(self):
        tokens = _tokenize("   ")
        assert tokens == []


class TestBM25Searcher:
    def test_index_and_search(self):
        searcher = BM25Searcher()
        chunks = [
            ("c1", "P0最高优先级告警通过webhook即时推送到飞书群"),
            ("c2", "P2较低优先级告警汇总后走邮件通知"),
            ("c3", "Jaeger查询工具用于搜索分布式Trace数据"),
        ]
        searcher.index("apm_knowledge", chunks)

        results = searcher.search("apm_knowledge", "P0最高优先级webhook推送", top_k=2)
        assert len(results) == 2
        # c1 should score highest — unique term "P0" + "webhook" match
        assert results[0][0] == "c1", f"Expected c1 first, got {results}"
        assert results[0][1] > 0, "c1 score should be positive"

    def test_search_exclusive_term(self):
        """A query with a term unique to one doc should rank that doc first."""
        searcher = BM25Searcher()
        chunks = [
            ("c1", "Jaeger查询Trace的工具和方法"),
            ("c2", "Prometheus查询指标的方法"),
            ("c3", "告警分级和路由策略"),
        ]
        searcher.index("test", chunks)
        results = searcher.search("test", "Jaeger使用方法", top_k=3)
        assert results[0][0] == "c1", f"Expected c1 for Jaeger query, got {results}"

    def test_search_empty_collection(self):
        searcher = BM25Searcher()
        results = searcher.search("nonexistent", "query", top_k=5)
        assert results == []

    def test_search_no_match(self):
        searcher = BM25Searcher()
        searcher.index("test", [("c1", "告警处理流程")])
        results = searcher.search("test", "xyz不存在的词", top_k=5)
        # All scores are 0 or near 0
        all_zero = all(score < 0.001 for _, score in results)
        assert all_zero, f"Expected all-zero scores, got {results}"

    def test_reindex_replaces_previous(self):
        searcher = BM25Searcher()
        searcher.index("test", [("c1", "old content")])
        searcher.index("test", [("c2", "new content different")])
        results = searcher.search("test", "new", top_k=1)
        assert results[0][0] == "c2"

    def test_multiple_collections_isolated(self):
        searcher = BM25Searcher()
        searcher.index("col_a", [("a1", "alpha content")])
        searcher.index("col_b", [("b1", "beta content")])
        results_a = searcher.search("col_a", "alpha", top_k=1)
        results_b = searcher.search("col_b", "beta", top_k=1)
        assert results_a[0][0] == "a1"
        assert results_b[0][0] == "b1"

    def test_index_empty_chunks_clears(self):
        searcher = BM25Searcher()
        searcher.index("test", [("c1", "some content")])
        searcher.index("test", [])
        results = searcher.search("test", "content", top_k=5)
        assert results == []

    def test_score_normalized_range(self):
        """Scores should be normalized to [0, 1] range."""
        searcher = BM25Searcher()
        chunks = [("c1", "alpha"), ("c2", "beta gamma"), ("c3", "alpha beta")]
        searcher.index("test", chunks)
        results = searcher.search("test", "alpha", top_k=5)
        for _, score in results:
            assert 0.0 <= score <= 1.0, f"Score {score} out of [0,1] range"
