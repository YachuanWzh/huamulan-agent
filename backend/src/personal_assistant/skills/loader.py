import hashlib
import importlib.util
import sys
from pathlib import Path

from langchain_core.tools import BaseTool

from personal_assistant.skills.base import Skill


class SkillRegistry:
    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir).resolve()
        self._skills: dict[str, Skill] = {}
        self.reload()

    @property
    def skills(self) -> dict[str, Skill]:
        return self._skills

    @property
    def all_tools(self) -> list[BaseTool]:
        tools: list[BaseTool] = []
        for skill in self._skills.values():
            tools.extend(skill.tools)
        return tools

    def reload(self) -> list[Skill]:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        loaded: dict[str, Skill] = {}
        for skill_dir in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            instructions = skill_md.read_text(encoding="utf-8")
            loaded[skill_dir.name] = Skill(
                name=skill_dir.name,
                description=_first_heading_or_line(instructions),
                instructions=instructions,
                path=skill_dir,
                tools=_load_tools(skill_dir),
            )
        self._skills = loaded
        return list(loaded.values())

    def tool_map_for_skills(self, skill_names: list[str]) -> dict[str, BaseTool]:
        selected = set(skill_names)
        tools: dict[str, BaseTool] = {}
        for skill in self._skills.values():
            if skill.name not in selected:
                continue
            for tool in skill.tools:
                tools[tool.name] = tool
        return tools


def _first_heading_or_line(markdown: str) -> str:
    for line in markdown.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text
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
