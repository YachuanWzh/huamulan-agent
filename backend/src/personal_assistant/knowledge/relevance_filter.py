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

        # Single doc: fast path — skip LLM call
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
        # Extract JSON object — handle code fences and json prefix
        raw = raw.strip()
        if raw.startswith("```"):
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
                try:
                    data = json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    logger.warning("Could not parse relevance filter response: %s", raw)
                    # Fail-open: all relevant
                    return _fail_open_verdicts(doc_count)
            else:
                logger.warning("Could not parse relevance filter response: %s", raw)
                return _fail_open_verdicts(doc_count)

        items = data.get("verdicts", [])
        if not isinstance(items, list):
            return _fail_open_verdicts(doc_count)

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


def _fail_open_verdicts(doc_count: int) -> list[RelevanceVerdict]:
    """Return all-relevant verdicts when parsing fails."""
    return [
        RelevanceVerdict(
            document_index=i,
            relevant=True,
            reason="parse error — fail open",
        )
        for i in range(doc_count)
    ]
