import json
import subprocess
import sys
from pathlib import Path

import pytest

from personal_assistant.agent.router import _keyword_route
from personal_assistant.apm import FrontendRumEvent, build_observability_snapshot
from personal_assistant.api.schemas import ExecutionLog
from personal_assistant.skills.evaluation import GoldenSkillCase, evaluate_routing_cases
from personal_assistant.skills.loader import SkillRegistry


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "evaluation" / "golden" / "apm_realistic.jsonl"
FIXTURE_DIR = ROOT / "evaluation" / "fixtures" / "apm_realistic"
SKILLS_DIR = ROOT / "src" / "personal_assistant" / "skills"
PATROL_SCRIPT = SKILLS_DIR / "patrol" / "checks" / "health_check.py"


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_fixture(case: dict) -> dict:
    fixture = case.get("fixture")
    assert isinstance(fixture, str) and fixture
    path = ROOT / fixture
    assert path.is_file(), f"Missing fixture for {case['id']}: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_apm_realistic_golden_covers_all_prelaunch_flows() -> None:
    cases = _load_cases()

    assert len(cases) >= 9
    assert {case["category"] for case in cases} >= {
        "apm_troubleshooting",
        "apm_runbook",
        "apm_patrol",
        "apm_knowledge",
        "governance_audit",
    }
    assert all(case["id"].startswith("apm-real-") for case in cases)
    assert all(case.get("expected_skills") for case in cases)
    assert all(case.get("fixture") for case in cases)


def test_apm_realistic_fixtures_validate_and_build_observability_snapshots() -> None:
    for case in _load_cases():
        payload = _load_fixture(case)
        rum_events = [
            FrontendRumEvent.model_validate(event)
            for event in payload.get("rum_events", [])
        ]
        execution_logs = [
            ExecutionLog.model_validate(log)
            for log in payload.get("execution_logs", [])
        ]

        assert payload.get("incident_meta", {}).get("scenario") == case["id"]
        assert rum_events or execution_logs or payload.get("checks")
        if rum_events or execution_logs:
            snapshot = build_observability_snapshot(rum_events, execution_logs)
            assert snapshot.frontend.total_events == len(rum_events)
            assert snapshot.backend.total_events == len(execution_logs)
            assert snapshot.root_cause.category


def test_apm_realistic_patrol_fixtures_follow_check_tool_contract() -> None:
    patrol_cases = [
        case for case in _load_cases() if "patrol" in case.get("expected_skills", [])
    ]
    assert patrol_cases

    for case in patrol_cases:
        payload = _load_fixture(case)
        checks = payload.get("checks", [])
        assert checks, f"Patrol case {case['id']} should include checks"
        completed = subprocess.run(
            [sys.executable, str(PATROL_SCRIPT)],
            input=json.dumps({"checks": checks}, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=True,
        )
        result = json.loads(completed.stdout)
        assert len(result["findings"]) == len(checks)
        assert {finding["status"] for finding in result["findings"]} <= {"pass", "fail"}


@pytest.mark.asyncio
async def test_apm_realistic_golden_routes_to_expected_skills() -> None:
    registry = SkillRegistry(SKILLS_DIR)
    cases = [GoldenSkillCase.model_validate(case) for case in _load_cases()]

    metrics = await evaluate_routing_cases(registry, cases)

    assert metrics.selection_accuracy == 1.0
    assert metrics.skill_selection_precision == 1.0
    assert metrics.skill_selection_recall == 1.0
    for case in _load_cases():
        assert _keyword_route(registry, case["query"]) == case["expected_skills"]
