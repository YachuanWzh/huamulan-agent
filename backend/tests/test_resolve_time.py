"""Tests for the resolve-time skill: script logic + frontmatter + skill loading."""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from personal_assistant.skills.loader import SkillRegistry, _parse_frontmatter

# Dynamically import the script from scripts/resolve_date.py
_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "personal_assistant" / "skills" / "resolve-time" / "scripts" / "resolve_date.py"
)
_spec = importlib.util.spec_from_file_location("resolve_date_test", _SCRIPT)
_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_script)

FAKE_NOW = datetime(2026, 6, 28, 14, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Sunday


class TestScriptDateByOffset:
    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_today(self, _):
        r = _script.calc_date_by_offset(0)
        assert r["date"] == "2026-06-28"
        assert r["weekday"] == "Sunday"
        assert r["description"] == "today"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_tomorrow(self, _):
        r = _script.calc_date_by_offset(1)
        assert r["date"] == "2026-06-29"
        assert r["weekday"] == "Monday"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_day_after_tomorrow(self, _):
        r = _script.calc_date_by_offset(2)
        assert r["date"] == "2026-06-30"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_yesterday(self, _):
        r = _script.calc_date_by_offset(-1)
        assert r["date"] == "2026-06-27"
        assert r["weekday"] == "Saturday"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_ten_days_later(self, _):
        r = _script.calc_date_by_offset(10)
        assert r["date"] == "2026-07-08"


class TestScriptDateByWeekday:
    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_next_tuesday(self, _):
        # Sunday 6/28 → next week Mon 6/29, Tuesday = 6/30
        r = _script.calc_date_by_weekday("Tuesday", 1)
        assert r["date"] == "2026-06-30"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_next_friday(self, _):
        r = _script.calc_date_by_weekday("Friday", 1)
        assert r["date"] == "2026-07-03"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_week_after_next_wednesday(self, _):
        r = _script.calc_date_by_weekday("Wednesday", 2)
        assert r["date"] == "2026-07-08"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_this_monday(self, _):
        # Sunday 6/28 → this week starts Mon 6/22
        r = _script.calc_date_by_weekday("Monday", 0)
        assert r["date"] == "2026-06-22"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_chinese_xingqi(self, _):
        r = _script.calc_date_by_weekday("星期二", 1)
        assert r["date"] == "2026-06-30"

    @patch.object(_script, "now", return_value=FAKE_NOW)
    def test_chinese_zhou(self, _):
        r = _script.calc_date_by_weekday("周五", 1)
        assert r["date"] == "2026-07-03"

    def test_invalid_weekday(self):
        try:
            _script.calc_date_by_weekday("Funday", 1)
            raise AssertionError("Expected ValueError")
        except ValueError:
            pass


class TestScriptCLI:
    """Test the script can be invoked as a CLI subprocess."""

    def test_cli_offset(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "offset", "1"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "date" in data
        assert "weekday" in data

    def test_cli_weekday(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "weekday", "Monday", "0"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["weekday"] == "Monday"

    def test_cli_now(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "now"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "T" in result.stdout  # ISO format


class TestFrontmatter:
    def test_parses_name_and_description(self, tmp_path: Path):
        md = tmp_path / "SKILL.md"
        md.write_text(
            "---\nname: my-skill\ndescription: Does things\n---\n\n# Title\n",
            encoding="utf-8",
        )
        meta = _parse_frontmatter(md)
        assert meta["name"] == "my-skill"
        assert meta["description"] == "Does things"

    def test_no_frontmatter_returns_empty(self, tmp_path: Path):
        md = tmp_path / "SKILL.md"
        md.write_text("# Just a heading\n\nSome content.\n", encoding="utf-8")
        meta = _parse_frontmatter(md)
        assert meta == {}

    def test_resolve_time_frontmatter(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "resolve-time"
        )
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        assert meta["name"] == "resolve-time"
        assert "时间" in meta["description"] or "日期" in meta["description"]


class TestResolveTimeFrontmatter:
    def test_has_triggers(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "resolve-time"
        )
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        triggers = meta.get("triggers", [])
        assert "今天" in triggers
        assert "tomorrow" in triggers

    def test_declares_three_script_tools(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "resolve-time"
        )
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        names = {s["name"] for s in meta["scripts"]}
        assert names == {"resolve_current_time", "resolve_date_by_offset", "resolve_date_by_weekday"}

    def test_each_script_decl_points_at_resolve_date_py(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "resolve-time"
        )
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        for decl in meta["scripts"]:
            assert "scripts/resolve_date.py" in " ".join(decl["command"])


class TestSkillLoadingWithFrontmatter:
    def test_registry_reads_frontmatter_description(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        skill = registry.skills.get("resolve-time")
        assert skill is not None
        assert skill.description
        assert "时间" in skill.description or "日期" in skill.description
        assert not skill.loaded

    def test_load_skill_loads_instructions(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        registry.load_skill("resolve-time")
        skill = registry.skills["resolve-time"]
        assert skill.loaded
        assert "resolve_date_by_offset" in skill.instructions
        assert "scripts/resolve_date.py" in skill.instructions

    def test_load_skill_builds_three_script_tools(self):
        """The scripts/ declarations become LangChain tools on load."""
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        registry.load_skill("resolve-time")
        skill = registry.skills["resolve-time"]
        names = sorted(t.name for t in skill.tools)
        assert names == [
            "resolve_current_time",
            "resolve_date_by_offset",
            "resolve_date_by_weekday",
        ]

    def test_tool_map_exposes_resolve_time_tools(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        registry.load_skill("resolve-time")
        tool_map = registry.tool_map_for_skills(["resolve-time"])
        assert "resolve_date_by_offset" in tool_map
        assert "resolve_date_by_weekday" in tool_map
        assert "resolve_current_time" in tool_map


class TestResolveTimeToolsRunScripts:
    """Each script tool, when called, runs the script and returns its output."""

    @classmethod
    def setup_class(cls):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        registry.load_skill("resolve-time")
        cls.registry = registry

    def test_resolve_date_by_offset_returns_json(self):
        tool = self.registry.tool_map_for_skills(["resolve-time"])["resolve_date_by_offset"]
        result = tool.invoke({"day_offset": 0, "timezone": "Asia/Shanghai"})
        data = json.loads(result)
        assert "date" in data
        assert "weekday" in data
        assert data["day_offset"] == 0

    def test_resolve_date_by_offset_negative(self):
        tool = self.registry.tool_map_for_skills(["resolve-time"])["resolve_date_by_offset"]
        result = tool.invoke({"day_offset": -1, "timezone": "Asia/Shanghai"})
        data = json.loads(result)
        assert data["day_offset"] == -1

    def test_resolve_date_by_weekday_returns_json(self):
        tool = self.registry.tool_map_for_skills(["resolve-time"])["resolve_date_by_weekday"]
        result = tool.invoke(
            {"weekday": "Monday", "week_offset": 0, "timezone": "Asia/Shanghai"}
        )
        data = json.loads(result)
        assert data["weekday"] == "Monday"
        assert "date" in data

    def test_resolve_current_time_returns_iso(self):
        tool = self.registry.tool_map_for_skills(["resolve-time"])["resolve_current_time"]
        result = tool.invoke({"timezone": "Asia/Shanghai"})
        assert "T" in result  # ISO-8601

    def test_offset_uses_default_timezone(self):
        tool = self.registry.tool_map_for_skills(["resolve-time"])["resolve_date_by_offset"]
        # timezone optional with default — omitting it should still work
        result = tool.invoke({"day_offset": 1})
        data = json.loads(result)
        assert data["day_offset"] == 1
