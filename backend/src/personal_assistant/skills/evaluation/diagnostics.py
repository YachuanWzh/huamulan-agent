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
    checks = _build_checks(case, outcome, mode=mode)
    selected = _string_list(outcome.get("selected_skills"))
    # Calculate per-case routing P/R/F1
    precision = None
    recall = None
    f1 = None
    if case.expected_skills:
        expected_set = set(case.expected_skills)
        selected_set = set(selected)
        tp = len(expected_set & selected_set)
        fp = len(selected_set - expected_set)
        fn = len(expected_set - selected_set)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
        else:
            f1 = 0.0

    # 判定case状态：pass/warning/fail
    failed_checks = [c for c in checks if not c.passed]
    status = "pass"
    diagnosis = _diagnose(checks)
    if failed_checks:
        # 仅多选技能（无漏选、无其他阶段失败）→ warning（黄色提示，不算失败）
        has_recall_failure = any(
            c.stage == "routing" and c.name == "skill_selection_recall"
            for c in failed_checks
        )
        has_non_routing_failure = any(c.stage != "routing" for c in failed_checks)
        only_over_selected = not has_recall_failure and not has_non_routing_failure
        if only_over_selected:
            status = "warning"
            diagnosis.severity = "warning"
            # 提取多选择的技能名称
            precision_fail = next(
                (c for c in failed_checks if c.name == "skill_selection_precision"),
                None
            )
            extra = precision_fail.reason if precision_fail else "存在冗余技能选择"
            diagnosis.summary = f"⚠️ 路由存在冗余选择: {extra}"
            diagnosis.signals = [f"warning.routing.over_selection: {extra}"]
        else:
            status = "fail"
    log_summary = _summarize_logs(outcome.get("logs") or [])
    routing_trace = _routing_trace(outcome)

    return CaseEvaluationDetail(
        case_id=case.id,
        mode=mode,
        query=_case_query(case),
        turns=_case_turns(case),
        expected_skills=case.expected_skills,
        selected_skills=selected,
        expected_tool_calls=case.expected_tool_calls if mode == "e2e" else [],
        actual_tool_calls=_tool_calls(outcome) if mode == "e2e" else [],
        final_answer=str(outcome.get("final_answer") or "") if mode == "e2e" else "",
        checks=checks,
        diagnosis=diagnosis,
        status=status,
        skill_selection_precision=precision,
        skill_selection_recall=recall,
        skill_selection_f1=f1,
        judge=judge,
        log_summary=log_summary,
        suspected_node=_suspected_node(checks, judge),
        routing_trace=routing_trace,
        diagnostic_outputs=_diagnostic_outputs(
            case,
            outcome,
            checks,
            judge=judge,
            log_summary=log_summary,
            routing_trace=routing_trace,
        ),
    )


