import hashlib
import importlib.util
import sys
import threading
from pathlib import Path

from langchain_core.tools import BaseTool

from personal_assistant.skills.base import Skill


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
        """Scan skills_dir and create Skill objects with meta only (no tool import)."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        loaded: dict[str, Skill] = {}
        for skill_dir in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            head = _read_first_line(skill_md)
            description = _first_heading_or_line(head)
            # Preserve already-loaded skills if they still exist on disk
            existing = self._skills.get(skill_dir.name)
            if existing and existing.loaded:
                loaded[skill_dir.name] = existing
            else:
                loaded[skill_dir.name] = Skill(
                    name=skill_dir.name,
                    description=description,
                    path=skill_dir,
                    instructions_path=skill_md,
                )
        self._skills = loaded
        return list(loaded.values())

    # ── full loading (phase 2 — on demand) ──────────────────────

    def load_skill(self, name: str) -> None:
        """Load full content (instructions + tools) for a specific skill."""
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f"Unknown skill: {name}")
        if skill.loaded:
            return
        skill.instructions = skill.instructions_path.read_text(encoding="utf-8")
        skill.tools = _load_tools(skill.path)

    # ── bulk reload (backward compat) ───────────────────────────

    def reload(self) -> list[Skill]:
        """Rescan and fully load all skills."""
        self.scan_metadata()
        for name in self._skills:
            skill = self._skills[name]
            skill.instructions = skill.instructions_path.read_text(encoding="utf-8")
            skill.tools = _load_tools(skill.path)
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


def _read_first_line(path: Path) -> str:
    """Read only the first non-empty line of a file."""
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


def _first_heading_or_line(text: str) -> str:
    cleaned = text.strip().lstrip("#").strip()
    return cleaned if cleaned else "No description"


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
