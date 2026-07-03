#!/usr/bin/env python3
"""
s10_long_term_memory.py — 长期记忆系统

跨 session 的持久化知识存储。
Checkpoint 是 session 级（短期），Long-Term Memory 是 cross-session（长期）。

三大子系统：
  Selection    — LLM reflection 决定什么值得记住（"记住该记的，忘掉该忘的"）
  Extraction   — 将对话提炼为一条自包含的结构化事实
  Consolidation — 写入 .memory/ 目录并维护 MEMORY.md 索引

.memory/ 目录结构：
  USER.md    — 用户偏好、背景、风格
  SYSTEM.md  — 系统知识、架构决策、项目约定
  MEMORY.md  — 索引，链接到各记忆文件
  *.md       — 单条记忆（YAML frontmatter + markdown body）

源码参考: backend/src/personal_assistant/memory/long_term.py

Usage:
    python s10_long_term_memory/code.py
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Optional


# ═══════════════════════════════════════════════════════════
# LongTermMemoryStore
# ═══════════════════════════════════════════════════════════

class LongTermMemoryStore:
    """File-based persistent memory across sessions.

    One fact per file, YAML frontmatter for metadata, MEMORY.md as index.
    read_all() is called at session start to inject memories into the system prompt.
    """

    def __init__(self, root: str = ".memory"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for fname, heading in [
            ("USER.md", "# User\n\n"),
            ("SYSTEM.md", "# System\n\n"),
            ("MEMORY.md", "# Memory Index\n\n"),
        ]:
            p = self.root / fname
            if not p.exists():
                p.write_text(heading, encoding="utf-8")

    # ── CRUD ──────────────────────────────────────────────

    def save_memory(self, category: str, name: str, content: str,
                    metadata: Optional[dict] = None) -> Path:
        """Write a memory fact as a single .md file with YAML frontmatter."""
        safe = _safe_slug(name)
        meta = dict(metadata or {})
        meta.setdefault("name", name)
        meta.setdefault("category", category)
        meta.setdefault("created", datetime.now().strftime("%Y-%m-%d %H:%M"))

        fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
        path = self.root / f"{safe}.md"
        path.write_text(fm + content.rstrip() + "\n", encoding="utf-8")

        self._upsert_index(
            f"- [{name}]({path.name}) - {content[:80]}", path.name
        )
        return path

    def list_memories(self, category: Optional[str] = None) -> list[Path]:
        """List memory files, optionally filtered by category from frontmatter."""
        core = {"USER.md", "SYSTEM.md", "MEMORY.md"}
        files = [f for f in sorted(self.root.glob("*.md")) if f.name not in core]
        if not category:
            return files
        return [f for f in files if _read_category(f) == category]

    def get_memory(self, name: str) -> Optional[str]:
        """Read a memory file by name. Returns None if not found."""
        path = self.root / (name if name.endswith(".md") else f"{name}.md")
        return path.read_text(encoding="utf-8") if path.exists() else None

    def delete_memory(self, name: str) -> bool:
        """Delete a memory file and remove its index entry."""
        path = self.root / (name if name.endswith(".md") else f"{name}.md")
        if not path.exists():
            return False
        path.unlink()
        self._remove_from_index(path.name)
        return True

    # ── Index management ──────────────────────────────────

    def _upsert_index(self, line: str, filename: str):
        idx = self.root / "MEMORY.md"
        lines = idx.read_text(encoding="utf-8").splitlines()
        pat = re.compile(rf"^- \[[^\]]+\]\({re.escape(filename)}\)")
        filtered = [l for l in lines if not pat.match(l)]
        if filtered and filtered[-1] != "":
            filtered.append("")
        filtered.append(line)
        idx.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")

    def _remove_from_index(self, filename: str):
        idx = self.root / "MEMORY.md"
        if not idx.exists():
            return
        lines = idx.read_text(encoding="utf-8").splitlines()
        pat = re.compile(rf"^- \[[^\]]+\]\({re.escape(filename)}\)")
        idx.write_text(
            "\n".join(l for l in lines if not pat.match(l)).rstrip() + "\n",
            encoding="utf-8",
        )

    # ── System prompt injection ───────────────────────────

    def read_all(self) -> str:
        """Read all memories. Called at session start to inject into system prompt."""
        if not self.root.exists():
            return ""
        parts = []
        for fname in ["MEMORY.md", "SYSTEM.md", "USER.md"]:
            p = self.root / fname
            if p.exists():
                c = p.read_text(encoding="utf-8").strip()
                heading = fname.replace(".md", "")
                if c and not re.sub(
                    rf"^#\s*{heading}\s*", "", c, flags=re.IGNORECASE
                ).strip():
                    continue
                parts.append(c)
        for mem in self.list_memories():
            body = _extract_body(mem)
            if body:
                parts.append(body)
        return "\n\n".join(parts)


# ── Helpers ──────────────────────────────────────────────────

def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "memory"


def _read_category(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^category:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _extract_body(path: Path) -> str:
    """Extract markdown body from a memory file, stripping YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].strip()
    return text.strip()


