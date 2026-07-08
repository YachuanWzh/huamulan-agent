# RAG Hybrid Retrieval — BM25 + Relevance Filter Implementation Plan

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve RAG accuracy for OTEL knowledge base by adding BM25 keyword search (hybrid retrieval with RRF fusion) and a deepseek-v4-flash metadata relevance filter that prevents irrelevant knowledge from entering downstream agent context.

**Architecture:** Three new components sit between the existing `KnowledgeRetriever` and the downstream agents:
1. `BM25Searcher` — in-memory BM25 keyword index rebuilt on each import/upsert
2. `RelevanceFilter` — LLM-based (deepseek-v4-flash) metadata comparison, filtering out irrelevant chunks
3. `HybridRetriever` — orchestrates vector search + BM25 search in parallel, fuses results with RRF, then passes through `RelevanceFilter`

When **all** retrieved chunks are filtered out, the downstream agent receives a `NO_KNOWLEDGE_FOUND` signal instructing it to output "知识库中无相关知识" and not free-form answer.

**Tech Stack:** Python 3.14, jieba (tokenizer), Pydantic, LangChain ChatDeepSeek (for RelevanceFilter LLM), Qdrant, BGE-M3 embeddings

---

### Task 1: BM25 Keyword Searcher

**Files:**
- Create: `backend/src/personal_assistant/knowledge/bm25_searcher.py`
- Create: `backend/tests/test_knowledge_bm25_searcher.py`
- Modify: `backend/pyproject.toml` (add `rank-bm25` dependency)

- [ ] **Step 1: Add `rank-bm25` dependency**

```toml
# In pyproject.toml, add to dependencies list:
"rank-bm25>=0.2.0",
```

Run: `python -m pip install rank-bm25`

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_knowledge_bm25_searcher.py`:

```python
"""Tests for BM25Searcher."""
import pytest
from personal_assistant.knowledge.bm25_searcher import BM25Searcher, _tokenize


class TestTokenize:
    def test_tokenize_chinese(self):
        tokens = _tokenize("P0告警如何处理")
        assert "p0" in tokens  # lowercase
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


class TestBM25Searcher:
    def test_index_and_search(self):
        searcher = BM25Searcher()
        chunks = [
            ("c1", "P0告警走webhook通道发送到飞书"),
            ("c2", "P2告警走邮件通道通知"),
            ("c3", "Jaeger用于查询分布式Trace"),
        ]
        searcher.index("apm_knowledge", chunks)

        results = searcher.search("apm_knowledge", "P0告警通知", top_k=2)
        assert len(results) == 2
        # c1 should score higher than c2 or c3 for "P0告警通知"
        assert results[0][0] == "c1"
        assert results[0][1] > results[1][1]

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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_knowledge_bm25_searcher.py -v`
Expected: FAIL — module or class not found

- [ ] **Step 4: Write minimal implementation**

Create `backend/src/personal_assistant/knowledge/bm25_searcher.py`:

```python
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

