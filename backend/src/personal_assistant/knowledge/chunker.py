"""Markdown chunker with context buffer for semantic coherence.

Splits markdown documents by H2 headings, then further splits long sections
by paragraph boundaries. Each chunk gets a ~50-token context buffer from
surrounding sections so the embedding vector captures cross-section coherence.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from personal_assistant.knowledge.models import Chunk


@dataclass(frozen=True)
class ChunkConfig:
    """Configuration for the markdown chunker."""

    max_chunk_tokens: int = 512
    """Maximum tokens per chunk. Sections longer than this are split further."""

    min_chunk_tokens: int = 100
    """Minimum tokens per chunk. Smaller chunks are merged with neighbors."""

    context_buffer_tokens: int = 50
    """Number of tokens to prepend/append from surrounding sections."""


def _count_tokens(text: str) -> int:
    """Rough token count: Chinese chars ≈ 1 token, English words ≈ 1.3 tokens.

    This is a heuristic — the exact count is not critical since BGE-M3
    has an 8192-token context window.
    """
    # Count CJK characters (each ≈ 1 token)
    cjk = len(re.findall(r"[一-鿿　-〿＀-￯]", text))
    # Count remaining "words" (split on whitespace)
    remaining = re.sub(r"[一-鿿　-〿＀-￯]", " ", text)
    words = len(remaining.split())
    return cjk + max(1, int(words * 1.3))


def _extract_title(markdown_text: str) -> str:
    """Extract the first H1 heading as the document title."""
    for line in markdown_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return ""


@dataclass
class _Section:
    """Internal: a markdown section bounded by an H2 heading."""

    heading: str  # H2 heading text (without ## prefix)
    body: str     # Section body text
    start_line: int


def _split_sections(markdown_text: str) -> list[_Section]:
    """Split markdown by H2 headings into sections.

    H1 headings and blank lines before the first H2 are stripped from the body.
    """
    lines = markdown_text.split("\n")
    sections: list[_Section] = []
    current_heading = ""
    current_body: list[str] = []
    current_start = 0
    seen_first_h2 = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            # Save previous section (strip H1/blank preamble from first section)
            if current_body:
                body_text = "\n".join(current_body).strip()
                if body_text:
                    sections.append(_Section(
                        heading=current_heading or "正文",
                        body=body_text,
                        start_line=current_start,
                    ))
            current_heading = stripped[3:].strip()
            current_body = []
            current_start = i + 1
            seen_first_h2 = True
        elif not seen_first_h2 and (stripped.startswith("# ") or stripped == ""):
            # Skip H1 title and blank lines before first H2
            continue
        else:
            current_body.append(line)

    # Last section
    if current_body:
        body_text = "\n".join(current_body).strip()
        if body_text:
            sections.append(_Section(
                heading=current_heading or "正文",
                body=body_text,
                start_line=current_start,
            ))

    # If no H2 at all, treat entire document as one section
    if not sections:
        body_lines = [
            line for line in lines
            if not (line.strip().startswith("# ") and not line.strip().startswith("## "))
        ]
        body_text = "\n".join(body_lines).strip()
        if body_text:
            sections.append(_Section(heading="正文", body=body_text, start_line=0))

    return sections


def _split_long_section(section: _Section, max_tokens: int) -> list[_Section]:
    """Split a section that exceeds max_tokens by paragraph boundaries.

    If a single paragraph still exceeds max_tokens, it is further split
    by sentence boundaries (。！？. ! ?).
    """
    if _count_tokens(section.body) <= max_tokens:
        return [section]

    # Split by blank-line-separated paragraphs
    raw_paragraphs = re.split(r"\n\s*\n", section.body)

    # Further split any paragraph that is itself too long
    paragraphs: list[str] = []
    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue
        if _count_tokens(para) <= max_tokens:
            paragraphs.append(para)
        else:
            # Split by sentence boundaries
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
            current: list[str] = []
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                test = " ".join(current + [sent]) if current else sent
                if _count_tokens(test) > max_tokens and current:
                    paragraphs.append(" ".join(current))
                    current = [sent]
                else:
                    current.append(sent)
            if current:
                paragraphs.append(" ".join(current))

    # Group paragraphs into chunks that fit within max_tokens
    result: list[_Section] = []
    current_paras: list[str] = []
    for para in paragraphs:
        test = "\n\n".join(current_paras + [para]) if current_paras else para
        if _count_tokens(test) > max_tokens and current_paras:
            result.append(_Section(
                heading=section.heading,
                body="\n\n".join(current_paras),
                start_line=section.start_line,
            ))
            current_paras = [para]
        else:
            current_paras.append(para)

    if current_paras:
        result.append(_Section(
            heading=section.heading,
            body="\n\n".join(current_paras),
            start_line=section.start_line,
        ))

    return result if result else [section]


def _merge_short_sections(sections: list[_Section], min_tokens: int) -> list[_Section]:
    """Merge consecutive sections that share the same heading into larger chunks.

    Only merges when the combined section stays within a reasonable size.
    Does NOT merge across different H2 boundaries — each H2 section keeps its
    semantic identity.
    """
    if not sections or min_tokens <= 0:
        return list(sections)

    result: list[_Section] = []
    buffer: list[_Section] = []

    for section in sections:
        if not buffer:
            buffer.append(section)
            continue

        same_heading = buffer[-1].heading == section.heading
        buffer_tokens = sum(_count_tokens(s.body) for s in buffer)

        if same_heading and buffer_tokens < min_tokens:
            # Merge fragments from the same parent section
            buffer.append(section)
        else:
            # Flush buffer
            if len(buffer) == 1:
                result.append(buffer[0])
            else:
                merged_body = "\n\n".join(s.body for s in buffer)
                result.append(_Section(
                    heading=buffer[0].heading,
                    body=merged_body,
                    start_line=buffer[0].start_line,
                ))
            buffer = [section]

    # Flush remaining
    if buffer:
        if len(buffer) == 1:
            result.append(buffer[0])
        else:
            merged_body = "\n\n".join(s.body for s in buffer)
            result.append(_Section(
                heading=buffer[0].heading,
                body=merged_body,
                start_line=buffer[0].start_line,
            ))

    return result


def _extract_buffer(text: str, token_count: int, from_end: bool = True) -> str:
    """Extract approximately token_count tokens from the end or start of text."""
    if token_count <= 0 or not text:
        return ""
    lines = text.split("\n")
    if from_end:
        # Take last N lines that fit within token budget
        collected: list[str] = []
        tokens = 0
        for line in reversed(lines):
            line_tokens = _count_tokens(line)
            if tokens + line_tokens > token_count and collected:
                break
            collected.insert(0, line)
            tokens += line_tokens
        return "\n".join(collected)
    else:
        collected = []
        tokens = 0
        for line in lines:
            line_tokens = _count_tokens(line)
            if tokens + line_tokens > token_count and collected:
                break
            collected.append(line)
            tokens += line_tokens
        return "\n".join(collected)


class MarkdownChunker:
    """Splits markdown into semantically coherent chunks with context buffers.

    Strategy:
    1. Split by H2 headings into sections
    2. Split long sections (> max_chunk_tokens) by paragraph boundaries
    3. Merge short sections (< min_chunk_tokens) with neighbors
    4. Add context buffer (~50 tokens) from surrounding sections to each chunk
    """

    def __init__(self, config: ChunkConfig | None = None) -> None:
        self.config = config or ChunkConfig()

    def chunk_document(
        self, *, source_file: Path, doc_id: str, markdown_text: str,
    ) -> list[Chunk]:
        """Parse markdown and return a list of Chunk objects ready for embedding."""
        if not markdown_text.strip():
            return []

        # Step 1: Split by H2 headings
        sections = _split_sections(markdown_text)
        if not sections:
            return []

        # Step 2: Split long sections
        expanded: list[_Section] = []
        for section in sections:
            expanded.extend(
                _split_long_section(section, self.config.max_chunk_tokens)
            )

        # Step 3: Merge short sections
        merged = _merge_short_sections(expanded, self.config.min_chunk_tokens)

        # Step 4: Build chunks with context buffers
        chunks: list[Chunk] = []
        for i, section in enumerate(merged):
            raw = section.body
            parts: list[str] = []

            # Prepend context from previous section
            if i > 0 and self.config.context_buffer_tokens > 0:
                prev_buffer = _extract_buffer(
                    merged[i - 1].body, self.config.context_buffer_tokens, from_end=True,
                )
                if prev_buffer:
                    parts.append(prev_buffer)

            # Main content
            parts.append(raw)

            # Append context from next section
            if i < len(merged) - 1 and self.config.context_buffer_tokens > 0:
                next_buffer = _extract_buffer(
                    merged[i + 1].body, self.config.context_buffer_tokens, from_end=False,
                )
                if next_buffer:
                    parts.append(next_buffer)

            content = "\n\n".join(parts)

            chunks.append(Chunk(
                chunk_id=f"{doc_id}#chunk-{i:03d}",
                doc_id=doc_id,
                chunk_index=i,
                title=section.heading,
                content=content,
                raw_content=raw,
            ))

        return chunks
