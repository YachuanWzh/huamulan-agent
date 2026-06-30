from pathlib import Path

from personal_assistant.skills.loader import SkillRegistry


class TestSkillRegistryScanMetadata:
    def test_scan_discovers_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        names = registry.skill_names
        assert set(names) == {"skill-a", "skill-b", "skill-c"}

    def test_scan_extracts_description(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        assert registry.skills["skill-a"].description == "Skill Alpha"
        assert registry.skills["skill-b"].description == "Skill Beta"
        assert registry.skills["skill-c"].description == "Skill Charlie"

    def test_scan_skips_dirs_without_skill_md(self, tmp_path: Path):
        empty = tmp_path / "empty-skill"
        empty.mkdir()
        valid = tmp_path / "valid-skill"
        valid.mkdir()
        (valid / "SKILL.md").write_text("# Valid\n", encoding="utf-8")

        registry = SkillRegistry(tmp_path)
        assert "valid-skill" in registry.skill_names
        assert "empty-skill" not in registry.skill_names

    def test_scan_does_not_import_skill_py(self, skill_dir: Path):
        """After scan, tools should be empty — skill.py is not imported yet."""
        registry = SkillRegistry(skill_dir)
        skill = registry.skills["test-skill"]
        assert skill.tools == []
        assert not skill.loaded


class TestSkillRegistryLoadSkill:
    def test_load_skill_loads_instructions(self, skill_dir: Path):
        registry = SkillRegistry(skill_dir)
        registry.load_skill("test-skill")
        skill = registry.skills["test-skill"]
        assert skill.loaded
        assert "Use this skill to test things" in skill.instructions

    def test_load_skill_loads_tools(self, skill_dir: Path):
        registry = SkillRegistry(skill_dir)
        registry.load_skill("test-skill")
        skill = registry.skills["test-skill"]
        assert len(skill.tools) == 1
        assert skill.tools[0].name == "do_thing"

    def test_load_skill_no_tools(self, skill_dir_no_tools: Path):
        registry = SkillRegistry(skill_dir_no_tools)
        registry.load_skill("text-only-skill")
        skill = registry.skills["text-only-skill"]
        assert skill.loaded
        assert skill.tools == []
        assert "just instructions" in skill.instructions

    def test_load_skill_idempotent(self, skill_dir: Path):
        registry = SkillRegistry(skill_dir)
        registry.load_skill("test-skill")
        first_tools = registry.skills["test-skill"].tools
        registry.load_skill("test-skill")
        assert registry.skills["test-skill"].tools is first_tools

    def test_load_skill_unknown_name_raises(self, skill_dir: Path):
        registry = SkillRegistry(skill_dir)
        try:
            registry.load_skill("nonexistent")
            raise AssertionError("Expected KeyError")
        except KeyError:
            pass

    def test_load_multiple_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        registry.load_skill("skill-c")

        assert registry.skills["skill-a"].loaded
        assert len(registry.skills["skill-a"].tools) == 1
        assert not registry.skills["skill-b"].loaded
        assert registry.skills["skill-c"].loaded
        assert len(registry.skills["skill-c"].tools) == 1


class TestSkillRegistryAllTools:
    def test_all_tools_includes_loaded_only(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # No skills loaded yet
        assert registry.all_tools == []

        registry.load_skill("skill-a")
        assert len(registry.all_tools) == 1
        assert registry.all_tools[0].name == "alpha_tool"

        registry.load_skill("skill-c")
        assert len(registry.all_tools) == 2

    def test_tool_map_for_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        registry.load_skill("skill-c")

        tool_map = registry.tool_map_for_skills(["skill-a"])
        assert "alpha_tool" in tool_map
        assert "charlie_tool" not in tool_map


class TestSkillRegistryReload:
    def test_reload_loads_all_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        skills = registry.reload()
        assert len(skills) == 3
        for skill in skills:
            assert skill.loaded

    def test_reload_detects_new_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        assert len(registry.skills) == 3

        # Add a new skill
        new_skill = multi_skill_dir / "skill-d"
        new_skill.mkdir()
        (new_skill / "SKILL.md").write_text("# Skill Delta\n", encoding="utf-8")

        registry.reload()
        assert "skill-d" in registry.skills
        assert len(registry.skills) == 4

    def test_reload_detects_removed_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        assert "skill-a" in registry.skills

        # Remove skill-a
        import shutil
        shutil.rmtree(multi_skill_dir / "skill-a")

        registry.reload()
        assert "skill-a" not in registry.skills


class TestSkillRegistryWatching:
    def test_start_and_stop_watching(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.start_watching()
        assert registry.is_watching
        registry.stop_watching()
        assert not registry.is_watching

    def test_watcher_detects_new_skill(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        initial_count = len(registry.skills)

        registry.start_watching()
        try:
            # Add a new skill
            new_skill = multi_skill_dir / "skill-new"
            new_skill.mkdir()
            (new_skill / "SKILL.md").write_text("# New Skill\n", encoding="utf-8")

            # Wait for the watcher to pick up the change
            import time
            for _ in range(20):
                time.sleep(0.1)
                if len(registry.skills) > initial_count:
                    break

            assert "skill-new" in registry.skills
        finally:
            registry.stop_watching()


def test_builtin_audit_sop_skill_is_discoverable() -> None:
    skills_dir = Path("src/personal_assistant/skills")
    registry = SkillRegistry(skills_dir)

    assert "audit-sop" in registry.skills
    skill = registry.skills["audit-sop"]
    assert skill.name == "audit-sop"
    assert "audit" in skill.description.lower()


def test_audit_sop_openai_metadata_exists() -> None:
    metadata_path = Path("src/personal_assistant/skills/audit-sop/agents/openai.yaml")

    assert metadata_path.exists()
    text = metadata_path.read_text(encoding="utf-8")
    assert "display_name:" in text
    assert "short_description:" in text
    assert "default_prompt:" in text
