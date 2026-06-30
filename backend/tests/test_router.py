from pathlib import Path
from textwrap import dedent

import pytest

from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.agent.router import build_skill_router, build_system_prompt, _keyword_route
from personal_assistant.memory.long_term import LongTermMemoryStore


def _make_triggered_skill(tmp_path: Path) -> Path:
    """A skill whose trigger words do NOT appear in its name or description."""
    d = tmp_path / "cal"
    d.mkdir()
    (d / "SKILL.md").write_text(
        dedent(
            """
            ---
            name: cal
            description: Performs calendar arithmetic.
            triggers:
              - 今天
              - tomorrow
              - 星期几
            ---

            # Cal
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


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


class TestTriggerRouting:
    def test_matches_via_triggers_when_present(self, tmp_path: Path):
        """A trigger word not in name/description still routes the skill."""
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        # "今天" is a trigger but does not appear in name "cal" or description
        assert _keyword_route(registry, "今天是几号") == ["cal"]

    def test_matches_english_trigger(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "what about tomorrow") == ["cal"]

    def test_no_trigger_match_returns_empty(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "completely unrelated xyz") == []

    def test_skills_without_triggers_fall_back_to_token_match(self, multi_skill_dir: Path):
        """Skills without a triggers list still match on name/description tokens."""
        registry = SkillRegistry(multi_skill_dir)
        result = _keyword_route(registry, "I need help with alpha tasks")
        assert "skill-a" in result


class TestRouteSkillsNoFallback:
    """route_skills must not force-load all skills when nothing matches."""

    @pytest.mark.asyncio
    async def test_no_match_loads_no_skills(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        state = await router({"messages": [], "selected_skills": []})
        # Nothing matched "random unrelated text"
        assert state["selected_skills"] == []
        # Skill stays unloaded — only meta overview is in the system prompt
        assert not registry.skills["cal"].loaded

    @pytest.mark.asyncio
    async def test_match_loads_only_matched_skill(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        # add a second skill that won't match
        other = tmp_path / "other"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: other\ndescription: Other stuff.\n---\n# Other\n", encoding="utf-8"
        )
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        from langchain_core.messages import HumanMessage

        state = await router({"messages": [HumanMessage(content="今天怎么样")]})
        assert state["selected_skills"] == ["cal"]
        assert registry.skills["cal"].loaded
        assert not registry.skills["other"].loaded

    @pytest.mark.asyncio
    async def test_system_prompt_has_meta_overview_even_when_unmatched(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        state = await router({"messages": [], "selected_skills": []})
        system = state["messages"][0]
        # Meta overview lists the skill even though it wasn't loaded
        assert "cal" in system.content


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


class TestMemoryInjection:
    def test_system_prompt_includes_memory_when_store_provided(self, multi_skill_dir: Path, tmp_path: Path):
        registry = SkillRegistry(multi_skill_dir)
        memory_store = LongTermMemoryStore(tmp_path / ".memory")
        memory_store.ensure_files()
        (tmp_path / ".memory" / "USER.md").write_text(
            "# User\n\nCall me Yazuki.\n", encoding="utf-8"
        )

        msg = build_system_prompt(registry, selected=[], long_term_memory=memory_store)
        assert "Yazuki" in msg.content

    def test_system_prompt_works_without_memory_store(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[], long_term_memory=None)
        assert "personal assistant" in msg.content.lower()

    def test_memory_appears_before_skills_in_prompt(self, multi_skill_dir: Path, tmp_path: Path):
        registry = SkillRegistry(multi_skill_dir)
        memory_store = LongTermMemoryStore(tmp_path / ".memory")
        memory_store.add_memory(
            slug="test-mem",
            title="Test Memory",
            summary="A test memory",
            body="This is a test memory entry.",
        )

        msg = build_system_prompt(registry, selected=[], long_term_memory=memory_store)
        content = msg.content
        # Memory should appear near the beginning, before Available Skills
        mem_pos = content.index("Test Memory")
        skills_pos = content.index("Available Skills")
        assert mem_pos < skills_pos, "Memory should appear before skills in system prompt"
