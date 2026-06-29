from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import BaseTool


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    instructions_path: Path
    instructions: str | None = None
    tools: list[BaseTool] = field(default_factory=list)
    # Lightweight metadata parsed from frontmatter at scan time (phase 1).
    triggers: list[str] = field(default_factory=list)
    script_decls: list[dict] = field(default_factory=list)
    # mtime of SKILL.md at scan time — used to detect edits for hot-plug reload.
    source_mtime_ns: int | None = None
    source_hash: str | None = None

    @property
    def loaded(self) -> bool:
        return self.instructions is not None

    @property
    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]
