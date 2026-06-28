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

    @property
    def loaded(self) -> bool:
        return self.instructions is not None

    @property
    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]
