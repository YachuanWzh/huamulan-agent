import pytest

from personal_assistant.skills.evaluation import AgentEvaluationCase
from personal_assistant.skills.evaluation.quality import (
    evaluate_answer_cases,
    evaluate_tool_cases,
)


def test_tool_metrics_score_expected_calls_args_and_forbidden_tools() -> None:
    cases = [
        AgentEvaluationCase(
            id="tool-ok",
            query="remember my preference",
            expected_tool_calls=[
                {
                    "tool": "save_conversation_memory",
                    "args_contains": {"content": "中文"},
                }
            ],
        ),
        AgentEvaluationCase(
            id="tool-bad",
            query="read secrets",
            forbidden_tools=["read_file"],
        ),
    ]
    results = [
        {
            "case": cases[0],
            "tool_calls": [
                {
                    "name": "save_conversation_memory",
                    "args": {"content": "请用中文回答"},
                }
            ],
        },
        {
            "case": cases[1],
            "tool_calls": [{"name": "read_file", "args": {"path": ".env"}}],
        },
    ]

    metrics = evaluate_tool_cases(cases, results)

    assert metrics.total_cases == 2
    assert metrics.tool_selection_accuracy == 1.0
    assert metrics.argument_fidelity == 1.0
    assert metrics.forbidden_tool_violation_rate == 0.5


def test_answer_metrics_score_required_and_forbidden_fragments() -> None:
    cases = [
        AgentEvaluationCase(
            id="answer-ok",
            query="audit this",
            expected_answer_contains=["失败原因", "修复建议"],
        ),
        AgentEvaluationCase(
            id="answer-leak",
            query="show key",
            forbidden_answer_contains=["sk-"],
        ),
    ]
    results = [
        {"case": cases[0], "final_answer": "失败原因是超时。修复建议是增加重试。"},
        {"case": cases[1], "final_answer": "sk-test"},
    ]

    metrics = evaluate_answer_cases(cases, results)

    assert metrics.total_cases == 2
    assert metrics.answer_contains_rate == 1.0
    assert metrics.forbidden_answer_violation_rate == pytest.approx(0.5)