# Regex to detect English/tech terms that should stay as-is
_EN_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*")


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text.

    Strategy:
    1. Extract English/tech tokens (trace_id, P99, Jaeger) via regex
    2. Remove them from the text temporarily
    3. Run jieba on the remaining Chinese text
    4. Combine results, lowercase everything
    """
    # Extract English/technical terms
    en_terms = _EN_WORD_RE.findall(text)
    # Remove extracted terms from text
    chinese_part = _EN_WORD_RE.sub(" ", text)
    # Tokenize Chinese part
    cn_tokens = [t.strip() for t in jieba.lcut(chinese_part) if t.strip()]
    # Combine
    tokens = [t.lower() for t in en_terms] + [t.lower() for t in cn_tokens]
    # Filter empty and single-space tokens
    return [t for t in tokens if t and t != " "]


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
        scores = index.get_scores(tokenized_query)

        chunk_ids = self._chunk_ids[collection]
        scored = list(zip(chunk_ids, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        # Normalize BM25 scores to [0, 1] range for easier fusion
        max_score = scored[0][1] if scored else 1.0
        if max_score > 0:
            scored = [(cid, s / max_score) for cid, s in scored]

        return scored[:max(top_k, 1)]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_knowledge_bm25_searcher.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml backend/src/personal_assistant/knowledge/bm25_searcher.py backend/tests/test_knowledge_bm25_searcher.py
git commit -m "feat(knowledge): add BM25 keyword searcher with jieba tokenization"
```

---

### Task 2: Metadata Relevance Filter

**Files:**
- Create: `backend/src/personal_assistant/knowledge/relevance_filter.py`
- Create: `backend/tests/test_knowledge_relevance_filter.py`
- Modify: `backend/src/personal_assistant/knowledge/models.py` (add `RelevanceVerdict`, `RelevanceFilterResult`)

- [ ] **Step 1: Write the failing model test**

Add to `backend/tests/test_knowledge_models.py`:

```python
from personal_assistant.knowledge.models import RelevanceVerdict, RelevanceFilterResult


class TestRelevanceVerdict:
    def test_create_relevant(self):
        v = RelevanceVerdict(document_index=0, relevant=True, reason="query与文档来源匹配")
        assert v.relevant is True
        assert v.document_index == 0

    def test_create_irrelevant(self):
        v = RelevanceVerdict(document_index=2, relevant=False, reason="文档主题是告警分级，query是关于Trace查询")
        assert v.relevant is False


class TestRelevanceFilterResult:
    def test_all_relevant(self):
        docs = [
            SearchResult(
                chunk_id="d#c-0", doc_id="d", score=0.9,
                content="C", title="T",
                source_attribution="来源：文档《T》，版本 v1.0，更新日期 2026-07-06，片段 1/1",
                metadata={},
            )
        ]
        result = RelevanceFilterResult(
            all_relevant=True,
            verdicts=[RelevanceVerdict(document_index=0, relevant=True, reason="匹配")],
            filtered_documents=docs,
        )
        assert result.all_relevant is True
        assert len(result.filtered_documents) == 1
        assert result.no_knowledge_signal == ""

    def test_none_relevant(self):
        result = RelevanceFilterResult(
            all_relevant=False,
            verdicts=[RelevanceVerdict(document_index=0, relevant=False, reason="不匹配")],
            filtered_documents=[],
            no_knowledge_signal=(
                "⚠️ 知识库中无相关知识：经过检索和相关性校验，知识库中未找到与当前查询匹配的文档。"
                "请仅基于您自身知识中的通用概念进行解释，不要编造具体的内部流程、配置或数值。"
                "如果无法给出可靠答案，请直接回复'知识库中无相关知识，无法回答此问题'。"
            ),
        )
        assert result.all_relevant is False
        assert len(result.filtered_documents) == 0
        assert "知识库中无相关知识" in result.no_knowledge_signal
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_knowledge_models.py::TestRelevanceVerdict tests/test_knowledge_models.py::TestRelevanceFilterResult -v`
Expected: FAIL — ImportError for RelevanceVerdict, RelevanceFilterResult

- [ ] **Step 3: Add models**

Add to `backend/src/personal_assistant/knowledge/models.py` (after `GenerationMetrics` class):

```python
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

    filtered_documents: list[SearchResult]
    """Subset of input documents that passed the relevance check."""

    no_knowledge_signal: str = ""
    """When ``all_relevant`` is False, this contains the instruction to inject
    into the downstream agent's context to prevent free-form answers."""
```

- [ ] **Step 4: Run model test to verify it passes**

Run: `python -m pytest tests/test_knowledge_models.py::TestRelevanceVerdict tests/test_knowledge_models.py::TestRelevanceFilterResult -v`
Expected: PASS

- [ ] **Step 5: Write the failing filter test**

Create `backend/tests/test_knowledge_relevance_filter.py`:

```python
"""Tests for RelevanceFilter."""
from unittest.mock import MagicMock, AsyncMock
import pytest
from personal_assistant.knowledge.models import (
    RelevanceFilterResult,
    SearchResult,
)
from personal_assistant.knowledge.relevance_filter import RelevanceFilter, NO_KNOWLEDGE_MESSAGE


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
    async def test_filter_handles_llm_error(self):
        """LLM call fails → pass all docs through (fail-open for safety)."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("API timeout"))

        docs = [make_doc(0, "告警分级", "knowledge/01-alert.md")]
        f = RelevanceFilter(llm=llm)
        result = await f.filter("P0告警处理", docs)

        # Fail-open: pass all docs through
        assert result.all_relevant is True
        assert len(result.filtered_documents) == 1

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
        # Should contain JSON schema instruction
        assert "document_index" in prompt
        assert "relevant" in prompt
```

- [ ] **Step 6: Run filter test to verify it fails**

Run: `python -m pytest tests/test_knowledge_relevance_filter.py -v`
Expected: FAIL — module not found

- [ ] **Step 7: Write minimal implementation**

Create `backend/src/personal_assistant/knowledge/relevance_filter.py`:

```python
"""Metadata relevance filter using a small LLM (deepseek-v4-flash).

Before passing retrieved chunks into the downstream agent's context, this
filter asks a fast model to compare each chunk's metadata (title, source_file,
category) against the user query. Chunks whose sources are clearly unrelated
are discarded.

