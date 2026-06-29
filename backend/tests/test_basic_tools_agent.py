from pathlib import Path

from personal_assistant.agent.agent import _active_tools_for_state
from personal_assistant.skills import SkillRegistry
from personal_assistant.tools.basic import build_basic_tools


def test_active_tools_include_basic_tools_even_without_selected_skills(
    skill_dir: Path, tmp_path: Path
):
    registry = SkillRegistry(skill_dir)
    basic_tools = build_basic_tools(tmp_path)

    tools = _active_tools_for_state(registry, [], basic_tools)
    names = {tool.name for tool in tools}

    assert "shell_command" in names
    assert "read_file" in names
    assert "do_thing" not in names


def test_active_tools_merge_basic_tools_with_selected_skill_tools(
    skill_dir: Path, tmp_path: Path
):
    registry = SkillRegistry(skill_dir)
    registry.load_skill("test-skill")
    basic_tools = build_basic_tools(tmp_path)

    tools = _active_tools_for_state(registry, ["test-skill"], basic_tools)
    names = {tool.name for tool in tools}

    assert "shell_command" in names
    assert "read_file" in names
    assert "do_thing" in names
