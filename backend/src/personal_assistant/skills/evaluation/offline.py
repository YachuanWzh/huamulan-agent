from typing import Any

from personal_assistant.agent.router import route_skill_names
from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.models import GoldenSkillCase, RoutingMetrics


async def evaluate_routing_cases(
    registry: SkillRegistry,
    cases: list[GoldenSkillCase],
    **router_kwargs: Any,
) -> RoutingMetrics:
    positive_total = 0
    positive_exact_matches = 0
    negative_total = 0
    false_positives = 0

    for case in cases:
        selected = await route_skill_names(registry, case.query, **router_kwargs)
        expected_set = set(case.expected_skills)
        selected_set = set(selected)
        if expected_set:
            positive_total += 1
            if selected_set == expected_set:
                positive_exact_matches += 1
        else:
            negative_total += 1
            if selected_set:
                false_positives += 1

    return RoutingMetrics(
        total_cases=len(cases),
        selection_accuracy=(
            positive_exact_matches / positive_total if positive_total else None
        ),
        false_positive_rate=false_positives / negative_total if negative_total else None,
        parameter_extraction_fidelity=None,
    )
