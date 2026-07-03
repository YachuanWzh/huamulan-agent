from collections.abc import Sequence
from typing import Any

from personal_assistant.skills.evaluation.models import (
    AnswerEvaluationMetrics,
    GoldenSkillCase,
    HallucinationEvaluationMetrics,
    ToolEvaluationMetrics,
)


def evaluate_tool_cases(
    cases: Sequence[GoldenSkillCase],
    case_results: Sequence[dict[str, Any]],
) -> ToolEvaluationMetrics:
    result_by_id = _results_by_case_id(case_results)
    tool_cases = [
        case
        for case in cases
        if case.expected_tool_calls or case.forbidden_tools
    ]
    total = len(tool_cases)
    if total == 0:
        return ToolEvaluationMetrics(total_cases=0)

    expected_cases = [case for case in tool_cases if case.expected_tool_calls]
    selected_ok = 0
    args_ok = 0
    forbidden_violations = 0
    expected_tool_count = 0
    actual_tool_count = 0
    matched_tool_count = 0
    unnecessary_tool_count = 0
    missing_tool_count = 0
    duplicate_tool_count = 0
    argument_expected_count = 0
    argument_actual_count = 0
    argument_matched_count = 0
    argument_schema_valid_count = 0
    argument_value_violations = 0
    argument_value_checked_count = 0

    for case in tool_cases:
        result = result_by_id.get(case.id, {})
        tool_calls = _tool_calls(result)
        expected_names = [expectation.tool for expectation in case.expected_tool_calls]
        actual_names = [
            name for name in (_tool_name(call) for call in tool_calls) if name is not None
        ]
        expected_tool_count += len(expected_names)
        actual_tool_count += len(actual_names)
        matched_tool_count += _bounded_match_count(expected_names, actual_names)
        unnecessary_tool_count += sum(name not in expected_names for name in actual_names)
        missing_tool_count += _missing_count(expected_names, actual_names)
        duplicate_tool_count += _duplicate_call_count(tool_calls)
        for expectation in case.expected_tool_calls:
            argument_expected_count += 1
            matching_calls = [
                call for call in tool_calls if _tool_name(call) == expectation.tool
            ]
            argument_actual_count += len(matching_calls)
            argument_schema_valid_count += sum(isinstance(_tool_args(call), dict) for call in matching_calls)
            if any(_dict_contains(_tool_args(call), expectation.args_contains) for call in matching_calls):
                argument_matched_count += 1
            elif matching_calls:
                argument_value_violations += 1
            if matching_calls:
                argument_value_checked_count += 1
        if any(_tool_name(call) in case.forbidden_tools for call in tool_calls):
            forbidden_violations += 1
        if not case.expected_tool_calls:
            continue
        if _expected_tools_present(case, tool_calls):
            selected_ok += 1
        if _expected_args_present(case, tool_calls):
            args_ok += 1

    return ToolEvaluationMetrics(
        total_cases=total,
        tool_selection_accuracy=(
            selected_ok / len(expected_cases) if expected_cases else None
        ),
        argument_fidelity=args_ok / len(expected_cases) if expected_cases else None,
        forbidden_tool_violation_rate=forbidden_violations / total,
        tool_call_precision=_safe_rate(matched_tool_count, actual_tool_count),
        tool_call_recall=_safe_rate(matched_tool_count, expected_tool_count),
        tool_call_f1=_f1(
            _safe_rate(matched_tool_count, actual_tool_count),
            _safe_rate(matched_tool_count, expected_tool_count),
        ),
        unnecessary_tool_call_rate=_safe_rate(unnecessary_tool_count, actual_tool_count),
        missing_tool_call_rate=_safe_rate(missing_tool_count, expected_tool_count),
        duplicate_tool_call_rate=_safe_rate(duplicate_tool_count, actual_tool_count),
        argument_precision=_safe_rate(argument_matched_count, argument_actual_count),
        argument_recall=_safe_rate(argument_matched_count, argument_expected_count),
        argument_f1=_f1(
            _safe_rate(argument_matched_count, argument_actual_count),
            _safe_rate(argument_matched_count, argument_expected_count),
        ),
        argument_schema_validity_rate=_safe_rate(argument_schema_valid_count, argument_actual_count),
        argument_value_hallucination_rate=_safe_rate(
            argument_value_violations,
            argument_value_checked_count,
        ),
    )


def evaluate_answer_cases(
    cases: Sequence[GoldenSkillCase],
    case_results: Sequence[dict[str, Any]],
) -> AnswerEvaluationMetrics:
    result_by_id = _results_by_case_id(case_results)
    answer_cases = [
        case
        for case in cases
        if case.expected_answer_contains or case.forbidden_answer_contains
    ]
    total = len(answer_cases)
    if total == 0:
        return AnswerEvaluationMetrics(total_cases=0)

    expected_cases = [case for case in answer_cases if case.expected_answer_contains]
    expected_hits = 0
    forbidden_violations = 0
    for case in answer_cases:
        result = result_by_id.get(case.id, {})
        final_answer = str(result.get("final_answer") or "")
        if case.expected_answer_contains and all(
            fragment in final_answer for fragment in case.expected_answer_contains
        ):
            expected_hits += 1
        if any(fragment and fragment in final_answer for fragment in case.forbidden_answer_contains):
            forbidden_violations += 1

    return AnswerEvaluationMetrics(
        total_cases=total,
        answer_contains_rate=(
            expected_hits / len(expected_cases) if expected_cases else None
        ),
        forbidden_answer_violation_rate=forbidden_violations / total,
    )


