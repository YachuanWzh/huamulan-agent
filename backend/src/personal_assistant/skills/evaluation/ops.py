from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from personal_assistant.observability.traces import redact_payload


RunStatus = Literal["running", "completed", "incomplete", "failed"]


class EvaluationCaseResult(BaseModel):
    run_id: str
    case_id: str
    status: Literal["completed", "failed", "skipped"] = "completed"
    passed: bool = False
    safety_passed: bool | None = None
    forbidden_tools: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    total_tokens: int | None = None
    trace_id: str | None = None
    thread_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class EvaluationRun(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    mode: str
    agent_mode: str
    status: RunStatus = "running"
    source: str | None = None
    dataset_path: str
    dataset_hash: str
    git_sha: str | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    report: dict[str, Any] = Field(default_factory=dict)
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)


class RegressionThresholds(BaseModel):
    max_pass_rate_drop: float = 0.0
    latency_increase_ratio: float = 0.25
    token_increase_ratio: float = 0.25


class RegressionFinding(BaseModel):
    rule: str
    severity: Literal["error", "warning", "info"]
    case_id: str | None = None
    baseline: Any = None
    candidate: Any = None
    message: str


class EvaluationComparison(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    status: Literal["passed", "warning", "failed"]
    baseline_pass_rate: float
    candidate_pass_rate: float
    findings: list[RegressionFinding] = Field(default_factory=list)


class EvaluationCompareRequest(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    thresholds: RegressionThresholds = Field(default_factory=RegressionThresholds)


def create_run_snapshot(
    *,
    run_id: str,
    mode: str,
    agent_mode: str,
    dataset_path: str | Path,
    settings: Any,
    git_sha: str | None = None,
    total_cases: int = 0,
) -> EvaluationRun:
    path = Path(dataset_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if hasattr(settings, "model_dump"):
        config = settings.model_dump(mode="json")
    elif isinstance(settings, dict):
        config = dict(settings)
    else:
        config = {
            key: value
            for key, value in vars(settings).items()
            if not key.startswith("_")
        }
    return EvaluationRun(
        run_id=run_id,
        mode=mode,
        agent_mode=agent_mode,
        source=f"golden:{path}",
        dataset_path=str(path),
        dataset_hash=digest,
        git_sha=git_sha,
        config_snapshot=redact_payload(config),
        total_cases=total_cases,
    )


def case_result_from_detail(
    *,
    run_id: str,
    case_id: str,
    detail: Any,
    trace_id: str | None = None,
    thread_id: str | None = None,
) -> EvaluationCaseResult:
    payload = detail.model_dump(mode="json") if hasattr(detail, "model_dump") else dict(detail)
    passed = bool(
        payload.get(
            "passed",
            payload.get("status") == "pass" or payload.get("score", 0) >= 1,
        )
    )
    return EvaluationCaseResult(
        run_id=run_id,
        case_id=case_id,
        passed=passed,
        safety_passed=payload.get("safety_passed"),
        forbidden_tools=list(payload.get("forbidden_tools") or []),
        latency_ms=payload.get("latency_ms") or payload.get("duration_ms"),
        total_tokens=payload.get("total_tokens"),
        trace_id=trace_id,
        thread_id=thread_id,
        detail=redact_payload(payload),
    )


def compare_evaluation_runs(
    baseline: EvaluationRun,
    candidate: EvaluationRun,
    thresholds: RegressionThresholds | None = None,
) -> EvaluationComparison:
    thresholds = thresholds or RegressionThresholds()
    findings: list[RegressionFinding] = []
    baseline_cases = {item.case_id: item for item in baseline.case_results}
    candidate_cases = {item.case_id: item for item in candidate.case_results}

    for case_id, before in baseline_cases.items():
        after = candidate_cases.get(case_id)
        if after is None:
            findings.append(_finding("missing_case", "error", case_id, True, None, "候选运行缺少基线用例"))
            continue
        if before.passed and not after.passed:
            findings.append(_finding("pass_to_fail", "error", case_id, True, False, "用例从通过退化为失败"))
        if before.safety_passed is True and after.safety_passed is False:
            findings.append(_finding("safety_pass_to_fail", "error", case_id, True, False, "安全用例发生退化"))
        if after.forbidden_tools:
            findings.append(_finding("forbidden_tool", "error", case_id, before.forbidden_tools, after.forbidden_tools, "候选运行调用了禁用工具"))
        _append_ratio_warning(findings, "latency_regression", case_id, before.latency_ms, after.latency_ms, thresholds.latency_increase_ratio, "延迟")
        _append_ratio_warning(findings, "token_regression", case_id, before.total_tokens, after.total_tokens, thresholds.token_increase_ratio, "Token")
        if not before.passed and after.passed:
            findings.append(_finding("fail_to_pass", "info", case_id, False, True, "用例由失败改善为通过"))

    baseline_rate = _pass_rate(baseline_cases.values())
    candidate_rate = _pass_rate(candidate_cases.values())
    if baseline_rate - candidate_rate > thresholds.max_pass_rate_drop:
        findings.append(_finding("pass_rate_drop", "error", None, baseline_rate, candidate_rate, "整体通过率下降超过阈值"))
    status = "failed" if any(item.severity == "error" for item in findings) else (
        "warning" if any(item.severity == "warning" for item in findings) else "passed"
    )
    return EvaluationComparison(
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        status=status,
        baseline_pass_rate=baseline_rate,
        candidate_pass_rate=candidate_rate,
        findings=findings,
    )


def _finding(rule: str, severity: str, case_id: str | None, baseline: Any, candidate: Any, message: str) -> RegressionFinding:
    return RegressionFinding(rule=rule, severity=severity, case_id=case_id, baseline=baseline, candidate=candidate, message=message)


def _append_ratio_warning(findings, rule, case_id, baseline, candidate, threshold, label) -> None:
    if baseline is None or candidate is None or baseline <= 0:
        return
    ratio = (candidate - baseline) / baseline
    if ratio > threshold:
        findings.append(_finding(rule, "warning", case_id, baseline, candidate, f"{label}增加 {ratio:.1%}，超过阈值 {threshold:.1%}"))


def _pass_rate(cases) -> float:
    rows = list(cases)
    return sum(1 for item in rows if item.passed) / len(rows) if rows else 0.0
