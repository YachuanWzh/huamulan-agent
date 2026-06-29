import hashlib
import importlib.util
import sys
import threading
from pathlib import Path

import yaml
from langchain_core.tools import BaseTool

from personal_assistant.skills.base import Skill
from personal_assistant.skills.script_tool import build_script_tool


class SkillRegistry:
    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).resolve()
        self._skills: dict[str, Skill] = {}
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.scan_metadata()

    # ── properties ──────────────────────────────────────────────

    @property
    def skills(self) -> dict[str, Skill]:
        return self._skills

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    @property
    def all_tools(self) -> list[BaseTool]:
        tools: list[BaseTool] = []
        for skill in self._skills.values():
            if skill.loaded:
                tools.extend(skill.tools)
        return tools

    @property
    def is_watching(self) -> bool:
        return self._watcher_thread is not None and self._watcher_thread.is_alive()

    # ── metadata scanning (phase 1 — lightweight) ───────────────

    def scan_metadata(self) -> list[Skill]:
        """Scan skills_dir and create Skill objects with meta only (no tool import).

        Reads YAML frontmatter (name, description, triggers, scripts) if present,
        otherwise falls back to the first Markdown heading. No ``skill.py`` is
        imported and no script tools are built — that happens in :meth:`load_skill`.
        """
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        loaded: dict[str, Skill] = {}
        for skill_dir in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            meta = _parse_frontmatter(skill_md)
            name = meta.get("name", skill_dir.name)
            description = meta.get("description") or _first_heading(skill_md)
            triggers = [str(t) for t in meta.get("triggers", []) if t]
            script_decls = _valid_script_decls(meta.get("scripts", []))
            mtime_ns = skill_md.stat().st_mtime_ns
            source_hash = _file_hash(skill_md)
            # Preserve an already-loaded skill only if its SKILL.md is unchanged
            # since the last scan. An edit resets it to unloaded so the next
            # route_skills picks up new triggers/scripts/instructions.
            existing = self._skills.get(skill_dir.name)
            if (
                existing
                and existing.loaded
                and existing.source_mtime_ns == mtime_ns
                and existing.source_hash == source_hash
            ):
                loaded[skill_dir.name] = existing
            else:
                loaded[skill_dir.name] = Skill(
                    name=name,
                    description=description,
                    path=skill_dir,
                    instructions_path=skill_md,
                    triggers=triggers,
                    script_decls=script_decls,
                    source_mtime_ns=mtime_ns,
                    source_hash=source_hash,
                )
        self._skills = loaded
        return list(loaded.values())

    # ── full loading (phase 2 — on demand) ──────────────────────

    def load_skill(self, name: str) -> None:
        """Load full content (instructions + tools) for a specific skill.

        Builds script tools from the frontmatter ``scripts`` declarations and
        imports ``skill.py`` TOOLS if present. Both are cheap to build — the
        expensive part (subprocess execution) only happens when the agent calls
        the tool.
        """
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"Unknown skill: {name}")
        if skill.loaded:
            return
        skill.instructions = skill.instructions_path.read_text(encoding="utf-8")
        script_tools = [build_script_tool(decl, skill.path) for decl in skill.script_decls]
        skill.tools = script_tools + _load_tools(skill.path)

    # ── bulk reload (backward compat) ───────────────────────────

    def reload(self) -> list[Skill]:
        """Rescan and fully load all skills."""
        self.scan_metadata()
        for name in self._skills:
            self.load_skill(name)
        return list(self._skills.values())

    # ── tool helpers ────────────────────────────────────────────

    def tool_map_for_skills(self, skill_names: list[str]) -> dict[str, BaseTool]:
        selected = set(skill_names)
        tools: dict[str, BaseTool] = {}
        for skill in self._skills.values():
            if skill.name not in selected:
                continue
            for tool in skill.tools:
                tools[tool.name] = tool
        return tools

    # ── file watching (hot-plug) ────────────────────────────────

    def start_watching(self) -> None:
        """Start a background thread that watches skills_dir for changes."""
        if self.is_watching:
            return
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="skill-watcher"
        )
        self._watcher_thread.start()

    def stop_watching(self) -> None:
        """Stop the file-watching background thread."""
        self._stop_event.set()
        if self._watcher_thread is not None:
            self._watcher_thread.join(timeout=5)
            self._watcher_thread = None

    def _watch_loop(self) -> None:
        try:
            from watchfiles import watch
        except ImportError:
            return

        for _changes in watch(
            self.skills_dir,
            stop_event=self._stop_event,
            watch_filter=lambda change, path: Path(path).name == "SKILL.md",
        ):
            self.scan_metadata()


# ── module-private helpers ──────────────────────────────────────


def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a Markdown file.

    Reads the block between the opening and closing ``---`` delimiters and parses
    it with ``yaml.safe_load`` so nested structures (``triggers`` list,
    ``scripts`` list of dicts) are preserved. Returns an empty dict if no valid
    frontmatter is found. PyYAML is a declared dependency.
    """
    try:
        with path.open(encoding="utf-8") as fh:
            first = fh.readline().strip()
            if first != "---":
                return {}
            lines: list[str] = []
            for line in fh:
                if line.strip() == "---":
                    break
                lines.append(line)
    except (OSError, UnicodeDecodeError):
        return {}

    data = yaml.safe_load("".join(lines))
    if not isinstance(data, dict):
        return {}
    return data


def _file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _valid_script_decls(raw: list) -> list[dict]:
    """Keep only script declarations with a name and a non-empty list ``command``.

    A common mistake is writing ``command: python scripts/x.py`` (a string).
    Without validation, ``list(str)`` would silently turn it into a per-character
    argv and fail cryptically at invoke time. Drop malformed decls instead.
    """
    decls: list[dict] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        if not entry.get("name"):
            continue
        command = entry.get("command")
        if not isinstance(command, list) or not command:
            continue
        decls.append(entry)
    return decls


def _first_heading(path: Path) -> str:
    """Extract the first Markdown heading (after frontmatter) as description."""
    try:
        with path.open(encoding="utf-8") as fh:
            in_frontmatter = False
            for line in fh:
                stripped = line.strip()
                if stripped == "---":
                    in_frontmatter = not in_frontmatter
                    continue
                if in_frontmatter:
                    continue
                if stripped.startswith("#"):
                    return stripped.lstrip("#").strip()
    except (OSError, UnicodeDecodeError):
        pass
    return "No description"


def _load_tools(skill_dir: Path) -> list[BaseTool]:
    module_path = skill_dir / "skill.py"
    if not module_path.exists():
        return []

    module_hash = hashlib.sha1(str(module_path).encode("utf-8")).hexdigest()[:12]
    module_name = f"personal_assistant_dynamic_skill_{skill_dir.name}_{module_hash}"
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tools = getattr(module, "TOOLS", [])
    return [tool for tool in tools if isinstance(tool, BaseTool)]
