from pathlib import Path

import pytest


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Create a minimal skill directory with SKILL.md and skill.py."""
    d = tmp_path / "test-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "# Test Skill\n\nUse this skill to test things.\n\nAvailable tools:\n- `do_thing`\n",
        encoding="utf-8",
    )
    (d / "skill.py").write_text(
        "from langchain_core.tools import tool\n\n"
        "@tool\n"
        "def do_thing(query: str) -> str:\n"
        '    """Do a thing."""\n'
        '    return f"done: {query}"\n\n'
        "TOOLS = [do_thing]\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def skill_dir_no_tools(tmp_path: Path) -> Path:
    """Create a skill directory with SKILL.md but no skill.py."""
    d = tmp_path / "text-only-skill"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "# Text Only Skill\n\nThis skill has no tools, just instructions.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def multi_skill_dir(tmp_path: Path) -> Path:
    """Create a directory with multiple skills."""
    # Skill A: has tools
    a = tmp_path / "skill-a"
    a.mkdir()
    (a / "SKILL.md").write_text(
        "# Skill Alpha\n\nHandles alpha tasks.\n\nAvailable tools:\n- `alpha_tool`\n",
        encoding="utf-8",
    )
    (a / "skill.py").write_text(
        "from langchain_core.tools import tool\n\n"
        "@tool\n"
        "def alpha_tool(x: str) -> str:\n"
        '    """Alpha tool."""\n'
        '    return f"alpha: {x}"\n\n'
        "TOOLS = [alpha_tool]\n",
        encoding="utf-8",
    )

    # Skill B: no tools
    b = tmp_path / "skill-b"
    b.mkdir()
    (b / "SKILL.md").write_text(
        "# Skill Beta\n\nHandles beta tasks with no tools.\n",
        encoding="utf-8",
    )

    # Skill C: has tools + scripts subfolder
    c = tmp_path / "skill-c"
    c.mkdir()
    (c / "SKILL.md").write_text(
        "# Skill Charlie\n\nHandles charlie tasks.\n",
        encoding="utf-8",
    )
    (c / "skill.py").write_text(
        "from langchain_core.tools import tool\n\n"
        "@tool\n"
        "def charlie_tool(n: int) -> int:\n"
        '    """Charlie tool."""\n'
        "    return n * 2\n\n"
        "TOOLS = [charlie_tool]\n",
        encoding="utf-8",
    )
    scripts = c / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text("# helper script\n", encoding="utf-8")
    references = c / "references"
    references.mkdir()
    (references / "doc.md").write_text("# Reference Doc\n", encoding="utf-8")

    return tmp_path
