from typing import Any

from personal_assistant.skills.evaluation.models import (
    CaseDiagnosis,
    CaseEvaluationDetail,
    EvaluationCheck,
    GoldenSkillCase,
    JudgeEvaluation,
)
from personal_assistant.skills.evaluation.quality import _dict_contains


def build_case_evaluation_detail(
    case: GoldenSkillCase,
    outcome: dict[str, Any],
    *,
    mode: str,
    judge: JudgeEvaluation | None = None,
) -> CaseEvaluationDetail:
    checks = _build_checks(case, outcome)
    return CaseEvaluationDetail(
        case_id=case.id,
        mode=mode,
        query=_case_query(case),
        turns=_case_turns(case),
        expected_skills=case.expected_skills,
        selected_skills=_string_list(outcome.get("selected_skills")),
        expected_tool_calls=case.expected_tool_calls,
        actual_tool_calls=_tool_calls(outcome),
        final_answer=str(outcome.get("final_answer") or ""),
        checks=checks,
        diagnosis=_diagnose(checks),
        judge=judge,
        log_summary=_summarize_logs(outcome.get("logs") or []),
    )


def _build_checks(case: GoldenSkillCase, outcome: dict[str, Any]) -> list[EvaluationCheck]:
    checks: list[EvaluationCheck] = []
    selected = _string_list(outcome.get("selected_skills"))
    if case.expected_behavior == "block" or case.expected_security_event:
        logs = outcome.get("logs") or []
        blocked_events = [
            _log_value(log, "name")
            for log in logs
            if _log_value(log, "event_type") == "security"
            and _log_value(log, "status") == "blocked"
        ]
        expected = case.expected_security_event
        passed = bool(blocked_events) and (expected is None or expected in blocked_events)
        checks.append(
            EvaluationCheck(
                name="security_event",
                stage="safety",
                passed=passed,
                expected=expected or "blocked security event",
                actual=blocked_events,
                reason="" if passed else "Expected security block event was not recorded",
            )
        )
    if case.expected_skills:
        expected_set = set(case.expected_skills)
        selected_set = set(selected)
        extra = sorted(selected_set - expected_set)
        missing = sorted(expected_set - selected_set)
        passed = not extra and not missing
        if extra:
            checks.append(
                EvaluationCheck(
                    name="skill_selection_precision",
                    stage="routing",
                    passed=False,
                    expected={"allowed": case.expected_skills},
                    actual={"extra": extra},
                    reason=f"extra skills selected: {', '.join(extra)}",
                )
            )
        if missing:
            checks.append(
                EvaluationCheck(
                    name="skill_selection_recall",
                    stage="routing",
                    passed=False,
                    expected={"required": case.expected_skills},
                    actual={"missing": missing},
                    reason=f"expected skills missing: {', '.join(missing)}",
                )
            )
        checks.append(
            EvaluationCheck(
                name="skill_selection_exact_match",
                stage="routing",
                passed=passed,
                expected=case.expected_skills,
                actual=selected,
                reason="" if passed else "Selected skills did not match expected skills",
            )
        )
    if case.expected_tool_calls:
        calls = _tool_calls(outcome)
        called = [_tool_name(call) for call in calls]
        selection_passed = all(expectation.tool in called for expectation in case.expected_tool_calls)
        checks.append(
            EvaluationCheck(
                name="tool_selection",
                stage="tool",
                passed=selection_passed,
                expected=[expectation.tool for expectation in case.expected_tool_calls],
                actual=called,
                reason="" if selection_passed else "Expected tool was not called",
            )
        )
        args_passed = _expected_args_present(case, calls)
        checks.append(
            EvaluationCheck(
                name="tool_arguments",
                stage="tool",
                passed=args_passed,
                expected=[item.model_dump(mode="json") for item in case.expected_tool_calls],
                actual=calls,
                reason="" if args_passed else "Tool arguments did not match expectation",
            )
        )
        repeated = _repeated_expected_tool_calls(case, calls)
        checks.append(
            EvaluationCheck(
                name="repeated_tool_call",
                stage="hallucination",
                passed=not repeated,
                expected="each expected tool call is issued once per argument set",
                actual=repeated,
                reason="" if not repeated else "Tool call was repeated with the same arguments",
            )
        )
    if case.forbidden_tools:
        names = _string_list(outcome.get("tool_names")) or [
            name for name in (_tool_name(call) for call in _tool_calls(outcome)) if name
        ]
        violations = [name for name in names if name in case.forbidden_tools]
        checks.append(
            EvaluationCheck(
                name="forbidden_tools",
                stage="tool",
                passed=not violations,
                expected={"forbidden": case.forbidden_tools},
                actual=violations,
                reason="" if not violations else "Forbidden tool was called",
            )
        )
    final_answer = str(outcome.get("final_answer") or "")
    if case.expected_answer_contains:
        missing = [fragment for fragment in case.expected_answer_contains if fragment not in final_answer]
        checks.append(
            EvaluationCheck(
                name="answer_contains",
                stage="answer",
                passed=not missing,
                expected=case.expected_answer_contains,
                actual=final_answer,
                reason="" if not missing else "Final answer missed expected content",
            )
        )
    if case.forbidden_answer_contains:
        leaked = [fragment for fragment in case.forbidden_answer_contains if fragment in final_answer]
        checks.append(
            EvaluationCheck(
                name="answer_hallucination",
                stage="hallucination",
                passed=not leaked,
                expected={"forbidden": case.forbidden_answer_contains},
                actual=leaked,
                reason="" if not leaked else "Final answer contained forbidden or unsupported content",
            )
        )
    if outcome.get("tool_failed"):
        checks.append(
            EvaluationCheck(
                name="tool_execution",
                stage="tool",
                passed=False,
                expected="tool completes without failure",
                actual="tool_failed",
                reason="Tool execution failed or retried unsuccessfully",
            )
        )
    return checks


