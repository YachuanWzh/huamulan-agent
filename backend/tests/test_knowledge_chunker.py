"""Tests for MarkdownChunker — smart chunking with context buffer."""
from pathlib import Path

from personal_assistant.knowledge.chunker import ChunkConfig, MarkdownChunker


SIMPLE_MD = """\
# 测试文档

## 第一章

这是第一章的内容。包含一些中文文本用来测试分块功能。

这里还有第二段，继续第一章。

## 第二章

第二章的内容开始。这一章讨论不同的主题。

第二章的第二段。

## 第三章

第三章很短。
"""

LONG_SECTION_MD = """\
# 长文档

## 长章节

""" + ("这是长章节的内容。" * 200) + """

## 短章节

短章节只有一点点内容。
"""

NO_H2_MD = """\
# 无二级标题文档

这是没有 H2 的文档内容。所有内容都在一级标题下。

这里还有更多内容。测试分块器如何处理没有子标题的文档。

第三段内容。
"""


class TestMarkdownChunker:
    def test_chunk_by_h2_sections(self) -> None:
        chunker = MarkdownChunker(ChunkConfig(context_buffer_tokens=0))
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="test-doc",
            markdown_text=SIMPLE_MD,
        )
        # Should produce 3 chunks (one per H2 section)
        assert len(chunks) == 3
        assert chunks[0].title == "第一章"
        assert chunks[1].title == "第二章"
        assert chunks[2].title == "第三章"
        assert all(c.doc_id == "test-doc" for c in chunks)

    def test_chunk_ids_are_sequential(self) -> None:
        chunker = MarkdownChunker(ChunkConfig(context_buffer_tokens=0))
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="test-doc",
            markdown_text=SIMPLE_MD,
        )
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_id == f"test-doc#chunk-{i:03d}"
            assert chunk.chunk_index == i

    def test_context_preview_is_set(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="d",
            markdown_text=SIMPLE_MD,
        )
        for chunk in chunks:
            assert len(chunk.context_preview) > 0
            assert len(chunk.context_preview) <= 120

    def test_context_buffer_appended(self) -> None:
        """With a buffer, chunks should include surrounding context."""
        chunker = MarkdownChunker(ChunkConfig(
            max_chunk_tokens=9999,
            context_buffer_tokens=50,
        ))
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="d",
            markdown_text=SIMPLE_MD,
        )
        # The middle chunk should have context from both sides
        middle = chunks[1]
        assert len(middle.content) > 0
        # raw_content should be shorter (no buffer)
        assert len(middle.raw_content) <= len(middle.content)

    def test_long_section_splitting(self) -> None:
        """A section over max_chunk_tokens should be split."""
        chunker = MarkdownChunker(ChunkConfig(
            max_chunk_tokens=100,
            context_buffer_tokens=0,
        ))
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/long.md"),
            doc_id="d",
            markdown_text=LONG_SECTION_MD,
        )
        # Should have more than 2 chunks (long section split + short section)
        assert len(chunks) >= 3

    def test_no_h2_document(self) -> None:
        """Document with no H2 should still produce chunks."""
        chunker = MarkdownChunker(ChunkConfig(context_buffer_tokens=0))
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/no_h2.md"),
            doc_id="d",
            markdown_text=NO_H2_MD,
        )
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.content.strip() != ""

    def test_empty_document(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/empty.md"),
            doc_id="d",
            markdown_text="",
        )
        assert chunks == []

    def test_extract_title_from_h1(self) -> None:
        chunker = MarkdownChunker()
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="d",
            markdown_text="# 告警分级体系\n\n## P0\n\n内容",
        )
        # Each chunk keeps its H2 as title
        assert chunks[0].title == "P0"

    def test_raw_content_no_buffer(self) -> None:
        """raw_content should be the original section without buffer."""
        chunker = MarkdownChunker(ChunkConfig(context_buffer_tokens=50))
        chunks = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="d",
            markdown_text=SIMPLE_MD,
        )
        for chunk in chunks:
            # raw_content should not contain surrounding heading context
            assert "##" not in (chunk.raw_content or "")

    def test_reproducible_chunking(self) -> None:
        """Same input should produce same chunks."""
        chunker = MarkdownChunker()
        chunks1 = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="d",
            markdown_text=SIMPLE_MD,
        )
        chunks2 = chunker.chunk_document(
            source_file=Path("knowledge/test.md"),
            doc_id="d",
            markdown_text=SIMPLE_MD,
        )
        assert len(chunks1) == len(chunks2)
        for c1, c2 in zip(chunks1, chunks2):
            assert c1.content == c2.content
            assert c1.chunk_id == c2.chunk_id


class TestChunkConfig:
    def test_defaults(self) -> None:
        config = ChunkConfig()
        assert config.max_chunk_tokens == 512
        assert config.context_buffer_tokens == 50

    def test_custom(self) -> None:
        config = ChunkConfig(max_chunk_tokens=256, context_buffer_tokens=80)
        assert config.max_chunk_tokens == 256
        assert config.context_buffer_tokens == 80
