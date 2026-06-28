"""End-to-end integration test for progressive skill loading."""

from pathlib import Path

from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.agent.router import build_system_prompt, _keyword_route


class TestProgressiveLoadingE2E:
    """Verify the full progressive loading flow: scan → match → load → prompt."""

    def test_full_progressive_flow(self, multi_skill_dir: Path):
        # Phase 1: Registry scans metadata only (lightweight)
        registry = SkillRegistry(multi_skill_dir)
        assert len(registry.skills) == 3
        for skill in registry.skills.values():
            assert not skill.loaded
            assert skill.tools == []
            assert skill.instructions is None

        # Phase 2: Router matches skills by meta only
        matched = _keyword_route(registry, "I need alpha help")
        assert matched == ["skill-a"]

        # Phase 3: Load full content for matched skills only
        for name in matched:
            registry.load_skill(name)

        # Only skill-a should be loaded
        assert registry.skills["skill-a"].loaded
        assert not registry.skills["skill-b"].loaded
        assert not registry.skills["skill-c"].loaded

        # Phase 4: Build progressive system prompt
        system_msg = build_system_prompt(registry, selected=matched)
        content = system_msg.content

        # Meta overview includes ALL skills
        assert "skill-a" in content
        assert "skill-b" in content
        assert "skill-c" in content

        # Full instructions only for selected skill
        assert "## Skill: skill-a" in content
        assert "## Skill: skill-b" not in content
        assert "## Skill: skill-c" not in content

        # Tools available only for loaded skills
        tool_map = registry.tool_map_for_skills(matched)
        assert "alpha_tool" in tool_map
        assert len(tool_map) == 1

    def test_fallback_loads_all_when_no_match(self, multi_skill_dir: Path):
        """When no skills match, all skills are selected (fallback)."""
        registry = SkillRegistry(multi_skill_dir)

        matched = _keyword_route(registry, "zzzzz completely unrelated")
        assert matched == []

        # Fallback: select all
        if not matched and registry.skills:
            matched = list(registry.skills)

        assert len(matched) == 3

        for name in matched:
            registry.load_skill(name)

        # All skills loaded
        for skill in registry.skills.values():
            assert skill.loaded

    def test_hot_plug_add_and_remove(self, multi_skill_dir: Path):
        """Skills can be added and removed at runtime."""
        registry = SkillRegistry(multi_skill_dir)
        assert len(registry.skills) == 3

        # Add a new skill
        new = multi_skill_dir / "skill-delta"
        new.mkdir()
        (new / "SKILL.md").write_text("# Skill Delta\nNew skill.\n", encoding="utf-8")

        # Rescan picks it up
        registry.scan_metadata()
        assert "skill-delta" in registry.skills
        assert not registry.skills["skill-delta"].loaded

        # Remove a skill
        import shutil
        shutil.rmtree(multi_skill_dir / "skill-a")

        registry.scan_metadata()
        assert "skill-a" not in registry.skills
        assert "skill-delta" in registry.skills

    def test_skill_folder_with_subfolders(self, multi_skill_dir: Path):
        """Skills with scripts/ and references/ subfolders work correctly."""
        registry = SkillRegistry(multi_skill_dir)

        # skill-c has scripts/ and references/ subfolders
        assert "skill-c" in registry.skills
        registry.load_skill("skill-c")

        skill_c = registry.skills["skill-c"]
        assert skill_c.loaded
        assert len(skill_c.tools) == 1
        assert skill_c.tools[0].name == "charlie_tool"

        # Subfolders exist
        assert (skill_c.path / "scripts").is_dir()
        assert (skill_c.path / "references").is_dir()
