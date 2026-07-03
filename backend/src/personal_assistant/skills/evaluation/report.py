from collections.abc import Sequence
from typing import Any

from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.models import (
    GoldenSkillCase,
    RuntimeSkillMetrics,
    SkillEvaluationReport,
    SkillEvaluationResult,
)
from personal_assistant.skills.evaluation.offline import evaluate_routing_cases
from personal_assistant.skills.evaluation.runtime import evaluate_runtime_logs
from personal_assistant.skills.evaluation.static import evaluate_static_skill


async def evaluate_skill_registry(
    registry: SkillRegistry,
    *,
    cases: list[GoldenSkillCase] | None = None,
    runtime_logs: Sequence[Any] | None = None,
    **router_kwargs: Any,
) -> SkillEvaluationReport:
    routing = (
        await evaluate_routing_cases(registry, cases, **router_kwargs)
        if cases is not None
        else None
    )
    runtime_by_skill = (
        evaluate_runtime_logs(registry, runtime_logs)
        if runtime_logs is not None
        else {}
    )
    results = []
    for skill in registry.skills.values():
        static_metrics = evaluate_static_skill(skill)
        runtime_metrics = runtime_by_skill.get(skill.name)
        components = _score_components(routing, static_metrics, runtime_metrics)
        results.append(
            SkillEvaluationResult(
                skill_name=skill.name,
                overall_score=_weighted_score(components),
                static=static_metrics,
                runtime=runtime_metrics,
                score_components=components,
            )
        )
    return SkillEvaluationReport(skills=results, routing=routing)


