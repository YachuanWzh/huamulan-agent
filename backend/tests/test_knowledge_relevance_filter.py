"""Tests for RelevanceFilter."""
from unittest.mock import MagicMock, AsyncMock
import pytest
from personal_assistant.knowledge.models import (
    SearchResult,
)
from personal_assistant.knowledge.relevance_filter import RelevanceFilter


def make_doc(idx: int, title: str, source_file: str, category: str = "apm_knowledge") -> SearchResult:
    return SearchResult(
        chunk_id=f"d#c-{idx:03d}",
        doc_id=f"doc-{idx}",
        score=0.9 - idx * 0.1,
        content=f"Content of {title}",
        title=title,
        source_attribution=f"来源：文档《{title}》，版本 v1.0，更新日期 2026-07-06，片段 1/1",
        metadata={
            "source_file": source_file,
            "category": category,
            "version": "v1.0",
            "updated_at": "2026-07-06",
        },
    )


class TestRelevanceFilter:
    @pytest.mark.asyncio
    async def test_filter_all_relevant(self):
        """LLM returns all relevant → all docs pass through."""
        llm = MagicMock()
        response = MagicMock()
        response.content = (
            '{"verdicts": ['
            '  {"document_index": 0, "relevant": true, "reason": "查询关于告警分级，文档标题匹配"},'
            '  {"document_index": 1, "relevant": true, "reason": "查询关于告警路由，文档内容匹配"}'
            ']}'
        )
        llm.ainvoke = AsyncMock(return_value=response)

        docs = [
            make_doc(0, "告警分级体系", "knowledge/01-alert.md"),
            make_doc(1, "告警路由策略", "knowledge/02-route.md"),
        ]

        f = RelevanceFilter(llm=llm)
        result = await f.filter("P0告警如何分级处理？", docs)

        assert result.all_relevant is True
        assert len(result.filtered_documents) == 2
        assert result.no_knowledge_signal == ""

    @pytest.mark.asyncio
    async def test_filter_all_irrelevant(self):
        """LLM returns all irrelevant → empty docs + signal."""
        llm = MagicMock()
        response = MagicMock()
        response.content = (
            '{"verdicts": ['
            '  {"document_index": 0, "relevant": false, "reason": "查询关于Trace，文档是关于告警的"},'
            '  {"document_index": 1, "relevant": false, "reason": "查询关于Trace，文档是关于指标的"}'
            ']}'
        )
        llm.ainvoke = AsyncMock(return_value=response)

        docs = [
            make_doc(0, "告警分级体系", "knowledge/01-alert.md"),
            make_doc(1, "指标监控", "knowledge/03-metrics.md"),
        ]

        f = RelevanceFilter(llm=llm)
        result = await f.filter("Jaeger中如何查询Trace？", docs)

        assert result.all_relevant is False
        assert len(result.filtered_documents) == 0
        assert "知识库中无相关知识" in result.no_knowledge_signal

    @pytest.mark.asyncio
    async def test_filter_partial_relevant(self):
        """LLM returns some relevant, some irrelevant → only relevant pass."""
        llm = MagicMock()
        response = MagicMock()
        response.content = (
            '{"verdicts": ['
            '  {"document_index": 0, "relevant": false, "reason": "不匹配"},'
            '  {"document_index": 1, "relevant": true, "reason": "匹配"},'
            '  {"document_index": 2, "relevant": true, "reason": "匹配"}'
            ']}'
        )
        llm.ainvoke = AsyncMock(return_value=response)

        docs = [
            make_doc(0, "指标监控", "knowledge/03-metrics.md"),
            make_doc(1, "Trace查询指南", "knowledge/04-trace.md"),
            make_doc(2, "分布式追踪原理", "knowledge/05-distributed.md"),
        ]

        f = RelevanceFilter(llm=llm)
        result = await f.filter("Jaeger中如何查询Trace？", docs)

        assert result.all_relevant is True
        assert len(result.filtered_documents) == 2
        assert result.filtered_documents[0].title == "Trace查询指南"
        assert result.filtered_documents[1].title == "分布式追踪原理"

    @pytest.mark.asyncio
    async def test_filter_empty_docs(self):
        """No documents → not all_relevant + signal."""
        llm = MagicMock()
        f = RelevanceFilter(llm=llm)
        result = await f.filter("query", [])
        assert result.all_relevant is False
        assert len(result.filtered_documents) == 0
        assert "知识库中无相关知识" in result.no_knowledge_signal
        # Should not call LLM when no docs
        llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_filter_single_doc_skip_llm(self):
        """Single doc → fast path, skips LLM."""
        llm = MagicMock()
        docs = [make_doc(0, "告警分级", "knowledge/01-alert.md")]
        f = RelevanceFilter(llm=llm)
        result = await f.filter("P0告警处理", docs)
        assert result.all_relevant is True
        assert len(result.filtered_documents) == 1
        # Single doc should skip LLM call
        llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_filter_handles_llm_error(self):
        """LLM call fails with 2+ docs → pass all docs through (fail-open for safety)."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("API timeout"))

        docs = [
            make_doc(0, "告警分级", "knowledge/01-alert.md"),
            make_doc(1, "Trace查询", "knowledge/04-trace.md"),
        ]
        f = RelevanceFilter(llm=llm)
        result = await f.filter("P0告警处理", docs)

        # Fail-open: pass all docs through (both documents)
        assert result.all_relevant is True
        assert len(result.filtered_documents) == 2

    def test_build_prompt_includes_metadata(self):
        """Prompt should contain document titles and source files."""
        docs = [
            make_doc(0, "告警分级体系", "knowledge/01-alert.md"),
            make_doc(1, "Trace查询指南", "knowledge/04-trace.md"),
        ]
        prompt = RelevanceFilter._build_prompt("P0告警如何处理", docs)

        assert "P0告警如何处理" in prompt
        assert "告警分级体系" in prompt
        assert "knowledge/01-alert.md" in prompt
        assert "Trace查询指南" in prompt
        assert "knowledge/04-trace.md" in prompt
        assert "document_index" in prompt
        assert "relevant" in prompt