def _build_checks(
    case: GoldenSkillCase,
    outcome: dict[str, Any],
    *,
    mode: str,
) -> list[EvaluationCheck]:
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
    # Multi-agent intent checks
    intent_slots = outcome.get("intent_slots")
    if intent_slots is not None and isinstance(intent_slots, dict):
        actual_intent = intent_slots.get("intent", "general")
        if case.expected_intent is not None:
            intent_passed = actual_intent == case.expected_intent
            checks.append(
                EvaluationCheck(
                    name="intent_match",
                    stage="routing",
                    passed=intent_passed,
                    expected=case.expected_intent,
                    actual=actual_intent,
                    reason=(
                        ""
                        if intent_passed
                        else f"Expected intent '{case.expected_intent}', got '{actual_intent}'"
                    ),
                )
            )
        if case.expected_metrics:
            actual_metrics = set(
                m.lower() for m in intent_slots.get("metrics", [])
            )
            expected_metrics_set = set(m.lower() for m in case.expected_metrics)
            missing = sorted(expected_metrics_set - actual_metrics)
            metric_passed = not missing
            checks.append(
                EvaluationCheck(
                    name="metric_extraction",
                    stage="routing",
                    passed=metric_passed,
                    expected={"required": case.expected_metrics},
                    actual=(
                        {"missing": missing}
                        if not metric_passed
                        else {"matched": sorted(actual_metrics & expected_metrics_set)}
                    ),
                    reason=(
                        ""
                        if metric_passed
                        else f"Missing expected metrics: {', '.join(missing)}"
                    ),
                )
            )
        if case.expected_entities:
            actual_entities = set(
                e.lower() for e in intent_slots.get("entities", [])
            )
            expected_entities_set = set(e.lower() for e in case.expected_entities)
            missing = sorted(expected_entities_set - actual_entities)
            entity_passed = not missing
            checks.append(
                EvaluationCheck(
                    name="entity_extraction",
                    stage="routing",
                    passed=entity_passed,
                    expected={"required": case.expected_entities},
                    actual=(
                        {"missing": missing}
                        if not entity_passed
                        else {"matched": sorted(actual_entities & expected_entities_set)}
                    ),
                    reason=(
                        ""
                        if entity_passed
                        else f"Missing expected entities: {', '.join(missing)}"
                    ),
                )
            )
    if mode != "e2e":
        return checks
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


def _suspected_node(checks: list[EvaluationCheck], judge: JudgeEvaluation | None) -> str | None:
    if judge and judge.failure_stage:
        judge_stage = judge.failure_stage.lower()
        if "prompt" in judge_stage or "reason" in judge_stage or "answer" in judge_stage:
            return "prompt"
        if "skill" in judge_stage or "routing" in judge_stage:
            return "skill"
        if "tool" in judge_stage:
            return "tool"
        if "safety" in judge_stage or "guard" in judge_stage:
            return "prompt_guard"

    failed = [check for check in checks if not check.passed]
    if not failed:
        return None
    priority = {"safety": 0, "routing": 1, "tool": 2, "answer": 3, "hallucination": 4}
    first = sorted(failed, key=lambda item: priority.get(item.stage, 99))[0]
    return {
        "safety": "prompt_guard",
        "routing": "skill",
        "tool": "tool",
        "answer": "prompt",
        "hallucination": "prompt",
    }.get(first.stage, first.stage)


def _diagnostic_outputs(
    case: GoldenSkillCase,
    outcome: dict[str, Any],
    checks: list[EvaluationCheck],
    *,
    judge: JudgeEvaluation | None,
    log_summary: list[dict[str, Any]],
    routing_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    final_answer = str(outcome.get("final_answer") or "")
    failed_checks = [check.model_dump(mode="json") for check in checks if not check.passed]
    outputs: dict[str, Any] = {
        "expected": {
            "skills": case.expected_skills,
            "tool_calls": [item.model_dump(mode="json") for item in case.expected_tool_calls],
            "answer_contains": case.expected_answer_contains,
            "forbidden_answer_contains": case.forbidden_answer_contains,
            "forbidden_tools": case.forbidden_tools,
        },
        "actual": {
            "skills": _string_list(outcome.get("selected_skills")),
            "tool_calls": _tool_calls(outcome),
        },
        "failed_checks": failed_checks,
        "final_answer": final_answer,
        "logs": log_summary,
        "routing_trace": routing_trace,
    }
    if case.expected_answer_contains:
        outputs["missing_answer_fragments"] = [
            fragment for fragment in case.expected_answer_contains if fragment not in final_answer
        ]
    if case.forbidden_answer_contains:
        outputs["forbidden_answer_fragments"] = [
            fragment for fragment in case.forbidden_answer_contains if fragment in final_answer
        ]
    if judge:
        outputs["judge"] = judge.model_dump(mode="json")
    return outputs


def _routing_trace(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    trace = outcome.get("routing_trace") or []
    return [item for item in trace if isinstance(item, dict)] if isinstance(trace, list) else []


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
