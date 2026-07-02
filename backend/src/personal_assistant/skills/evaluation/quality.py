from collections.abc import Sequence
from typing import Any

from personal_assistant.skills.evaluation.models import (
    AnswerEvaluationMetrics,
    GoldenSkillCase,
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

    for case in tool_cases:
        result = result_by_id.get(case.id, {})
        tool_calls = _tool_calls(result)
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


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
