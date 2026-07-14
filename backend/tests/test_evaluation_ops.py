from pathlib import Path

from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills.evaluation.ops import (
    EvaluationCaseResult,
    EvaluationRun,
    RegressionThresholds,
    compare_evaluation_runs,
    create_run_snapshot,
)


class _Cursor:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection:
    def __init__(self, rows=()) -> None:
        self.rows = rows
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, connection) -> None:
        self.connection_value = connection

    def connection(self):
        return self.connection_value


def _run(run_id: str, cases: list[EvaluationCaseResult], *, status: str = "completed"):
    return EvaluationRun(
        run_id=run_id,
        mode="e2e",
        agent_mode="single",
        status=status,
        dataset_path="golden.jsonl",
        dataset_hash="a" * 64,
        total_cases=len(cases),
        completed_cases=len(cases),
        case_results=cases,
    )


def test_create_run_snapshot_hashes_dataset_and_redacts_config(tmp_path: Path) -> None:
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text('{"id":"case-1"}\n', encoding="utf-8")

    run = create_run_snapshot(
        run_id="eval-1",
        mode="e2e",
        agent_mode="single",
        dataset_path=dataset,
        settings={"model": "deepseek", "api_key": "sk-secret"},
        git_sha="abc123",
    )

    assert len(run.dataset_hash) == 64
    assert run.git_sha == "abc123"
    assert run.status == "running"
    assert run.config_snapshot["api_key"] == "[REDACTED]"


def test_compare_runs_blocks_pass_to_fail_and_safety_regression() -> None:
    baseline = _run(
        "base",
        [EvaluationCaseResult(run_id="base", case_id="safe-1", passed=True, safety_passed=True)],
    )
    candidate = _run(
        "candidate",
        [EvaluationCaseResult(run_id="candidate", case_id="safe-1", passed=False, safety_passed=False)],
    )

    comparison = compare_evaluation_runs(baseline, candidate)

    assert comparison.status == "failed"
    assert {item.rule for item in comparison.findings} >= {
        "pass_to_fail",
        "safety_pass_to_fail",
    }


def test_compare_runs_reports_missing_cases_and_latency_warning() -> None:
    baseline = _run(
        "base",
        [
            EvaluationCaseResult(run_id="base", case_id="one", passed=True, latency_ms=100),
            EvaluationCaseResult(run_id="base", case_id="two", passed=True),
        ],
    )
    candidate = _run(
        "candidate",
        [EvaluationCaseResult(run_id="candidate", case_id="one", passed=True, latency_ms=140)],
    )

    comparison = compare_evaluation_runs(
        baseline,
        candidate,
        RegressionThresholds(latency_increase_ratio=0.2),
    )

    assert comparison.status == "failed"
    assert any(item.rule == "missing_case" for item in comparison.findings)
    assert any(item.rule == "latency_regression" for item in comparison.findings)


async def test_evaluation_run_tables_and_create_are_additive() -> None:
    conn = _Connection()
    memory = PostgresMemory("postgresql://example")
    memory.pool = _Pool(conn)

    await memory._setup_evaluation_runs()
    await memory.create_evaluation_run(_run("eval-1", [], status="running"))

    all_sql = "\n".join(sql for sql, _ in conn.calls)
    assert "CREATE TABLE IF NOT EXISTS evaluation_runs" in all_sql
    assert "CREATE TABLE IF NOT EXISTS evaluation_case_results" in all_sql
    assert "UNIQUE (run_id, case_id)" in all_sql
    assert "INSERT INTO evaluation_runs" in all_sql


async def test_record_case_result_retains_full_detail() -> None:
    conn = _Connection()
    memory = PostgresMemory("postgresql://example")
    memory.pool = _Pool(conn)
    result = EvaluationCaseResult(
        run_id="eval-1",
        case_id="case-1",
        passed=True,
        trace_id="trace-1",
        detail={"nested": {"answer": "full"}},
    )

    await memory.record_evaluation_case_result(result)

    sql, params = conn.calls[0]
    assert "INSERT INTO evaluation_case_results" in sql
    assert "ON CONFLICT (run_id, case_id)" in sql
    assert params[-1].obj == {"nested": {"answer": "full"}}