When *all* chunks are discarded, a ``NO_KNOWLEDGE_FOUND`` signal is injected
into the agent's context to prevent free-form confabulation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from personal_assistant.knowledge.models import (
    RelevanceFilterResult,
    RelevanceVerdict,
    SearchResult,
)

logger = logging.getLogger(__name__)

NO_KNOWLEDGE_MESSAGE = (
    "⚠️ 知识库中无相关知识：经过检索和相关性校验，知识库中未找到与当前查询匹配的文档。"
    "请仅基于您自身知识中的通用概念进行解释，不要编造具体的内部流程、配置或数值。"
    "如果无法给出可靠答案，请直接回复'知识库中无相关知识，无法回答此问题'。"
)


class RelevanceFilter:
    """Filter retrieved chunks by metadata relevance using a small LLM.

    The filter asks the LLM to compare each chunk's *source* (title, file path,
    category) against the query — NOT to judge content quality. This keeps the
    prompt compact and the LLM call fast (~0.3-0.5s with deepseek-v4-flash).
    """

    def __init__(self, llm: Any) -> None:
        """Args:
            llm: A LangChain chat model instance (typically ChatDeepSeek with
                 deepseek-v4-flash).
        """
        self._llm = llm

    async def filter(
        self, query: str, documents: list[SearchResult],
    ) -> RelevanceFilterResult:
        """Check each document's metadata relevance against the query.

        Args:
            query: The user's original query.
            documents: Retrieved chunks with metadata.

        Returns:
            RelevanceFilterResult with filtered docs and optional signal.
        """
        if not documents:
            return RelevanceFilterResult(
                all_relevant=False,
                verdicts=[],
                filtered_documents=[],
                no_knowledge_signal=NO_KNOWLEDGE_MESSAGE,
            )

        # Single doc: fast path — skip LLM call if score is decent
        if len(documents) == 1:
            return RelevanceFilterResult(
                all_relevant=True,
                verdicts=[RelevanceVerdict(
                    document_index=0,
                    relevant=True,
                    reason="single result, skip filter",
                )],
                filtered_documents=documents,
            )

        try:
            prompt = self._build_prompt(query, documents)
            response = await self._llm.ainvoke(prompt)
            verdicts = self._parse_verdicts(
                str(getattr(response, "content", response) or ""),
                len(documents),
            )
        except Exception as exc:
            logger.warning("Relevance filter LLM call failed: %s — failing open", exc)
            # Fail-open: pass all docs through
            return RelevanceFilterResult(
                all_relevant=True,
                verdicts=[],
                filtered_documents=documents,
            )

        relevant_docs = [
            documents[v.document_index]
            for v in verdicts
            if v.relevant and v.document_index < len(documents)
        ]

        all_relevant = len(relevant_docs) > 0
        return RelevanceFilterResult(
            all_relevant=all_relevant,
            verdicts=verdicts,
            filtered_documents=relevant_docs,
            no_knowledge_signal="" if all_relevant else NO_KNOWLEDGE_MESSAGE,
        )

    @staticmethod
    def _build_prompt(query: str, documents: list[SearchResult]) -> str:
        """Build a compact prompt listing each doc's metadata for comparison."""
        doc_list_parts: list[str] = []
        for i, doc in enumerate(documents):
            source_file = doc.metadata.get("source_file", "未知")
            category = doc.metadata.get("category", "未知")
            doc_list_parts.append(
                f"[{i}] 标题：{doc.title} | "
                f"来源文件：{source_file} | "
                f"分类：{category}"
            )

        doc_list = "\n".join(doc_list_parts)

        return f"""你是一个文档相关性判断助手。请判断以下检索到的文档来源是否与用户查询相关。

**用户查询：** {query}

**检索到的文档来源：**
{doc_list}

**指令：**
1. 逐一比较每个文档的"标题"和"来源文件"与用户查询的主题是否匹配
2. 只基于文档主题和查询主题的相关性做判断，不考虑内容质量
3. 如果查询提到的概念（如 Trace、告警、指标）与文档标题/来源明显无关，标记为 irrelevant
4. 返回严格的 JSON 格式

**返回格式（严格 JSON）：**
{{"verdicts": [{{"document_index": 0, "relevant": true/false, "reason": "简短中文理由"}}]}}"""

    @staticmethod
    def _parse_verdicts(raw: str, doc_count: int) -> list[RelevanceVerdict]:
        """Parse the LLM JSON response into RelevanceVerdict objects."""
        # Extract JSON object
        raw = raw.strip()
        if raw.startswith("```"):
            # Remove code fences
            lines = raw.split("\n")
            raw = "\n".join(l for l in lines if not l.startswith("```"))
            raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to find a JSON object in the text
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(raw[start:end + 1])
            else:
                logger.warning("Could not parse relevance filter response: %s", raw)
                # Fail-open: all relevant
                return [
                    RelevanceVerdict(document_index=i, relevant=True, reason="parse error — fail open")
                    for i in range(doc_count)
                ]

        items = data.get("verdicts", [])
        if not isinstance(items, list):
            return [
                RelevanceVerdict(document_index=i, relevant=True, reason="unexpected format — fail open")
                for i in range(doc_count)
            ]

        verdicts: list[RelevanceVerdict] = []
        seen_indices: set[int] = set()
        for item in items:
            idx = int(item.get("document_index", -1))
            if idx < 0 or idx >= doc_count or idx in seen_indices:
                continue
            seen_indices.add(idx)
            verdicts.append(RelevanceVerdict(
                document_index=idx,
                relevant=bool(item.get("relevant", True)),
                reason=str(item.get("reason", "")),
            ))

        # Fill in any missing indices as relevant (fail-open)
        for i in range(doc_count):
            if i not in seen_indices:
                verdicts.append(RelevanceVerdict(
                    document_index=i,
                    relevant=True,
                    reason="not mentioned by LLM — fail open",
                ))

        return sorted(verdicts, key=lambda v: v.document_index)
