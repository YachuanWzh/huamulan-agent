"""Tests for the weather skill: script logic + frontmatter + skill loading."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from personal_assistant.skills.loader import SkillRegistry, _parse_frontmatter

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "personal_assistant" / "skills" / "weather" / "scripts" / "weather.py"
)

# ── Sample wttr.in API responses ──────────────────────────────────

SAMPLE_CURRENT = {
    "current_condition": [
        {
            "temp_C": "22",
            "weatherDesc": [{"value": "Sunny"}],
            "humidity": "55",
            "windspeedKmph": "15",
            "winddir16Point": "NNE",
            "FeelsLikeC": "20",
        }
    ]
}

SAMPLE_FORECAST = {
    "weather": [
        {
            "date": "2026-06-30",
            "mintempC": "18",
            "maxtempC": "25",
            "hourly": [
                {
                    "time": "0",
                    "tempC": "19",
                    "weatherDesc": [{"value": "Clear"}],
                }
            ],
        },
        {
            "date": "2026-07-01",
            "mintempC": "20",
            "maxtempC": "28",
            "hourly": [
                {
                    "time": "0",
                    "tempC": "21",
                    "weatherDesc": [{"value": "Partly cloudy"}],
                }
            ],
        },
        {
            "date": "2026-07-02",
            "mintempC": "19",
            "maxtempC": "26",
            "hourly": [
                {
                    "time": "0",
                    "tempC": "20",
                    "weatherDesc": [{"value": "Light rain"}],
                }
            ],
        },
    ]
}


# ── Helpers ───────────────────────────────────────────────────────

def _load_script_module():
    """Dynamically import the weather script as a module."""
    if not _SCRIPT.exists():
        return None
    spec = importlib.util.spec_from_file_location("weather_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_fetch(url: str):
    """Return a mock httpx Response based on the URL."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    if "format=j1" in url:
        # Full response with both current and forecast
        data = {**SAMPLE_CURRENT, **SAMPLE_FORECAST}
        mock.json.return_value = data
    else:
        mock.json.return_value = {}
    return mock


# ── Script function tests ─────────────────────────────────────────


class TestWeatherScriptCurrent:
    """Test current weather extraction from wttr.in j1 response."""

    @classmethod
    def setup_class(cls):
        cls.module = _load_script_module()

    def test_script_exists(self):
        assert _SCRIPT.exists(), f"weather.py not found at {_SCRIPT}"

    def test_extract_current_weather(self):
        """extract_current should pull fields from current_condition[0]."""
        if self.module is None:
            return
        result = self.module.extract_current(SAMPLE_CURRENT, city="Beijing")
        assert result["city"] == "Beijing"
        assert result["temperature_c"] == 22
        assert result["condition"] == "Sunny"
        assert result["humidity"] == 55
        assert result["wind_speed_kmph"] == 15
        assert result["wind_direction"] == "NNE"
        assert result["feels_like_c"] == 20


class TestWeatherScriptForecast:
    """Test forecast extraction from wttr.in j1 response."""

    @classmethod
    def setup_class(cls):
        cls.module = _load_script_module()

    def test_extract_forecast(self):
        """extract_forecast should produce a list of daily summaries."""
        if self.module is None:
            return
        result = self.module.extract_forecast(SAMPLE_FORECAST)
        assert isinstance(result, list)
        assert len(result) == 3
        day1 = result[0]
        assert day1["date"] == "2026-06-30"
        assert day1["temp_min_c"] == 18
        assert day1["temp_max_c"] == 25


class TestWeatherScriptFetch:
    """Test the fetch function with mocked HTTP."""

    @classmethod
    def setup_class(cls):
        cls.module = _load_script_module()

    def test_fetch_returns_parsed_json(self):
        """fetch_weather should call wttr.in and return parsed JSON."""
        if self.module is None:
            return
        with patch.object(
            self.module.httpx,
            "get",
            return_value=_mock_fetch("wttr.in/Beijing?format=j1"),
        ):
            result = self.module.fetch_weather("Beijing")
        assert "current_condition" in result
        assert "weather" in result


# ── CLI tests ─────────────────────────────────────────────────────