# ═══════════════════════════════════════════════════════════
# Memory Reflection Node
# ═══════════════════════════════════════════════════════════

def memory_reflection(store: LongTermMemoryStore, conversation: str):
    """Post-turn background reflection: LLM decides what to remember.

    Three subsystems (simulated here; in production this is an LLM call):
      Selection    — "记住该记的，忘掉该忘的": filter for durable value
      Extraction   — Distill into one self-contained fact per file
      Consolidation — Write to .memory/, update MEMORY.md index
    """
    reflections = _simulate_reflection(conversation)
    for r in reflections:
        store.save_memory(r["category"], r["name"], r["content"], r.get("metadata"))
        print(f"  [memory] {r['name']} → .memory/ ({r['category']})")


def _simulate_reflection(text: str) -> list[dict]:
    """Simulate LLM reflection. Replace with actual LLM call in production."""
    results = []
    t = text.lower()

    if any(kw in t for kw in ["prefer", "喜欢", "偏好", "常用", "习惯"]):
        results.append({
            "category": "user",
            "name": "user-preference",
            "content": text[:120],
            "metadata": {"tags": "preference"},
        })

    if any(kw in t for kw in ["decided", "决定", "采用", "选型", "选择"]):
        results.append({
            "category": "system",
            "name": "tech-decision",
            "content": text[:120],
            "metadata": {"tags": "architecture, decision"},
        })

    if any(kw in t for kw in ["convention", "约定", "规范", "规则", "标准"]):
        results.append({
            "category": "system",
            "name": "convention",
            "content": text[:120],
            "metadata": {"tags": "convention"},
        })

    if any(kw in t for kw in ["背景", "经验", "工程师", "developer", "全栈"]):
        results.append({
            "category": "user",
            "name": "user-background",
            "content": text[:120],
            "metadata": {"tags": "background, profile"},
        })

    return results


# ═══════════════════════════════════════════════════════════
# Demo
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 58)
    print("s10: Long-Term Memory — 长期记忆系统")
    print("=" * 58)
    print()

    store = LongTermMemoryStore(".memory")

    # Simulate 4 conversation turns, each followed by reflection
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

    # Show .memory/ directory structure
    print("─" * 58)
    print(".memory/ 目录结构：")
    print("─" * 58)
    for p in sorted(store.root.glob("*.md")):
        print(f"  {p.name} ({p.stat().st_size} bytes)")

    # Show MEMORY.md index
    print()
    print("─" * 58)
    print("MEMORY.md 索引（自动维护）：")
    print("─" * 58)
    print((store.root / "MEMORY.md").read_text(encoding="utf-8"))

    # Demonstrate read_all — this is what gets injected into system prompt
    print("─" * 58)
    print("read_all() 注入到 system prompt 的内容（前 500 字符）：")
    print("─" * 58)
    all_memories = store.read_all()
    print(all_memories[:500])
    if len(all_memories) > 500:
        print("...")