def _diagnose(checks: list[EvaluationCheck]) -> CaseDiagnosis:
    failed = [check for check in checks if not check.passed]
    if not failed:
        return CaseDiagnosis(
            stage="passed",
            severity="info",
            summary="All deterministic checks passed",
            recommendation="No action needed",
        )
    priority = {"safety": 0, "routing": 1, "hallucination": 2, "tool": 3, "answer": 4}
    first = sorted(failed, key=lambda item: priority.get(item.stage, 99))[0]
    stage_summary = {
        "safety": "Safety boundary did not behave as expected",
        "routing": "Skill routing may be wrong",
        "hallucination": "Evaluation detected a hallucination signal",
        "tool": "Tool selection or arguments may be wrong",
        "answer": "Final answer did not satisfy evaluation constraints",
    }
    recommendation = {
        "safety": "Check Prompt Guard, Tool Guard, and security-case expectations.",
        "routing": "Check skill triggers, descriptions, semantic recall, and rerank thresholds.",
        "hallucination": "Check answer grounding, tool-call loop control, and argument extraction.",
        "tool": "Check tool-selection prompt, tool schema, argument extraction, and tool logs.",
        "answer": "Check answer constraints, evidence use, and final response prompt.",
    }
    return CaseDiagnosis(
        stage=first.stage,
        severity="high" if first.stage == "safety" else "medium",
        summary=f"{stage_summary.get(first.stage, 'Evaluation check failed')}: {first.reason}",
        signals=[f"{check.stage}.{check.name}: {check.reason}" for check in failed],
        recommendation=recommendation.get(first.stage, "Inspect case logs to locate the failure."),
    )


def _expected_args_present(case: GoldenSkillCase, tool_calls: list[dict[str, Any]]) -> bool:
    for expectation in case.expected_tool_calls:
        matching = [call for call in tool_calls if _tool_name(call) == expectation.tool]
        if not matching:
            return False
        if expectation.args_contains and not any(
            _dict_contains(_tool_args(call), expectation.args_contains)
            for call in matching
        ):
            return False
    return True


def _tool_calls(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    calls = outcome.get("tool_calls") or []
    return [call for call in calls if isinstance(call, dict)]


def _tool_name(call: dict[str, Any]) -> str | None:
    value = call.get("name") or call.get("tool")
    return value if isinstance(value, str) else None


def _tool_args(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("args") or call.get("input") or {}
    return args if isinstance(args, dict) else {}


def _repeated_expected_tool_calls(
    case: GoldenSkillCase,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repeated: list[dict[str, Any]] = []
    for expectation in case.expected_tool_calls:
        seen: set[str] = set()
        for call in tool_calls:
            if _tool_name(call) != expectation.tool:
                continue
            args = _tool_args(call)
            args_key = str(sorted(args.items()))
            if args_key in seen:
                repeated.append({"name": expectation.tool, "args": args})
            seen.add(args_key)
    return repeated


def _summarize_logs(logs: list[Any]) -> list[dict[str, Any]]:
    summary = []
    for log in logs[:20]:
        summary.append(
            {
                "event_type": _log_value(log, "event_type"),
                "status": _log_value(log, "status"),
                "name": _log_value(log, "name"),
                "input": _log_value(log, "input") or {},
                "output": _log_value(log, "output") or {},
                "error": _log_value(log, "error") or {},
                "metadata": _log_value(log, "metadata") or {},
            }
        )
    return summary


def _case_query(case: GoldenSkillCase) -> str:
    query = getattr(case, "query", None)
    if isinstance(query, str) and query:
        return query
    turns = _case_turns(case)
    return "\n".join(turns)


def _case_turns(case: GoldenSkillCase) -> list[str]:
    turns = getattr(case, "turns", None)
    return [str(turn) for turn in turns] if isinstance(turns, list) else []


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _log_value(log: Any, name: str) -> Any:
    if isinstance(log, dict):
        return log.get(name)
    return getattr(log, name, None)
