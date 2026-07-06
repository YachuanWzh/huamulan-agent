"""Tests for trigger_otel_alert.py CLI script."""
import json
import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "trigger_otel_alert.py"


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True, text=True, timeout=30,
    )


class TestBuildPayload:
    """Test the build_payload function via --dry-run CLI output."""

    def test_p0_dry_run_produces_valid_json(self):
        result = _run_script("--dry-run", "P0", "--service", "test-svc",
                             "--alert", "TestDown", "--summary", "test is down")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        assert payload["receiver"] == "langgraph-claw"
        assert payload["status"] == "firing"
        assert len(payload["alerts"]) == 1
        alert = payload["alerts"][0]
        assert alert["labels"]["severity"] == "critical"
        assert alert["labels"]["service_name"] == "test-svc"
        assert alert["labels"]["alertname"] == "TestDown"

    def test_p1_dry_run_severity_is_warning(self):
        result = _run_script("--dry-run", "P1", "--service", "svc",
                             "--alert", "HighLatency", "--summary", "slow")
        assert result.returncode == 0, result.stderr
        # Extract JSON from output
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        assert payload["alerts"][0]["labels"]["severity"] == "warning"

    def test_p2_dry_run_severity_is_info(self):
        result = _run_script("--dry-run", "P2", "--service", "svc",
                             "--alert", "TrendUp", "--summary", "rising")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        assert payload["alerts"][0]["labels"]["severity"] == "info"

    def test_p3_dry_run_severity_is_none(self):
        result = _run_script("--dry-run", "P3", "--service", "svc",
                             "--alert", "SloDrift", "--summary", "drift")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        assert payload["alerts"][0]["labels"]["severity"] == "none"

    def test_dry_run_includes_description_when_provided(self):
        result = _run_script("--dry-run", "P0", "--service", "svc",
                             "--alert", "Test", "--summary", "desc",
                             "--description", "detailed info here")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        assert payload["alerts"][0]["annotations"]["description"] == "detailed info here"


class TestPresets:
    """Test preset listing and preset triggering."""

    def test_presets_command_lists_all_levels(self):
        result = _run_script("presets")
        assert result.returncode == 0, result.stderr
        output = result.stdout
        assert "[P0]" in output
        assert "[P1]" in output
        assert "[P2]" in output
        assert "[P3]" in output
        assert "ServiceDown" in output
        assert "HighLatencyP95" in output

    def test_preset_trigger_dry_run(self):
        result = _run_script("--dry-run", "preset", "ServiceDown")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        alert = payload["alerts"][0]
        assert alert["labels"]["severity"] == "critical"
        assert alert["labels"]["service_name"] == "frontend"
        assert alert["labels"]["alertname"] == "ServiceDown"

    def test_preset_not_found_exits_with_error(self):
        result = _run_script("preset", "NonExistentAlert")
        assert result.returncode != 0
        assert "not found" in result.stderr or "not found" in result.stdout

    def test_preset_override_service(self):
        result = _run_script("--dry-run", "preset", "ServiceDown",
                             "--service", "my-frontend")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        json_start = next(i for i, line in enumerate(lines) if line.strip().startswith("{"))
        payload = json.loads("\n".join(lines[json_start:]))
        assert payload["alerts"][0]["labels"]["service_name"] == "my-frontend"


class TestHelp:
    """Test --help output."""

    def test_help_shows_subcommands(self):
        result = _run_script("--help")
        assert result.returncode == 0
        assert "P0" in result.stdout
        assert "P1" in result.stdout
        assert "presets" in result.stdout
        assert "batch" in result.stdout

    def test_p0_subcommand_shows_help(self):
        result = _run_script("P0", "--help")
        assert result.returncode == 0
        assert "--service" in result.stdout
        assert "--alert" in result.stdout
