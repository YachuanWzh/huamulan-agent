import json
from typing import Any

from personal_assistant.skills.evaluation.models import GoldenSkillCase, JudgeEvaluation


async def evaluate_case_with_judge(
    case: GoldenSkillCase,
    outcome: dict[str, Any],
    *,
    judge_client: Any,
    model: str,
) -> JudgeEvaluation:
    try:
        response = await judge_client.ainvoke(_judge_messages(case, outcome))
        content = getattr(response, "content", response)
        return parse_judge_response(str(content), model=model)
    except Exception as exc:
        return JudgeEvaluation(
            model=model,
            available=False,
            passed=None,
            score=None,
            failure_stage="judge_unavailable",
            reason=str(exc),
            evidence=[],
            recommendation="Check judge model configuration and network connectivity.",
        )


def parse_judge_response(content: str, *, model: str) -> JudgeEvaluation:
    try:
        data = json.loads(_extract_json(content))
    except Exception as exc:
        return JudgeEvaluation(
            model=model,
            available=False,
            failure_stage="judge_unavailable",
            reason=f"invalid judge response: {exc}",
            recommendation="Check whether the judge prompt is returning stable JSON.",
        )
    evidence = data.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    return JudgeEvaluation(
        model=model,
        available=True,
        score=_float_or_none(data.get("score")),
        passed=data.get("passed") if isinstance(data.get("passed"), bool) else None,
        failure_stage=str(data.get("failure_stage") or ""),
        reason=str(data.get("reason") or ""),
        evidence=[str(item) for item in evidence],
        recommendation=str(data.get("recommendation") or ""),
    )


def _judge_messages(case: GoldenSkillCase, outcome: dict[str, Any]) -> list[dict[str, str]]:
    payload = {
        "case": case.model_dump(mode="json"),
        "selected_skills": outcome.get("selected_skills"),
        "tool_calls": outcome.get("tool_calls"),
        "final_answer": outcome.get("final_answer"),
    }
    return [
        {
            "role": "system",
            "content": (
                "You are an evaluation judge. Return strict JSON with keys "
                "score, passed, failure_stage, reason, evidence, recommendation. "
                "Use failure_stage values routing, tool, safety, answer, "
                "prompt_or_reasoning, or passed."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _extract_json(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
