"""Tests for loader parsing of `scripts` and `triggers` frontmatter, and
building script tools on load_skill."""

from pathlib import Path
from textwrap import dedent

from langchain_core.tools import BaseTool

from personal_assistant.skills.loader import SkillRegistry


def _make_scripted_skill(tmp_path: Path) -> Path:
    """A skill with a scripts/ declaration but no skill.py."""
    skill_dir = tmp_path / "weather"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "lookup.py").write_text(
        dedent(
            """
            import json, sys
            city = sys.argv[1] if len(sys.argv) > 1 else "unknown"
            print(json.dumps({"city": city, "temp_c": 22}))
            """
        ).strip(),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        dedent(
            """
            ---
            name: weather
            description: Look up the weather for a city.
            triggers:
              - weather
              - 天气
            scripts:
              - name: lookup_weather
                description: Get current weather for a city.
                command: ["python", "scripts/lookup.py", "{city}"]
                params:
                  city:
                    type: string
                    description: City name.
                    required: true
            ---

            # Weather

            Use `lookup_weather` to get the weather.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


class TestScanParsesTriggersAndScripts:
    def test_scan_populates_triggers(self, tmp_path: Path):
        _make_scripted_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["weather"]
        assert skill.triggers == ["weather", "天气"]

    def test_scan_populates_script_decls(self, tmp_path: Path):
        _make_scripted_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["weather"]
        assert len(skill.script_decls) == 1
        decl = skill.script_decls[0]
        assert decl["name"] == "lookup_weather"
        assert decl["command"] == ["python", "scripts/lookup.py", "{city}"]
        assert decl["params"]["city"]["type"] == "string"

    def test_scan_does_not_build_tools(self, tmp_path: Path):
        _make_scripted_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["weather"]
        assert not skill.loaded
        assert skill.tools == []  # script tools built only on load_skill

    def test_skills_without_triggers_have_empty_list(self, tmp_path: Path):
        d = tmp_path / "plain"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: plain\ndescription: A plain skill.\n---\n# Plain\n", encoding="utf-8")
        registry = SkillRegistry(tmp_path)
        assert registry.skills["plain"].triggers == []
        assert registry.skills["plain"].script_decls == []


class TestLoadSkillBuildsScriptTools:
    def test_load_builds_script_tools_from_decls(self, tmp_path: Path):
        _make_scripted_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        registry.load_skill("weather")
        skill = registry.skills["weather"]
        assert skill.loaded
        assert len(skill.tools) == 1
        tool = skill.tools[0]
        assert isinstance(tool, BaseTool)
        assert tool.name == "lookup_weather"

    def test_built_script_tool_runs_the_script(self, tmp_path: Path):
        _make_scripted_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        registry.load_skill("weather")
        tool = registry.skills["weather"].tools[0]
        result = tool.invoke({"city": "Beijing"})
        assert '"city": "Beijing"' in result
        assert '"temp_c": 22' in result

    def test_script_tools_exposed_via_tool_map(self, tmp_path: Path):
        _make_scripted_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        registry.load_skill("weather")
        tool_map = registry.tool_map_for_skills(["weather"])
        assert "lookup_weather" in tool_map

    def test_skill_with_scripts_and_skill_py_has_both(self, tmp_path: Path):
        """A skill may have both script tools and skill.py TOOLS."""
        skill_dir = tmp_path / "hybrid"
        scripts = skill_dir / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "s.py").write_text('import sys; print("script-out", sys.argv[1])\n', encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(
            dedent(
                """
                ---
                name: hybrid
                description: Hybrid skill.
                scripts:
                  - name: run_s
                    description: Run s script.
                    command: ["python", "scripts/s.py", "{x}"]
                    params:
                      x:
                        type: string
                        description: x.
                        required: true
                ---

                # Hybrid
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        (skill_dir / "skill.py").write_text(
            "from langchain_core.tools import tool\n\n"
            "@tool\n"
            "def native_tool(n: int) -> int:\n"
            '    """Native."""\n'
            "    return n + 1\n\n"
            "TOOLS = [native_tool]\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(tmp_path)
        registry.load_skill("hybrid")
        names = sorted(t.name for t in registry.skills["hybrid"].tools)
        assert names == ["native_tool", "run_s"]