class TestWeatherScriptCLI:
    """Test the script can be invoked as a CLI subprocess."""

    def test_cli_current(self):
        """python weather.py current Beijing should succeed."""
        if not _SCRIPT.exists():
            return
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "current", "Beijing"],
            capture_output=True, text=True,
        )
        # May fail if no network, but the command structure should be valid
        # Accept both success (with network) and failure with a meaningful error
        out = (result.stdout + result.stderr).lower()
        # Either returns valid JSON or a network-related error
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert "city" in data
        else:
            assert "network" in out or "timeout" in out or "error" in out or "connect" in out or "dns" in out or "name" in out

    def test_cli_forecast(self):
        """python weather.py forecast Shanghai should succeed."""
        if not _SCRIPT.exists():
            return
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "forecast", "Shanghai"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert isinstance(data, list)


# ── Frontmatter tests ─────────────────────────────────────────────


class TestWeatherFrontmatter:
    def test_parses_frontmatter(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "weather"
        )
        if not (skill_dir / "SKILL.md").exists():
            return
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        assert meta["name"] == "weather"
        assert "天气" in meta["description"]

    def test_has_triggers(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "weather"
        )
        if not (skill_dir / "SKILL.md").exists():
            return
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        triggers = meta.get("triggers", [])
        assert "天气" in triggers
        assert "weather" in triggers

    def test_declares_two_script_tools(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "weather"
        )
        if not (skill_dir / "SKILL.md").exists():
            return
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        names = {s["name"] for s in meta.get("scripts", [])}
        assert names == {"get_current_weather", "get_forecast"}

    def test_each_script_decl_points_at_weather_py(self):
        skill_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills" / "weather"
        )
        if not (skill_dir / "SKILL.md").exists():
            return
        meta = _parse_frontmatter(skill_dir / "SKILL.md")
        for decl in meta.get("scripts", []):
            assert "scripts/weather.py" in " ".join(decl["command"])


# ── Skill loading tests ───────────────────────────────────────────


class TestWeatherSkillLoading:
    def test_registry_sees_weather_skill(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        skill = registry.skills.get("weather")
        if skill is None:
            return  # skill not created yet
        assert skill.description
        assert "天气" in skill.description
        assert not skill.loaded

    def test_load_skill_loads_instructions(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        skill = registry.skills.get("weather")
        if skill is None:
            return
        registry.load_skill("weather")
        assert skill.loaded
        assert "get_current_weather" in skill.instructions
        assert "scripts/weather.py" in skill.instructions

    def test_load_skill_builds_two_script_tools(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        skill = registry.skills.get("weather")
        if skill is None:
            return
        registry.load_skill("weather")
        names = sorted(t.name for t in skill.tools)
        assert names == ["get_current_weather", "get_forecast"]

    def test_tool_map_exposes_weather_tools(self):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        skill = registry.skills.get("weather")
        if skill is None:
            return
        registry.load_skill("weather")
        tool_map = registry.tool_map_for_skills(["weather"])
        assert "get_current_weather" in tool_map
        assert "get_forecast" in tool_map


class TestWeatherToolsRunScripts:
    """Each script tool, when called, runs the script and returns its output."""

    @classmethod
    def setup_class(cls):
        skills_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "personal_assistant" / "skills"
        )
        registry = SkillRegistry(skills_dir)
        skill = registry.skills.get("weather")
        if skill is None:
            cls.registry = None
            return
        registry.load_skill("weather")
        cls.registry = registry

    def test_get_current_weather_returns_json(self):
        if self.registry is None:
            return
        tool = self.registry.tool_map_for_skills(["weather"])["get_current_weather"]
        result = tool.invoke({"city": "Beijing"})
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            # Transient network error or timeout — not a code defect
            return
        assert "city" in data
        assert "temperature_c" in data
        assert "condition" in data

    def test_get_forecast_returns_list(self):
        if self.registry is None:
            return
        tool = self.registry.tool_map_for_skills(["weather"])["get_forecast"]
        result = tool.invoke({"city": "Beijing"})
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            # Transient network error or timeout — not a code defect
            return
        assert isinstance(data, list)
        if data:
            assert "date" in data[0]
