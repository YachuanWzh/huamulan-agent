from typing import Any

from personal_assistant.agent.router import route_skill_names
from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.models import (
    GoldenSkillCase,
    MultiAgentRoutingMetrics,
    RoutingMetrics,
)


async def evaluate_routing_cases(
    registry: SkillRegistry,
    cases: list[GoldenSkillCase],
    **router_kwargs: Any,
) -> RoutingMetrics:
    positive_total = 0
    positive_exact_matches = 0
    negative_total = 0
    false_positives = 0
    true_positive_skills = 0
    selected_positive_skills = 0
    expected_positive_skills = 0
    over_selected_cases = 0
    under_selected_cases = 0

    for case in cases:
        selected = await route_skill_names(registry, _case_query(case), **router_kwargs)
        expected_set = set(case.expected_skills)
        selected_set = set(selected)
        if expected_set:
            positive_total += 1
            true_positive_skills += len(selected_set & expected_set)
            selected_positive_skills += len(selected_set)
            expected_positive_skills += len(expected_set)
            if selected_set - expected_set:
                over_selected_cases += 1
            if expected_set - selected_set:
                under_selected_cases += 1
            if selected_set == expected_set:
                positive_exact_matches += 1
        else:
            negative_total += 1
            if selected_set:
                false_positives += 1
                over_selected_cases += 1

    precision = (
        true_positive_skills / selected_positive_skills
        if selected_positive_skills
        else None
    )
    recall = (
        true_positive_skills / expected_positive_skills
        if expected_positive_skills
        else None
    )

    return RoutingMetrics(
        total_cases=len(cases),
        selection_accuracy=(
            positive_exact_matches / positive_total if positive_total else None
        ),
        false_positive_rate=false_positives / negative_total if negative_total else None,
        parameter_extraction_fidelity=None,
        skill_selection_precision=precision,
        skill_selection_recall=recall,
        skill_selection_f1=_f1(precision, recall),
        skill_over_selection_rate=(
            over_selected_cases / len(cases) if cases else None
        ),
        skill_under_selection_rate=(
            under_selected_cases / positive_total if positive_total else None
        ),
    )


def _case_query(case: GoldenSkillCase) -> str:
    query = getattr(case, "query", None)
    if isinstance(query, str) and query:
        return query
    turns = getattr(case, "turns", None)
    if isinstance(turns, list) and turns:
        return "\n".join(str(turn) for turn in turns)
    return ""


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate_multi_agent_intent_cases(
    cases: list[GoldenSkillCase],
) -> MultiAgentRoutingMetrics:
    """Evaluate multi-agent intent routing against expected intent/slots."""
    from personal_assistant.agent.multi_agent import rewrite_query_and_slots
    relevant = [c for c in cases if c.expected_intent is not None]
    if not relevant:
        return MultiAgentRoutingMetrics(total_cases=0)

    intent_correct = 0
    total_metric_recall = 0.0
    total_entity_recall = 0.0
    intent_tp = 0
    intent_fp = 0
    intent_fn = 0
    metric_cases = 0
    entity_cases = 0

    for case in relevant:
        payload = rewrite_query_and_slots(case.query)
        actual_intent = payload["slots"].get("intent", "general")
        expected_intent = case.expected_intent

        # Intent match
        if actual_intent == expected_intent:
            intent_correct += 1
            intent_tp += 1
        else:
            intent_fn += 1
            intent_fp += 1

        # Metric recall: did we catch all expected metrics?
        actual_metrics = set(
            m.lower() for m in payload["slots"].get("metrics", [])
        )
        expected_metrics_set = set(m.lower() for m in case.expected_metrics)
        if expected_metrics_set:
            metric_cases += 1
            matched = actual_metrics & expected_metrics_set
            metric_recall = len(matched) / len(expected_metrics_set)
            total_metric_recall += metric_recall

        # Entity recall
        actual_entities = set(
            e.lower() for e in payload["slots"].get("entities", [])
        )
        expected_entities_set = set(e.lower() for e in case.expected_entities)
        if expected_entities_set:
            entity_cases += 1
            matched = actual_entities & expected_entities_set
            entity_recall = len(matched) / len(expected_entities_set)
            total_entity_recall += entity_recall

    n = len(relevant)
    precision = (
        intent_tp / (intent_tp + intent_fp) if (intent_tp + intent_fp) > 0 else None
    )
    recall = (
        intent_tp / (intent_tp + intent_fn) if (intent_tp + intent_fn) > 0 else None
    )

    return MultiAgentRoutingMetrics(
        total_cases=n,
        intent_accuracy=intent_correct / n,
        intent_precision=precision,
        intent_recall=recall,
        intent_f1=_f1(precision, recall),
        metric_extraction_recall=(
            total_metric_recall / metric_cases if metric_cases > 0 else None
        ),
        entity_extraction_recall=(
            total_entity_recall / entity_cases if entity_cases > 0 else None
        ),
    )
