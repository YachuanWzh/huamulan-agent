from collections.abc import Sequence
from math import ceil
from typing import Any

from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.models import RuntimeSkillMetrics


def evaluate_runtime_logs(
    registry: SkillRegistry,
    logs: Sequence[Any],
) -> dict[str, RuntimeSkillMetrics]:
    tool_to_skill = _tool_to_skill_map(registry)
    buckets: dict[str, _RuntimeBucket] = {}

    for log in logs:
        event_type = _field(log, "event_type")
        tool_name = _tool_name(log)
        if not tool_name:
            continue
        skill_name = tool_to_skill.get(tool_name)
        if skill_name is None:
            continue
        bucket = buckets.setdefault(skill_name, _RuntimeBucket(skill_name=skill_name))
        if event_type == "tool":
            bucket.record_tool(log)
        elif event_type == "tool_retry":
            bucket.retry_count += 1

    return {name: bucket.to_metrics() for name, bucket in buckets.items()}


class _RuntimeBucket:
    def __init__(self, skill_name: str) -> None:
        self.skill_name = skill_name
        self.tool_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.retry_count = 0
        self.latencies: list[int] = []
        self.total_tokens = 0

    def record_tool(self, log: Any) -> None:
        self.tool_calls += 1
        status = _field(log, "status")
        if status == "completed":
            self.successful_calls += 1
        elif status == "failed":
            self.failed_calls += 1
        duration_ms = _field(log, "duration_ms")
        if isinstance(duration_ms, int):
            self.latencies.append(duration_ms)
        token_usage = _field(log, "token_usage") or {}
        if isinstance(token_usage, dict):
            total = token_usage.get("total_tokens")
            if isinstance(total, int):
                self.total_tokens += total

    def to_metrics(self) -> RuntimeSkillMetrics:
        return RuntimeSkillMetrics(
            skill_name=self.skill_name,
            tool_calls=self.tool_calls,
            successful_calls=self.successful_calls,
            failed_calls=self.failed_calls,
            retry_count=self.retry_count,
            execution_success_rate=(
                self.successful_calls / self.tool_calls if self.tool_calls else None
            ),
            retry_ratio=self.retry_count / self.tool_calls if self.tool_calls else None,
            p95_latency_ms=_percentile(self.latencies, 95),
            p99_latency_ms=_percentile(self.latencies, 99),
            token_consumption_per_call=(
                self.total_tokens / self.tool_calls
                if self.tool_calls and self.total_tokens
                else None
            ),
        )


def _tool_to_skill_map(registry: SkillRegistry) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for registry_key, skill in registry.skills.items():
        if not skill.loaded:
            registry.load_skill(registry_key)
        for tool_name in skill.tool_names:
            mapping[tool_name] = skill.name
        for decl in skill.script_decls:
            name = decl.get("name")
            if isinstance(name, str):
                mapping[name] = skill.name
    return mapping


def _tool_name(log: Any) -> str | None:
    name = _field(log, "name")
    if isinstance(name, str):
        return name
    metadata = _field(log, "metadata")
    if isinstance(metadata, dict):
        metadata_name = metadata.get("tool_name")
        if isinstance(metadata_name, str):
            return metadata_name
    return None


def _field(log: Any, name: str) -> Any:
    if isinstance(log, dict):
        return log.get(name)
    return getattr(log, name, None)


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = max(0, ceil((percentile / 100) * len(sorted_values)) - 1)
    return sorted_values[index]
