"""Tests for code-review fixes: command validation, empty placeholder, hot-plug
reload, word-boundary token routing, and interpreter resolution."""

import sys
from pathlib import Path
from textwrap import dedent

import pytest

from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.skills.script_tool import build_script_tool, _resolve_interpreter
from personal_assistant.agent.router import _keyword_route


# ── Important #3: command shape validation ────────────────────────


def _skill_with(tmp_path: Path, frontmatter: str) -> Path:
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return tmp_path


class TestCommandValidation:
    def test_string_command_is_skipped(self, tmp_path: Path):
        """A `command` that is a string (not a list) is dropped, not silently
        turned into a per-character argv."""
        _skill_with(
            tmp_path,
            dedent(
                """
                ---
                name: s
                description: A skill.
                scripts:
                  - name: bad
                    description: bad
                    command: python scripts/x.py
                    params: {}
                ---
                # s
                """
            ).strip()
            + "\n",
        )
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["s"]
        # malformed decl is filtered out at scan time
        assert skill.script_decls == []

    def test_missing_command_is_skipped(self, tmp_path: Path):
        _skill_with(
            tmp_path,
            dedent(
                """
                ---
                name: s
                description: A skill.
                scripts:
                  - name: nope
                    description: no command
                ---
                # s
                """
            ).strip()
            + "\n",
        )
        registry = SkillRegistry(tmp_path)
        assert registry.skills["s"].script_decls == []

    def test_valid_list_command_is_kept(self, tmp_path: Path):
        _skill_with(
            tmp_path,
            dedent(
                """
                ---
                name: s
                description: A skill.
                scripts:
                  - name: good
                    description: good
                    command: ["python", "scripts/x.py", "{x}"]
                    params:
                      x:
                        type: string
                        description: x
                        required: true
                ---
                # s
                """
            ).strip()
            + "\n",
        )
        registry = SkillRegistry(tmp_path)
        decls = registry.skills["s"].script_decls
        assert len(decls) == 1
        assert decls[0]["name"] == "good"


# ── Minor #4: empty placeholder `{}` ──────────────────────────────


class TestEmptyPlaceholder:
    def test_empty_placeholder_raises_at_build(self, tmp_path: Path):
        decl = {
            "name": "t",
            "description": "t",
            "command": ["python", "scripts/x.py", "{}"],
            "params": {},
        }
        with pytest.raises(ValueError, match="Empty placeholder"):
            build_script_tool(decl, tmp_path)

    def test_literal_brace_token_passed_through(self, tmp_path: Path):
        """`{a}{b}` is NOT a placeholder — passed through literally."""
        rendered = _resolve_interpreter(["echo", "{a}{b}"])
        assert rendered == ["echo", "{a}{b}"]


# ── Minor #7: interpreter resolution ──────────────────────────────


class TestInterpreterResolution:
    def test_python_token_replaced_with_sys_executable(self):
        rendered = _resolve_interpreter(["python", "scripts/x.py"])
        assert rendered[0] == sys.executable

    def test_python3_token_replaced(self):
        rendered = _resolve_interpreter(["python3", "scripts/x.py"])
        assert rendered[0] == sys.executable

    def test_non_python_command_left_untouched(self):
        rendered = _resolve_interpreter(["bash", "scripts/x.sh"])
        assert rendered == ["bash", "scripts/x.sh"]


# ── Important #2: hot-plug reload of changed SKILL.md ─────────────


class TestHotPlugReload:
    def test_edited_skill_md_resets_loaded_and_picks_up_new_meta(self, tmp_path: Path):
        d = tmp_path / "s"
        d.mkdir()
        md = d / "SKILL.md"
        md.write_text(
            dedent(
                """
                ---
                name: s
                description: First version.
                triggers:
                  - alpha
                ---
                # s
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(tmp_path)
        registry.load_skill("s")
        assert registry.skills["s"].loaded
        assert registry.skills["s"].triggers == ["alpha"]

        # Edit SKILL.md: change description + add a trigger
        md.write_text(
            dedent(
                """
                ---
                name: s
                description: Second version.
                triggers:
                  - alpha
                  - beta
                ---
                # s
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        registry.scan_metadata()
        skill = registry.skills["s"]
        # Loaded state reset so the next route reloads full content
        assert not skill.loaded
        # New meta picked up
        assert skill.description == "Second version."
        assert skill.triggers == ["alpha", "beta"]

    def test_unchanged_loaded_skill_preserved(self, tmp_path: Path):
        d = tmp_path / "s"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: s\ndescription: Same.\n---\n# s\n", encoding="utf-8"
        )
        registry = SkillRegistry(tmp_path)
        registry.load_skill("s")
        # rescan without changes keeps it loaded
        registry.scan_metadata()
        assert registry.skills["s"].loaded


# ── Minor #5: word-boundary token routing ─────────────────────────


class TestTokenBoundaryRouting:
    def test_substring_inside_word_does_not_match(self, tmp_path: Path):
        """`for` in the description must not match user text `information`
        (substring false positive avoided by word-boundary matching)."""
        d = tmp_path / "s"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: s\ndescription: for everything\n---\n# s\n", encoding="utf-8"
        )
        registry = SkillRegistry(tmp_path)
        # "information" contains "for" as a substring but not as a word
        assert _keyword_route(registry, "I need information please") == []

    def test_whole_word_stopword_does_not_route(self, tmp_path: Path):
        d = tmp_path / "s"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: s\ndescription: for everything\n---\n# s\n", encoding="utf-8"
        )
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "this is for me") == []
