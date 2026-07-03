import pytest

from personal_assistant.skills.evaluation import AgentEvaluationCase
from personal_assistant.skills.evaluation.quality import (
    evaluate_hallucination_cases,
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
    assert metrics.tool_call_precision == pytest.approx(0.5)
    assert metrics.tool_call_recall == pytest.approx(1.0)
    assert metrics.tool_call_f1 == pytest.approx(2 / 3)
    assert metrics.unnecessary_tool_call_rate == pytest.approx(0.5)
    assert metrics.missing_tool_call_rate == pytest.approx(0.0)


def test_tool_metrics_score_precision_recall_f1_duplicates_and_arguments() -> None:
    cases = [
        AgentEvaluationCase(
            id="over-tool",
            query="lookup weather",
            expected_tool_calls=[
                {"tool": "weather_lookup", "args_contains": {"city": "Hangzhou"}}
            ],
        ),
        AgentEvaluationCase(
            id="missing-tool",
            query="remember preference",
            expected_tool_calls=[
                {"tool": "save_memory", "args_contains": {"content": "tabs"}}
            ],
        ),
    ]
    results = [
        {
            "case": cases[0],
            "tool_calls": [
                {"name": "weather_lookup", "args": {"city": "Shanghai"}},
                {"name": "weather_lookup", "args": {"city": "Shanghai"}},
                {"name": "resolve_time", "args": {"timezone": "UTC"}},
            ],
        },
        {"case": cases[1], "tool_calls": []},
    ]

    metrics = evaluate_tool_cases(cases, results)

    assert metrics.tool_call_precision == pytest.approx(1 / 3)
    assert metrics.tool_call_recall == pytest.approx(1 / 2)
    assert metrics.tool_call_f1 == pytest.approx(0.4)
    assert metrics.unnecessary_tool_call_rate == pytest.approx(1 / 3)
    assert metrics.missing_tool_call_rate == pytest.approx(1 / 2)
    assert metrics.duplicate_tool_call_rate == pytest.approx(1 / 3)
    assert metrics.argument_precision == pytest.approx(0.0)
    assert metrics.argument_recall == pytest.approx(0.0)
    assert metrics.argument_f1 == pytest.approx(0.0)
    assert metrics.argument_value_hallucination_rate == pytest.approx(1.0)


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


def test_hallucination_metrics_detect_answer_repeated_tools_and_bad_args() -> None:
    cases = [
        AgentEvaluationCase(
            id="answer-hallucination",
            query="show safe result only",
            forbidden_answer_contains=["fake citation"],
        ),
        AgentEvaluationCase(
            id="tool-repeat",
            query="check weather once",
            expected_tool_calls=[
                {"tool": "weather_lookup", "args_contains": {"city": "杭州"}}
            ],
        ),
        AgentEvaluationCase(
            id="arg-hallucination",
            query="check Hangzhou weather",
            expected_tool_calls=[
                {"tool": "weather_lookup", "args_contains": {"city": "杭州"}}
            ],
        ),
    ]
    results = [
        {
            "case": cases[0],
            "final_answer": "Here is a fake citation.",
            "tool_calls": [],
        },
        {
            "case": cases[1],
            "final_answer": "done",
            "tool_calls": [
                {"name": "weather_lookup", "args": {"city": "杭州"}},
                {"name": "weather_lookup", "args": {"city": "杭州"}},
            ],
        },
        {
            "case": cases[2],
            "final_answer": "上海天气",
            "tool_calls": [{"name": "weather_lookup", "args": {"city": "上海"}}],
        },
    ]

    metrics = evaluate_hallucination_cases(cases, results)

    assert metrics.total_cases == 3
    assert metrics.answer_hallucination_rate == pytest.approx(1 / 3)
    assert metrics.repeated_tool_call_rate == pytest.approx(1 / 3)
    assert metrics.tool_argument_hallucination_rate == pytest.approx(1 / 3)


def test_hallucination_metrics_detect_tool_evidence_usage_and_unsupported_answers() -> None:
    cases = [
        AgentEvaluationCase(
            id="grounded",
            query="weather",
            expected_tool_calls=[{"tool": "weather_lookup"}],
        ),
        AgentEvaluationCase(
            id="unsupported",
            query="weather",
            expected_tool_calls=[{"tool": "weather_lookup"}],
            forbidden_answer_contains=["storm"],
        ),
    ]
    results = [
        {
            "case": cases[0],
            "final_answer": "Hangzhou is sunny today.",
            "tool_calls": [{"name": "weather_lookup", "args": {}}],
            "logs": [
                {
                    "event_type": "tool",
                    "status": "completed",
                    "name": "weather_lookup",
                    "output": {"city": "Hangzhou", "condition": "sunny"},
                }
            ],
        },
        {
            "case": cases[1],
            "final_answer": "There will be a storm.",
            "tool_calls": [{"name": "weather_lookup", "args": {}}],
            "logs": [
                {
                    "event_type": "tool",
                    "status": "completed",
                    "name": "weather_lookup",
                    "output": {"city": "Hangzhou", "condition": "sunny"},
                }
            ],
        },
    ]

    metrics = evaluate_hallucination_cases(cases, results)

    assert metrics.tool_evidence_usage_rate == pytest.approx(0.5)
    assert metrics.unsupported_answer_rate == pytest.approx(0.5)
