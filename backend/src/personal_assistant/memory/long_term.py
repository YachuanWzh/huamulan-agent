from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class LongTermMemoryStore:
    root: Path

    def ensure_files(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _write_if_missing(self.root / "USER.md", "# User\n\n")
        _write_if_missing(self.root / "SYSTEM.md", "# System\n\n")
        _write_if_missing(self.root / "MEMORY.md", "# Memory Index\n\n")

    def add_memory(self, *, slug: str, title: str, summary: str, body: str) -> Path:
        self.ensure_files()
        safe_slug = _safe_slug(slug)
        title = title.strip() or safe_slug
        summary = summary.strip()
        memory_path = self.root / f"{safe_slug}.md"
        memory_path.write_text(_with_trailing_newline(body), encoding="utf-8")
        self._upsert_index_line(
            f"- [{title}]({memory_path.name}) - {summary}",
            memory_path.name,
        )
        return memory_path

    def _upsert_index_line(self, line: str, filename: str) -> None:
        index_path = self.root / "MEMORY.md"
        lines = index_path.read_text(encoding="utf-8").splitlines()
        prefix_pattern = re.compile(rf"^- \[[^\]]+\]\({re.escape(filename)}\) - ")
        filtered = [existing for existing in lines if not prefix_pattern.match(existing)]
        if filtered and filtered[-1] != "":
            filtered.append("")
        filtered.append(line)
        index_path.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")

    def read_all(self) -> str:
        """Read all memory files and return formatted markdown for system prompt injection.

        Reads USER.md, SYSTEM.md, MEMORY.md (the index), and all individually linked
        memory files. Returns an empty string when the store directory doesn't exist.
        """
        if not self.root.exists():
            return ""

        parts: list[str] = []

        # MEMORY.md index — the central index of all individual memory entries
        index_path = self.root / "MEMORY.md"
        if index_path.exists():
            index_content = index_path.read_text(encoding="utf-8").strip()
            if index_content:
                parts.append(index_content)

        # SYSTEM.md — project-level context
        system_path = self.root / "SYSTEM.md"
        if system_path.exists():
            system_content = system_path.read_text(encoding="utf-8").strip()
            # Filter out bare template heading with no real content
            if system_content and not _is_template_only(system_content, "System"):
                parts.append(f"### System Context\n\n{system_content}")

        # USER.md — user profile / preferences
        user_path = self.root / "USER.md"
        if user_path.exists():
            user_content = user_path.read_text(encoding="utf-8").strip()
            if user_content and not _is_template_only(user_content, "User"):
                parts.append(f"### User Context\n\n{user_content}")

        # Individual memory files referenced from MEMORY.md
        if index_path.exists():
            link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")
            for line in index_path.read_text(encoding="utf-8").splitlines():
                m = link_pattern.search(line)
                if m:
                    mem_path = self.root / m.group(2)
                    if mem_path.exists():
                        body = mem_path.read_text(encoding="utf-8").strip()
                        if body:
                            parts.append(body)

        return "\n\n".join(parts)


def _is_template_only(content: str, heading: str) -> bool:
    """Check if a file is only a bare heading with no substantive content."""
    stripped = re.sub(rf"^#\s*{heading}\s*", "", content, flags=re.IGNORECASE).strip()
    return stripped == ""


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "memory"


def _with_trailing_newline(value: str) -> str:
    return value.rstrip() + "\n"
