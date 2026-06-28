from pathlib import Path

from personal_assistant.skills.base import Skill


class TestSkillMeta:
    def test_skill_with_meta_only(self, tmp_path: Path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# My Skill\n\nFull instructions here.\n", encoding="utf-8")

        skill = Skill(
            name="my-skill",
            description="My Skill",
            path=tmp_path,
            instructions_path=skill_md,
        )
        assert skill.name == "my-skill"
        assert skill.description == "My Skill"
        assert skill.instructions is None
        assert skill.tools == []
        assert not skill.loaded

    def test_skill_loaded_flag_false_initially(self, tmp_path: Path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# Foo\n", encoding="utf-8")
        skill = Skill(name="foo", description="Foo", path=tmp_path, instructions_path=skill_md)
        assert not skill.loaded

    def test_skill_loaded_flag_true_when_instructions_set(self, tmp_path: Path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# Foo\n", encoding="utf-8")
        skill = Skill(name="foo", description="Foo", path=tmp_path, instructions_path=skill_md)
        skill.instructions = "# Foo\n\nFull content."
        assert skill.loaded

    def test_tool_names(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from langchain_core.tools import BaseTool

        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# Foo\n", encoding="utf-8")
        tool_a = MagicMock(spec=BaseTool)
        tool_a.name = "tool_a"
        tool_b = MagicMock(spec=BaseTool)
        tool_b.name = "tool_b"
        skill = Skill(
            name="foo",
            description="Foo",
            path=tmp_path,
            instructions_path=skill_md,
            tools=[tool_a, tool_b],
        )
        assert skill.tool_names == ["tool_a", "tool_b"]
