#!/usr/bin/env python3
"""
s10_long_term_memory.py -- Persistent cross-session memory with LLM reflection.

Checkpoint is session-level (short-term); this is cross-session (long-term).
Selection -> Extraction -> Consolidation. "记住该记的，忘掉该忘的"

Reference: backend/src/personal_assistant/memory/long_term.py
Usage: python s10_long_term_memory/code.py
"""
import re
from pathlib import Path
from datetime import datetime
from typing import Optional


class LongTermMemoryStore:
    """File-based persistent memory. .memory/ with USER.md, SYSTEM.md, MEMORY.md
    (index) and individual *.md files (one fact per file, YAML frontmatter + body).
    """
    def __init__(self, root: str = ".memory"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name, heading in [("USER.md", "# User\n\n"), ("SYSTEM.md", "# System\n\n"),
                              ("MEMORY.md", "# Memory Index\n\n")]:
            p = self.root / name
            if not p.exists():
                p.write_text(heading, encoding="utf-8")

    def save_memory(self, category: str, name: str, content: str,
                    metadata: Optional[dict] = None) -> Path:
        """Write a memory fact as a .md file with YAML frontmatter. Updates index."""
        safe = _slug(name)
        meta = dict(metadata or {})
        meta.update(name=name, category=category,
                     created=datetime.now().strftime("%Y-%m-%d %H:%M"))
        fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
        path = self.root / f"{safe}.md"
        path.write_text(fm + content.rstrip() + "\n", encoding="utf-8")
        self._upsert(f"- [{name}]({path.name}) - {content[:80]}", path.name)
        return path

    def list_memories(self, category: Optional[str] = None) -> list[Path]:
        core = {"USER.md", "SYSTEM.md", "MEMORY.md"}
        files = [f for f in sorted(self.root.glob("*.md")) if f.name not in core]
        return files if not category else [f for f in files if _category(f) == category]

    def get_memory(self, name: str) -> Optional[str]:
        path = self.root / (name if name.endswith(".md") else f"{name}.md")
        return path.read_text(encoding="utf-8") if path.exists() else None

    def delete_memory(self, name: str) -> bool:
        path = self.root / (name if name.endswith(".md") else f"{name}.md")
        if not path.exists():
            return False
        path.unlink()
        self._remove_index(path.name)
        return True

    def _upsert(self, line: str, filename: str):
        self._mutate(lambda ls: [l for l in ls if not re.match(
            rf"^- \[[^\]]+\]\({re.escape(filename)}\)", l)], line)

    def _remove_index(self, filename: str):
        self._mutate(lambda ls: [l for l in ls if not re.match(
            rf"^- \[[^\]]+\]\({re.escape(filename)}\)", l)])

    def _mutate(self, filter_fn, extra: Optional[str] = None):
        idx = self.root / "MEMORY.md"
        lines = filter_fn(idx.read_text(encoding="utf-8").splitlines())
        if extra:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(extra)
        idx.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def read_all(self) -> str:
        """Read all memories for injection into system prompt at session start."""
        if not self.root.exists():
            return ""
        parts = []
        for fn in ["MEMORY.md", "SYSTEM.md", "USER.md"]:
            p = self.root / fn
            if p.exists():
                c = p.read_text(encoding="utf-8").strip()
                h = fn.replace(".md", "")
                if c and re.sub(rf"^#\s*{h}\s*", "", c, flags=re.IGNORECASE).strip():
                    parts.append(c)
        for mem in self.list_memories():
            body = _body(mem)
            if body:
                parts.append(body)
        return "\n\n".join(parts)


# -- helpers ------------------------------------------------------------------

def _slug(v: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", v.strip()).strip("-._")
    return s or "memory"

def _category(path: Path) -> str:
    m = re.search(r"^category:\s*(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else ""

def _body(path: Path) -> str:
    t = path.read_text(encoding="utf-8")
    if t.startswith("---"):
        end = t.find("---", 3)
        if end != -1:
            return t[end + 3:].strip()
    return t.strip()


# -- memory_reflection node ---------------------------------------------------

def memory_reflection(store: LongTermMemoryStore, conversation: str):
    """Post-turn background LLM call: decide what's worth persisting."""
    for r in _reflect(conversation):
        store.save_memory(r["c"], r["n"], r["t"], r.get("m"))
        print(f"  [memory] {r['n']} -> .memory/ ({r['c']})")


def _reflect(text: str) -> list[dict]:
    """Simulated LLM reflection. Replace with real LLM call in production.
    Selection: filter for durable value. Extraction: distill to one crisp fact.
    Consolidation: write to .memory/, update index."""
    t, results = text.lower(), []
    if any(kw in t for kw in ["prefer", "喜欢", "偏好", "常用", "习惯"]):
        results.append({"c": "user", "n": "user-preference", "t": text[:120],
            "m": {"tags": "preference"}})
    if any(kw in t for kw in ["decided", "决定", "采用", "选型", "选择"]):
        results.append({"c": "system", "n": "tech-decision", "t": text[:120],
            "m": {"tags": "architecture, decision"}})
    if any(kw in t for kw in ["convention", "约定", "规范", "规则", "标准"]):
        results.append({"c": "system", "n": "convention", "t": text[:120],
            "m": {"tags": "convention"}})
    if any(kw in t for kw in ["背景", "经验", "工程师", "developer", "全栈"]):
        results.append({"c": "user", "n": "user-background", "t": text[:120],
            "m": {"tags": "background, profile"}})
    return results


# -- demo ---------------------------------------------------------------------

if __name__ == "__main__":
    print("s10: Long-Term Memory -- 长期记忆系统\n")

    store = LongTermMemoryStore(".memory")
    turns = [
        "用户偏好：用中文回复，代码注释用中文。喜欢简洁风格。",
        "技术决策：采用 Redis 做缓存，PostgreSQL 做主数据库。",
        "项目约定：API 统一用 /api/v1/ 前缀，错误码用 RFC 7807。",
        "用户背景：全栈工程师，主要用 Python 和 TypeScript，10年经验。",
    ]
    for i, t in enumerate(turns, 1):
        print(f"[turn {i}] {t[:60]}...")
        memory_reflection(store, t)
        print()

    print("--- .memory/ ---")
    for p in sorted(store.root.glob("*.md")):
        print(f"  {p.name} ({p.stat().st_size}b)")

    print("\n--- MEMORY.md Index ---")
    print((store.root / "MEMORY.md").read_text(encoding="utf-8"))

    print("--- read_all() for system prompt injection ---")
    print(store.read_all()[:500])
