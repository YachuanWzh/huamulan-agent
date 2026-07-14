import json
from pathlib import Path

from personal_assistant.skills.evaluation.ops import EvaluationCaseResult, EvaluationRun
from personal_assistant.skills.evaluation.regression_cli import main


def _write_run(path: Path, run_id: str, passed: bool, *, status: str = "completed") -> None:
    run = EvaluationRun(
        run_id=run_id,
        mode="e2e",
        agent_mode="single",
        status=status,
        dataset_path="golden.jsonl",
        dataset_hash="a" * 64,
        total_cases=1,
        completed_cases=1,
        case_results=[
            EvaluationCaseResult(run_id=run_id, case_id="case-1", passed=passed)
        ],
    )
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")


def test_regression_cli_returns_one_and_writes_reports_for_failed_gate(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    output_json = tmp_path / "comparison.json"
    output_md = tmp_path / "comparison.md"
    _write_run(baseline, "base", True)
    _write_run(candidate, "candidate", False)

    exit_code = main(
        [
            "--baseline-json", str(baseline),
            "--candidate-json", str(candidate),
            "--output-json", str(output_json),
            "--output-md", str(output_md),
        ]
    )

    assert exit_code == 1
    assert json.loads(output_json.read_text(encoding="utf-8"))["status"] == "failed"
    assert "pass_to_fail" in output_md.read_text(encoding="utf-8")


def test_regression_cli_returns_two_for_incomplete_input(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write_run(baseline, "base", True)
    _write_run(candidate, "candidate", True, status="incomplete")

    assert main(["--baseline-json", str(baseline), "--candidate-json", str(candidate)]) == 2
