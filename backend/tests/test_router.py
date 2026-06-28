from pathlib import Path

from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.agent.router import build_system_prompt, _keyword_route


class TestKeywordRoute:
    def test_matches_by_name_and_description(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # "alpha" appears in skill-a's name and description
        result = _keyword_route(registry, "I need help with alpha tasks")
        assert "skill-a" in result

    def test_no_match_returns_empty(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        result = _keyword_route(registry, "completely unrelated xyz query")
        assert result == []

    def test_matches_multiple_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # "skill" appears in all skills' descriptions (meta only)
        result = _keyword_route(registry, "I need a skill for this")
        assert len(result) == 3

    def test_does_not_match_on_full_instructions_only(self, multi_skill_dir: Path):
        """Keywords that appear only in full SKILL.md (not meta) should not match."""
        registry = SkillRegistry(multi_skill_dir)
        # "handles" appears in full SKILL.md ("Handles alpha tasks") but not in meta
        result = _keyword_route(registry, "please handle this request")
        assert result == []


class TestBuildSystemPrompt:
    def test_includes_meta_for_all_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[])
        content = msg.content
        # All skill meta should be present
        assert "skill-a" in content
        assert "Skill Alpha" in content
        assert "skill-b" in content
        assert "Skill Beta" in content

    def test_includes_full_instructions_for_selected(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        msg = build_system_prompt(registry, selected=["skill-a"])
        content = msg.content
        assert "Available tools:" in content  # from SKILL.md full content
        assert "## Skill: skill-a" in content

    def test_base_preamble_present(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[])
        assert "personal assistant" in msg.content.lower()

    def test_unselected_skills_meta_only(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        msg = build_system_prompt(registry, selected=["skill-a"])
        content = msg.content
        # skill-a should have full instructions section
        assert "## Skill: skill-a" in content
        # skill-b should only appear in meta header, not in detailed section
        assert "## Skill: skill-b" not in content
