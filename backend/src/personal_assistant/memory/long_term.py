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


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "memory"


def _with_trailing_newline(value: str) -> str:
    return value.rstrip() + "\n"
