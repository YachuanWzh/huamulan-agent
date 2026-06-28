from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    instructions: str
    tools: list[BaseTool] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools]