def render_markdown_report(report: SkillEvaluationReport) -> str:
    lines = ["# Skill Evaluation Report", ""]
    if report.routing is not None:
        lines.extend(
            [
                "## Routing",
                f"- Total Cases: {report.routing.total_cases}",
                f"- Selection Accuracy: {_fmt_rate(report.routing.selection_accuracy)}",
                f"- False Positive Rate: {_fmt_rate(report.routing.false_positive_rate)}",
                "",
            ]
        )
    if report.safety is not None:
        lines.extend(
            [
                "## Safety",
                f"- Total Cases: {report.safety.total_cases}",
                f"- Attack Block Rate: {_fmt_rate(report.safety.attack_block_rate)}",
                f"- Unsafe Tool Call Rate: {_fmt_rate(report.safety.unsafe_tool_call_rate)}",
                f"- Secret Leak Rate: {_fmt_rate(report.safety.secret_leak_rate)}",
                f"- Security Event Precision: {_fmt_rate(report.safety.security_event_precision)}",
                "",
            ]
        )
    if report.tools is not None:
        lines.extend(
            [
                "## Tool Calls",
                f"- Total Cases: {report.tools.total_cases}",
                f"- Tool Selection Accuracy: {_fmt_rate(report.tools.tool_selection_accuracy)}",
                f"- Tool Call Precision: {_fmt_rate(report.tools.tool_call_precision)}",
                f"- Tool Call Recall: {_fmt_rate(report.tools.tool_call_recall)}",
                f"- Tool Call F1: {_fmt_rate(report.tools.tool_call_f1)}",
                f"- Argument Fidelity: {_fmt_rate(report.tools.argument_fidelity)}",
                f"- Argument Precision: {_fmt_rate(report.tools.argument_precision)}",
                f"- Argument Recall: {_fmt_rate(report.tools.argument_recall)}",
                f"- Argument F1: {_fmt_rate(report.tools.argument_f1)}",
                (
                    "- Forbidden Tool Violation Rate: "
                    f"{_fmt_rate(report.tools.forbidden_tool_violation_rate)}"
                ),
                "",
            ]
        )
    if report.answers is not None:
        lines.extend(
            [
                "## Answers",
                f"- Total Cases: {report.answers.total_cases}",
                f"- Answer Contains Rate: {_fmt_rate(report.answers.answer_contains_rate)}",
                (
                    "- Forbidden Answer Violation Rate: "
                    f"{_fmt_rate(report.answers.forbidden_answer_violation_rate)}"
                ),
                "",
            ]
        )
    if report.hallucinations is not None:
        lines.extend(
            [
                "## Hallucinations",
                f"- Total Cases: {report.hallucinations.total_cases}",
                (
                    "- Answer Hallucination Rate: "
                    f"{_fmt_rate(report.hallucinations.answer_hallucination_rate)}"
                ),
                (
                    "- Repeated Tool Call Rate: "
                    f"{_fmt_rate(report.hallucinations.repeated_tool_call_rate)}"
                ),
                (
                    "- Tool Argument Hallucination Rate: "
                    f"{_fmt_rate(report.hallucinations.tool_argument_hallucination_rate)}"
                ),
                (
                    "- Tool Evidence Usage Rate: "
                    f"{_fmt_rate(report.hallucinations.tool_evidence_usage_rate)}"
                ),
                (
                    "- Unsupported Answer Rate: "
                    f"{_fmt_rate(report.hallucinations.unsupported_answer_rate)}"
                ),
                "",
            ]
        )
    lines.extend(["## Skills", ""])
    for skill in sorted(report.skills, key=lambda item: item.skill_name):
        runtime = skill.runtime
        lines.extend(
            [
                f"### {skill.skill_name}",
                f"- Overall Score: {skill.overall_score:.3f}",
                f"- Description Tokens: {skill.static.description_tokens}",
                f"- Max Complexity: {skill.static.max_cyclomatic_complexity}",
                f"- Python Lines: {skill.static.python_lines}",
            ]
        )
        if runtime is not None:
            lines.extend(
                [
                    f"- Tool Calls: {runtime.tool_calls}",
                    f"- Execution Success Rate: {_fmt_rate(runtime.execution_success_rate)}",
                    f"- Retry Ratio: {_fmt_rate(runtime.retry_ratio)}",
                    f"- P95 Latency: {_fmt_value(runtime.p95_latency_ms)} ms",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _score_components(routing, static, runtime: RuntimeSkillMetrics | None) -> dict[str, float]:
    components: dict[str, float] = {}
    if routing is not None:
        accuracy = routing.skill_selection_f1
        if accuracy is None:
            accuracy = routing.selection_accuracy
        false_positive_rate = routing.false_positive_rate
        if accuracy is not None:
            fp_penalty = 1.0 - false_positive_rate if false_positive_rate is not None else 1.0
            components["routing"] = max(0.0, min(1.0, accuracy * fp_penalty))
    components["static"] = score_static_metrics(static)
    if runtime is not None:
        success = runtime.execution_success_rate
        retry_ratio = runtime.retry_ratio
        if success is not None:
            retry_score = 1.0 - retry_ratio if retry_ratio is not None else 1.0
            components["runtime"] = max(0.0, min(1.0, (success * 0.8) + (retry_score * 0.2)))
        components["usage"] = max(0.0, min(1.0, runtime.tool_calls / 10))
    return components


def score_static_metrics(static) -> float:
    description_score = 1.0 if static.description_tokens <= 200 else 200 / static.description_tokens
    complexity_score = (
        1.0
        if static.max_cyclomatic_complexity <= 10
        else 10 / static.max_cyclomatic_complexity
    )
    total_lines = static.skill_md_lines + static.python_lines
    line_score = 1.0 if total_lines <= 500 else 500 / total_lines
    return (description_score + complexity_score + line_score) / 3


def _weighted_score(components: dict[str, float]) -> float:
    weights = {
        "routing": 0.4,
        "runtime": 0.3,
        "static": 0.2,
        "usage": 0.1,
    }
    available_weight = sum(weights[name] for name in components)
    if available_weight == 0:
        return 0.0
    return sum(components[name] * weights[name] for name in components) / available_weight


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _fmt_value(value: object) -> str:
    return "n/a" if value is None else str(value)