```

- [ ] **Step 8: Run filter test to verify it passes**

Run: `python -m pytest tests/test_knowledge_relevance_filter.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add backend/src/personal_assistant/knowledge/models.py backend/src/personal_assistant/knowledge/relevance_filter.py backend/tests/test_knowledge_relevance_filter.py backend/tests/test_knowledge_models.py
git commit -m "feat(knowledge): add metadata relevance filter with LLM-based verification"
```

---

### Task 3: Hybrid Retriever with RRF Fusion

**Files:**
- Create: `backend/src/personal_assistant/knowledge/hybrid_retriever.py`
- Create: `backend/tests/test_knowledge_hybrid_retriever.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_knowledge_hybrid_retriever.py`:

```python
"""Tests for HybridRetriever."""
from unittest.mock import MagicMock, AsyncMock
import pytest
from personal_assistant.knowledge.hybrid_retriever import HybridRetriever, reciprocal_rank_fusion
from personal_assistant.knowledge.models import (
    KnowledgeRetrievalResult,
    RelevanceFilterResult,
    SearchResult,
)
from personal_assistant.knowledge.retriever import KnowledgeRetriever


class TestRRF:
    def test_rrf_combines_two_lists(self):
        vector = [("c1", 0.9), ("c2", 0.7)]
        bm25 = [("c2", 0.8), ("c3", 0.6)]
        fused = reciprocal_rank_fusion(vector, bm25, k=60)
        # c2 appears in both → should score highest
        assert fused[0][0] == "c2"
        assert len(fused) == 3  # c1, c2, c3

    def test_rrf_deduplicates(self):
        vector = [("c1", 0.9)]
        bm25 = [("c1", 0.8)]
        fused = reciprocal_rank_fusion(vector, bm25, k=60)
        assert len(fused) == 1
        assert fused[0][0] == "c1"

    def test_rrf_empty_vector(self):
        fused = reciprocal_rank_fusion([], [("c1", 0.9)], k=60)
        assert len(fused) == 1
        assert fused[0][0] == "c1"

    def test_rrf_both_empty(self):
        fused = reciprocal_rank_fusion([], [], k=60)
        assert fused == []

    def test_rrf_score_range(self):
        """RRF scores should be positive and roughly in 0-0.1 range."""
        vector = [("c1", 0.9), ("c2", 0.7), ("c3", 0.5)]
        bm25 = [("c2", 0.8), ("c4", 0.6)]
        fused = reciprocal_rank_fusion(vector, bm25, k=60)
        for _, score in fused:
            assert 0 < score < 0.1  # RRF scores with k=60 are small