def evaluate_hallucination_cases(
    cases: Sequence[GoldenSkillCase],
    case_results: Sequence[dict[str, Any]],
) -> HallucinationEvaluationMetrics:
    result_by_id = _results_by_case_id(case_results)
    hallucination_cases = [
        case
        for case in cases
        if case.forbidden_answer_contains or case.expected_tool_calls
    ]
    total = len(hallucination_cases)
    if total == 0:
        return HallucinationEvaluationMetrics(total_cases=0)

    answer_violations = 0
    repeated_tool_violations = 0
    argument_violations = 0
    evidence_cases = 0
    evidence_used_cases = 0
    for case in hallucination_cases:
        result = result_by_id.get(case.id, {})
        final_answer = str(result.get("final_answer") or "")
        tool_calls = _tool_calls(result)
        evidence = _tool_output_evidence(result)
        if case.expected_tool_calls and evidence:
            evidence_cases += 1
            if any(item in final_answer for item in evidence):
                evidence_used_cases += 1
        if any(fragment and fragment in final_answer for fragment in case.forbidden_answer_contains):
            answer_violations += 1
        if _has_repeated_expected_tool_call(case, tool_calls):
            repeated_tool_violations += 1
        if case.expected_tool_calls and not _expected_args_present(case, tool_calls):
            argument_violations += 1

    return HallucinationEvaluationMetrics(
        total_cases=total,
        answer_hallucination_rate=answer_violations / total,
        repeated_tool_call_rate=repeated_tool_violations / total,
        tool_argument_hallucination_rate=argument_violations / total,
        tool_evidence_usage_rate=_safe_rate(evidence_used_cases, evidence_cases),
        unsupported_answer_rate=answer_violations / total,
    )


def _results_by_case_id(case_results: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _field(result.get("case"), "id"): result
        for result in case_results
        if result.get("case") is not None
    }


def _tool_calls(result: dict[str, Any]) -> list[dict[str, Any]]:
    calls = result.get("tool_calls") or []
    return [call for call in calls if isinstance(call, dict)]


def _expected_tools_present(case: GoldenSkillCase, tool_calls: list[dict[str, Any]]) -> bool:
    called = [_tool_name(call) for call in tool_calls]
    return all(expectation.tool in called for expectation in case.expected_tool_calls)


def _expected_args_present(case: GoldenSkillCase, tool_calls: list[dict[str, Any]]) -> bool:
    for expectation in case.expected_tool_calls:
        matching = [
            call
            for call in tool_calls
            if _tool_name(call) == expectation.tool
        ]
        if not matching:
            return False
        if expectation.args_contains and not any(
            _dict_contains(_tool_args(call), expectation.args_contains)
            for call in matching
        ):
            return False
    return True


def _tool_name(call: dict[str, Any]) -> str | None:
    name = call.get("name") or call.get("tool")
    return name if isinstance(name, str) else None


def _tool_args(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("args") or call.get("input") or {}
    return args if isinstance(args, dict) else {}


def _dict_contains(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict):
                return False
            if not _dict_contains(actual_value, expected_value):
                return False
        elif str(expected_value) not in str(actual_value):
            return False
    return True


def _has_repeated_expected_tool_call(
    case: GoldenSkillCase,
    tool_calls: list[dict[str, Any]],
) -> bool:
    for expectation in case.expected_tool_calls:
        seen: set[str] = set()
        for call in tool_calls:
            if _tool_name(call) != expectation.tool:
                continue
            args_key = str(sorted(_tool_args(call).items()))
            if args_key in seen:
                return True
            seen.add(args_key)
    return False


def _bounded_match_count(expected: list[str], actual: list[str]) -> int:
    remaining = actual.copy()
    matches = 0
    for name in expected:
        if name in remaining:
            matches += 1
            remaining.remove(name)
    return matches


def _missing_count(expected: list[str], actual: list[str]) -> int:
    remaining = actual.copy()
    missing = 0
    for name in expected:
        if name in remaining:
            remaining.remove(name)
        else:
            missing += 1
    return missing


def _duplicate_call_count(tool_calls: list[dict[str, Any]]) -> int:
    seen: set[tuple[str | None, str]] = set()
    duplicates = 0
    for call in tool_calls:
        key = (_tool_name(call), str(sorted(_tool_args(call).items())))
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _tool_output_evidence(result: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for log in result.get("logs") or []:
        if _field(log, "event_type") != "tool":
            continue
        output = _field(log, "output") or {}
        evidence.extend(_text_leaves(output))
    return [item for item in evidence if len(item) >= 3]


def _text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        leaves: list[str] = []
        for item in value.values():
            leaves.extend(_text_leaves(item))
        return leaves
    if isinstance(value, list):
        leaves: list[str] = []
        for item in value:
            leaves.extend(_text_leaves(item))
        return leaves
    return []


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