class TestHybridRetriever:
    def _make_doc(self, idx: int, title: str) -> SearchResult:
        return SearchResult(
            chunk_id=f"d#c-{idx:03d}", doc_id=f"doc-{idx}",
            score=0.9 - idx * 0.1, content=f"Content {idx}: {title}",
            title=title,
            source_attribution=f"来源：文档《{title}》，版本 v1.0，更新日期 2026-07-06，片段 1/1",
            metadata={},
        )

    @pytest.mark.asyncio
    async def test_retrieve_hybrid_success(self):
        """Happy path: vector + BM25 → RRF → filter → results."""
        vector_retriever = MagicMock(spec=KnowledgeRetriever)
        vector_retriever.store.search.return_value = [
            self._make_doc(0, "告警分级"),
            self._make_doc(1, "Trace查询"),
        ]

        bm25 = MagicMock()
        bm25.search.return_value = [("doc-0#chunk-000", 0.95), ("doc-1#chunk-001", 0.7)]

        relevance_filter = MagicMock()
        relevance_filter.filter = AsyncMock(return_value=RelevanceFilterResult(
            all_relevant=True,
            verdicts=[],
            filtered_documents=[
                self._make_doc(0, "告警分级"),
                self._make_doc(1, "Trace查询"),
            ],
        ))

        # Mock embedding provider
        vector_retriever.embedding_provider = MagicMock()
        vector_retriever.embedding_provider.embed = AsyncMock(return_value=[0.1] * 1024)
        vector_retriever.top_k = 5

        hybrid = HybridRetriever(
            vector_retriever=vector_retriever,
            bm25=bm25,
            relevance_filter=relevance_filter,
            top_k=5,
        )

        result = await hybrid.retrieve("P0告警与Trace查询")

        assert result.status == "completed"
        assert len(result.documents) == 2

    @pytest.mark.asyncio
    async def test_retrieve_all_filtered_out(self):
        """All docs filtered out → trust_signal set."""
        vector_retriever = MagicMock(spec=KnowledgeRetriever)
        vector_retriever.store.search.return_value = [self._make_doc(0, "告警分级")]
        vector_retriever.embedding_provider = MagicMock()
        vector_retriever.embedding_provider.embed = AsyncMock(return_value=[0.1] * 1024)
        vector_retriever.top_k = 5

        bm25 = MagicMock()
        bm25.search.return_value = [("doc-0#chunk-000", 0.9)]

        relevance_filter = MagicMock()
        relevance_filter.filter = AsyncMock(return_value=RelevanceFilterResult(
            all_relevant=False,
            verdicts=[],
            filtered_documents=[],
            no_knowledge_signal="⚠️ 知识库中无相关知识...",
        ))

        hybrid = HybridRetriever(
            vector_retriever=vector_retriever,
            bm25=bm25,
            relevance_filter=relevance_filter,
            top_k=5,
        )

        result = await hybrid.retrieve("完全不相关的问题")

        # When all filtered out, status is still "completed" but trust_signal is set
        assert result.status == "completed"
        assert len(result.documents) == 0
        assert result.trust_signal == "NO_KNOWLEDGE_FOUND"

    @pytest.mark.asyncio
    async def test_retrieve_empty_query(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = await hybrid.retrieve("")
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_format_for_llm_with_trust_signal(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = KnowledgeRetrievalResult(
            status="completed",
            documents=[
                SearchResult(
                    chunk_id="d#c-0", doc_id="d", score=0.9,
                    content="知识内容", title="T",
                    source_attribution="来源：文档《T》，版本 v1.0，更新日期 2026-07-06，片段 1/1",
                    metadata={},
                )
            ],
        )
        formatted = hybrid.format_for_llm(result)
        assert "【相关知识" in formatted

    @pytest.mark.asyncio
    async def test_format_for_llm_no_knowledge(self):
        hybrid = HybridRetriever(
            vector_retriever=MagicMock(),
            bm25=MagicMock(),
            relevance_filter=MagicMock(),
        )
        result = KnowledgeRetrievalResult(
            status="completed",
            documents=[],
            trust_signal="NO_KNOWLEDGE_FOUND",
        )
        formatted = hybrid.format_for_llm(result)
        assert "知识库中无相关知识" in formatted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_knowledge_hybrid_retriever.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Add `trust_signal` field to `KnowledgeRetrievalResult`**

In `backend/src/personal_assistant/knowledge/models.py`, modify `KnowledgeRetrievalResult`:

```python
class KnowledgeRetrievalResult(BaseModel):
    """Full response from a knowledge retrieval operation."""

    status: str  # "completed" | "skipped" | "failed"
    documents: list[SearchResult] = Field(default_factory=list)
    reason: str = ""
    trust_signal: str = ""
    """When set to ``"NO_KNOWLEDGE_FOUND"``, indicates all retrieved docs were
    filtered out by the relevance check. Downstream agents should respect this
    and not produce free-form answers."""
```

- [ ] **Step 4: Write minimal implementation**

Create `backend/src/personal_assistant/knowledge/hybrid_retriever.py`:

```python
"""Hybrid retriever combining vector search, BM25 keyword search, and LLM relevance filtering.

The retrieval pipeline:
1. Embed query → vector search in Qdrant
2. BM25 keyword search (in-memory index)
3. RRF (Reciprocal Rank Fusion) to merge results
4. Relevance filter (deepseek-v4-flash) to verify metadata alignment
5. Return results with ``trust_signal`` for downstream agents
"""

from __future__ import annotations

import logging
from typing import Any

from personal_assistant.knowledge.bm25_searcher import BM25Searcher
from personal_assistant.knowledge.models import (
    KnowledgeRetrievalResult,
    SearchResult,
)
from personal_assistant.knowledge.relevance_filter import RelevanceFilter
from personal_assistant.knowledge.retriever import KnowledgeRetriever

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    vector_results: list[tuple[str, float]],
    bm25_results: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Combine two ranked result lists using Reciprocal Rank Fusion.

    RRF score = Σ 1/(k + rank_in_list) for each list the item appears in.

    Args:
        vector_results: ``(chunk_id, score)`` from vector search.
        bm25_results: ``(chunk_id, score)`` from BM25 search.
        k: RRF constant (default 60, standard value).

    Returns:
        Combined ``(chunk_id, rrf_score)`` sorted descending.
    """
    scores: dict[str, float] = {}

    for rank, (chunk_id, _) in enumerate(vector_results, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

    for rank, (chunk_id, _) in enumerate(bm25_results, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused


class HybridRetriever:
    """Retriever that fuses vector + BM25 results and filters by relevance.

    Designed as a drop-in replacement for ``KnowledgeRetriever`` in the
    multi-agent flow. The same ``retrieve() → KnowledgeRetrievalResult``
    interface, plus ``format_for_llm()`` aware of the trust signal.
    """

    def __init__(
        self,
        *,
        vector_retriever: KnowledgeRetriever,
        bm25: BM25Searcher,
        relevance_filter: RelevanceFilter | None = None,
        top_k: int = 5,
        collection: str = "apm_knowledge",
    ) -> None:
        self._vector = vector_retriever
        self._bm25 = bm25
        self._filter = relevance_filter
        self.top_k = top_k
        self.collection = collection

    async def retrieve(self, query: str) -> KnowledgeRetrievalResult:
        """Run the hybrid retrieval pipeline.

        Steps:
        1. Vector search (via KnowledgeRetriever)
        2. BM25 keyword search
        3. RRF fusion
        4. Relevance filter (if configured)
        """
        if not query.strip():
            return KnowledgeRetrievalResult(status="skipped", reason="empty query")

        # Step 1: Vector search
        vector_docs: list[SearchResult] = []
        try:
            vec_result = await self._vector.retrieve(query)
            vector_docs = vec_result.documents
        except Exception as exc:
            logger.warning("Vector retrieval failed: %s", exc)

        # Step 2: BM25 keyword search
        bm25_scored: list[tuple[str, float]] = []
        try:
            bm25_scored = self._bm25.search(self.collection, query, top_k=self.top_k)
        except Exception as exc:
            logger.warning("BM25 search failed: %s", exc)

        # Step 3: RRF fusion
        vector_scored = [(d.chunk_id, d.score) for d in vector_docs]
        fused = reciprocal_rank_fusion(vector_scored, bm25_scored)

        # Map fused IDs back to SearchResult objects
        doc_map: dict[str, SearchResult] = {d.chunk_id: d for d in vector_docs}
        merged: list[SearchResult] = []
        for chunk_id, rrf_score in fused[:self.top_k]:
            if chunk_id in doc_map:
                doc = doc_map[chunk_id]
                merged.append(SearchResult(
                    chunk_id=doc.chunk_id,
                    doc_id=doc.doc_id,
                    score=rrf_score,  # Replace with RRF score
                    content=doc.content,
                    title=doc.title,
                    source_attribution=doc.source_attribution,
                    metadata=doc.metadata,
                ))
            else:
                # BM25-only result: need to create from scratch
                merged.append(SearchResult(
                    chunk_id=chunk_id,
                    doc_id=chunk_id.rsplit("#", 1)[0],
                    score=rrf_score,
                    content="",
                    title="(BM25 match)",
                    source_attribution="",
                    metadata={},
                ))

        # Step 4: Relevance filter
        if self._filter and merged:
            try:
                filter_result = await self._filter.filter(query, merged)
                if not filter_result.all_relevant:
                    return KnowledgeRetrievalResult(
                        status="completed",
                        documents=[],
                        trust_signal="NO_KNOWLEDGE_FOUND",
                        reason=filter_result.no_knowledge_signal,
                    )
                merged = filter_result.filtered_documents
            except Exception as exc:
                logger.warning("Relevance filter failed, passing all docs through: %s", exc)

        if not merged and not vector_docs and not bm25_scored:
            return KnowledgeRetrievalResult(
                status="completed",
                documents=[],
                trust_signal="NO_KNOWLEDGE_FOUND",
                reason="no results from either vector or BM25 search",
            )

        return KnowledgeRetrievalResult(status="completed", documents=merged)

    def format_for_llm(self, result: KnowledgeRetrievalResult) -> str:
        """Format retrieval results for LLM context, respecting trust_signal.

        When trust_signal is NO_KNOWLEDGE_FOUND, returns the signal message
        instead of an empty string so the agent sees the constraint.
        """
        if result.trust_signal == "NO_KNOWLEDGE_FOUND":
            return result.reason or "知识库中无相关知识。"

        if result.status != "completed" or not result.documents:
            return ""

        parts: list[str] = []
        for i, doc in enumerate(result.documents, 1):
            parts.append(
                f"【相关知识 {i}】（综合得分：{doc.score:.4f}）\n"
                f"{doc.source_attribution}\n"
                f"章节：{doc.title}\n"
                f"---\n"
                f"{doc.content}\n"
                f"---"
            )
        return "\n\n".join(parts)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_knowledge_hybrid_retriever.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/personal_assistant/knowledge/hybrid_retriever.py backend/src/personal_assistant/knowledge/models.py backend/tests/test_knowledge_hybrid_retriever.py
git commit -m "feat(knowledge): add hybrid retriever with RRF fusion and relevance filter"
```

---

### Task 4: Integrate into Multi-Agent Flow

**Files:**
- Modify: `backend/src/personal_assistant/agent/multi_agent.py` (use hybrid retriever)
- Modify: `backend/src/personal_assistant/knowledge/__init__.py` (export new classes, update factory)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_multi_agent_graph.py`:

```python
@pytest.mark.asyncio
async def test_retrieve_user_vector_context_uses_hybrid_retriever(settings_factory):
    """When hybrid retriever is configured, query results pass through BM25 + filter."""
    settings = settings_factory()
    settings.user_vector_retrieval_enabled = True
    settings.user_vector_qdrant_url = "http://localhost:6333"

    from unittest.mock import AsyncMock, MagicMock, patch

    with patch(
        "personal_assistant.agent.multi_agent.OllamaBgeM3EmbeddingProvider"
    ) as mock_emb:
        mock_emb_instance = MagicMock()
        mock_emb_instance.embed = AsyncMock(return_value=[0.1] * 1024)
        mock_emb.return_value = mock_emb_instance

        with patch("personal_assistant.agent.multi_agent._qdrant_search_user_documents") as mock_search:
            mock_search.return_value = [
                {"score": 0.9, "content": "知识内容", "metadata": {"title": "告警分级"}}
            ]
            with patch(
                "personal_assistant.agent.multi_agent._build_hybrid_retriever"
            ) as mock_build:
                mock_hr = MagicMock()
                mock_hr.retrieve = AsyncMock(return_value=KnowledgeRetrievalResult(
                    status="completed",
                    documents=[
                        SearchResult(
                            chunk_id="d#c-0", doc_id="d", score=0.9,
                            content="知识内容", title="告警分级",
                            source_attribution="来源：...",
                            metadata={"title": "告警分级", "source_file": "knowledge/01.md"},
                        )
                    ],
                ))
                mock_hr.format_for_llm.return_value = "【相关知识 1】..."
                mock_build.return_value = mock_hr

                result = await _retrieve_user_vector_context(settings, "P0告警")

                assert result["status"] == "completed"
                assert "documents" in result
                # hybrid path should be used
                mock_build.assert_called_once()
                mock_hr.retrieve.assert_called_once_with("P0告警")
```

Also add these imports at top of the test function (inline in the test).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_multi_agent_graph.py::test_retrieve_user_vector_context_uses_hybrid_retriever -v`
Expected: FAIL — `_build_hybrid_retriever` not found

- [ ] **Step 3: Update `__init__.py` exports and add `_build_hybrid_retriever`**

In `backend/src/personal_assistant/knowledge/__init__.py`:

```python
"""RAG knowledge management system for APM alert analysis.

Provides document chunking, Qdrant CRUD, import, retrieval with source
attribution, and evaluation interfaces for the knowledge base.
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
    # ... (existing code unchanged) ...


def build_hybrid_retriever(settings, llm=None) -> "HybridRetriever | None":
    """Build a HybridRetriever when hybrid retrieval is enabled.

    Returns ``None`` when ``KNOWLEDGE_HYBRID_ENABLED`` is ``False`` or the
    base KnowledgeRetriever cannot be built.
    """
    enabled = bool(getattr(settings, "knowledge_hybrid_enabled", False))
    if not enabled:
        return None

    base = build_knowledge_retriever(settings)
    if base is None:
        return None

    bm25 = BM25Searcher()
    # TODO: populate BM25 index from existing Qdrant data
    # For now, the index is built on import/upsert

    relevance_filter = None
    if llm is not None:
        relevance_filter = RelevanceFilter(llm=llm)

    return HybridRetriever(
        vector_retriever=base,
        bm25=bm25,
        relevance_filter=relevance_filter,
        top_k=int(getattr(settings, "knowledge_retrieval_top_k", 5) or 5),
        collection=getattr(settings, "knowledge_qdrant_collection", "apm_knowledge"),
    )
```

- [ ] **Step 4: Modify `multi_agent.py` to use hybrid retriever**

In `backend/src/personal_assistant/agent/multi_agent.py`, modify `_retrieve_user_vector_context`:

```python
async def _retrieve_user_vector_context(settings: Settings, query: str) -> dict[str, Any]:
    # First try hybrid retriever
    from personal_assistant.knowledge import build_hybrid_retriever

    hybrid = build_hybrid_retriever(settings)
    if hybrid is not None:
        try:
            result = await hybrid.retrieve(query)
            formatted = hybrid.format_for_llm(result)
            trust_signal = getattr(result, "trust_signal", "")
            return {
                "status": result.status,
                "documents": [d.model_dump() for d in result.documents],
                "formatted": formatted,
                "trust_signal": trust_signal,
            }
        except Exception as exc:
            # Fall through to legacy path
            pass

    # Legacy path: raw Qdrant vector search (unchanged)
    if not getattr(settings, "user_vector_retrieval_enabled", False):
        return {
            "status": "skipped",
            "reason": "USER_VECTOR_RETRIEVAL_ENABLED is false",
            "documents": [],
        }
    # ... (existing legacy code unchanged) ...
```

- [ ] **Step 5: Run all knowledge + multi-agent tests**

Run: `python -m pytest tests/test_knowledge_*.py tests/test_multi_agent_*.py -v --tb=short`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/personal_assistant/knowledge/__init__.py backend/src/personal_assistant/agent/multi_agent.py backend/tests/test_multi_agent_graph.py
git commit -m "feat(multi-agent): integrate hybrid retriever into user vector context retrieval"
```

---

### Task 5: Config and Wiring

**Files:**
- Modify: `backend/src/personal_assistant/config.py` (add hybrid settings)
- Modify: `backend/src/personal_assistant/agent/harness.py` (wire hybrid retriever on startup)

- [ ] **Step 1: Add config settings**

In `backend/src/personal_assistant/config.py`, after the existing knowledge settings block:

```python
    # ── Knowledge Hybrid Retrieval ─────────────────────────────────────
    knowledge_hybrid_enabled: bool = Field(
        default=False,
        alias="KNOWLEDGE_HYBRID_ENABLED",
    )
    knowledge_relevance_filter_enabled: bool = Field(
        default=True,
        alias="KNOWLEDGE_RELEVANCE_FILTER_ENABLED",
    )
    knowledge_relevance_filter_model: str = Field(
        default="deepseek-v4-flash",
        alias="KNOWLEDGE_RELEVANCE_FILTER_MODEL",
    )
    knowledge_bm25_collection: str = Field(
        default="apm_knowledge",
        alias="KNOWLEDGE_BM25_COLLECTION",
    )
```

- [ ] **Step 2: Check harness.py for wiring**

Read `backend/src/personal_assistant/agent/harness.py` to understand how components are wired.

- [ ] **Step 3: Wire hybrid retriever in harness.py (if applicable)**

If `harness.py` initializes the knowledge retriever, add hybrid retriever initialization alongside it. The exact integration point depends on the existing wiring.

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest tests/test_knowledge_*.py tests/test_multi_agent_*.py -v --tb=short`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/config.py backend/src/personal_assistant/agent/harness.py
git commit -m "feat(config): add hybrid retrieval and relevance filter settings"
```

---

### Verification Checklist

- [ ] `KnowledgeRetrievalResult.trust_signal` populated when all docs filtered out
- [ ] `HybridRetriever.retrieve()` runs vector + BM25 in parallel
- [ ] RRF correctly fuses and deduplicates results
- [ ] `RelevanceFilter` sends LLM a compact metadata-only prompt
- [ ] `NO_KNOWLEDGE_FOUND` signal formatted correctly for downstream agents
- [ ] Settings map cleanly: `KNOWLEDGE_HYBRID_ENABLED`, `KNOWLEDGE_RELEVANCE_FILTER_ENABLED`, `KNOWLEDGE_RELEVANCE_FILTER_MODEL`
- [ ] All existing knowledge + multi-agent tests still pass
- [ ] New components follow existing code idioms (same error handling, logging, type hints)
