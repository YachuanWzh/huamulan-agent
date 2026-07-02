from pathlib import Path
from datetime import UTC, datetime

import pytest

from personal_assistant.api.schemas import SkillEvaluationSnapshot
from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.skills.evaluation import (
    GoldenSkillCase,
    evaluate_skill_registry,
    evaluate_routing_cases,
    evaluate_runtime_logs,
    evaluate_static_skill,
    render_markdown_report,
)
from personal_assistant.skills.evaluation.__main__ import main as skill_eval_main
from personal_assistant.api.server import _run_skill_evaluation_and_persist, _skill_info


def _write_skill(skills_dir: Path, name: str, description: str, triggers: list[str]) -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir()
    trigger_lines = "\n".join(f"  - {trigger}" for trigger in triggers)
    (skill_dir / "SKILL.md").write_text(
        (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"triggers:\n"
            f"{trigger_lines}\n"
            f"---\n\n"
            f"# {name}\n"
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_routing_metrics_measure_selection_accuracy_and_false_positives(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "weather", "Weather forecast lookup.", ["weather", "rain"])
    _write_skill(tmp_path, "stock", "Stock market lookup.", ["stock", "price"])
    registry = SkillRegistry(tmp_path)
    cases = [
        GoldenSkillCase(
            id="weather-hit",
            query="will it rain tomorrow",
            expected_skills=["weather"],
        ),
        GoldenSkillCase(
            id="stock-miss",
            query="stock price for 000001",
            expected_skills=["weather"],
        ),
        GoldenSkillCase(
            id="negative-clean",
            query="write a short poem",
            expected_skills=[],
        ),
        GoldenSkillCase(
            id="negative-fp",
            query="weather the storm metaphor",
            expected_skills=[],
        ),
    ]

    metrics = await evaluate_routing_cases(registry, cases)

    assert metrics.total_cases == 4
    assert metrics.selection_accuracy == 0.5
    assert metrics.false_positive_rate == 0.5
    assert metrics.parameter_extraction_fidelity is None


@pytest.mark.asyncio
async def test_routing_metrics_return_none_when_denominator_is_empty(tmp_path: Path) -> None:
    _write_skill(tmp_path, "weather", "Weather forecast lookup.", ["weather"])
    registry = SkillRegistry(tmp_path)

    positive_only = await evaluate_routing_cases(
        registry,
        [GoldenSkillCase(id="weather-hit", query="weather today", expected_skills=["weather"])],
    )
    negative_only = await evaluate_routing_cases(
        registry,
        [GoldenSkillCase(id="clean", query="compose a haiku", expected_skills=[])],
    )

    assert positive_only.false_positive_rate is None
    assert negative_only.selection_accuracy is None


def test_static_skill_metrics_count_metadata_size_code_size_and_complexity(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "branchy", "Weather lookup for rainy cities.", ["weather"])
    skill_dir = tmp_path / "branchy"
    (skill_dir / "skill.py").write_text(
        (
            "from langchain_core.tools import tool\n\n"
            "@tool\n"
            "def inspect_weather(city: str) -> str:\n"
            '    """Inspect weather."""\n'
            "    if city:\n"
            "        for char in city:\n"
            "            if char.isdigit():\n"
            "                return 'bad'\n"
            "    return 'ok'\n\n"
            "TOOLS = [inspect_weather]\n"
        ),
        encoding="utf-8",
    )
    registry = SkillRegistry(tmp_path)
    registry.load_skill("branchy")

    metrics = evaluate_static_skill(registry.skills["branchy"])

    assert metrics.skill_name == "branchy"
    assert metrics.description_tokens == 5
    assert metrics.skill_md_lines == 9
    assert metrics.python_lines == 12
    assert metrics.max_cyclomatic_complexity == 4
    assert metrics.tool_count == 1


def test_runtime_metrics_aggregate_tool_success_retry_latency_and_tokens(
    skill_dir: Path,
) -> None:
    registry = SkillRegistry(skill_dir)
    registry.load_skill("test-skill")
    logs = [
        {
            "event_type": "tool",
            "status": "completed",
            "name": "do_thing",
            "duration_ms": 100,
            "token_usage": {"total_tokens": 10},
        },
        {
            "event_type": "tool",
            "status": "failed",
            "name": "do_thing",
            "duration_ms": 200,
            "token_usage": {"total_tokens": 20},
        },
        {
            "event_type": "tool",
            "status": "completed",
            "name": "do_thing",
            "duration_ms": 1000,
            "token_usage": {"total_tokens": 30},
        },
        {
            "event_type": "tool_retry",
            "status": "retrying",
            "name": "do_thing",
        },
        {
            "event_type": "tool",
            "status": "completed",
            "name": "unknown_tool",
            "duration_ms": 10,
        },
    ]

    metrics_by_skill = evaluate_runtime_logs(registry, logs)
    metrics = metrics_by_skill["test-skill"]

    assert metrics.tool_calls == 3
    assert metrics.successful_calls == 2
    assert metrics.failed_calls == 1
    assert metrics.retry_count == 1
    assert metrics.execution_success_rate == pytest.approx(2 / 3)
    assert metrics.retry_ratio == pytest.approx(1 / 3)
    assert metrics.p95_latency_ms == 1000
    assert metrics.p99_latency_ms == 1000
    assert metrics.token_consumption_per_call == pytest.approx(20.0)


def test_runtime_metrics_map_tools_even_when_skill_was_not_preloaded(skill_dir: Path) -> None:
    registry = SkillRegistry(skill_dir)

    metrics_by_skill = evaluate_runtime_logs(
        registry,
        [
            {
                "event_type": "tool",
                "status": "completed",
                "name": "do_thing",
                "duration_ms": 10,
            }
        ],
    )

    assert metrics_by_skill["test-skill"].tool_calls == 1


@pytest.mark.asyncio
async def test_skill_evaluation_report_scores_and_renders_markdown(tmp_path: Path) -> None:
    _write_skill(tmp_path, "weather", "Weather forecast lookup.", ["weather"])
    registry = SkillRegistry(tmp_path)
    cases = [
        GoldenSkillCase(id="hit", query="weather today", expected_skills=["weather"]),
        GoldenSkillCase(id="clean", query="compose a haiku", expected_skills=[]),
    ]

    report = await evaluate_skill_registry(registry, cases=cases)
    markdown = render_markdown_report(report)

    assert report.routing is not None
    assert report.routing.selection_accuracy == 1.0
    assert len(report.skills) == 1
    assert 0.0 <= report.skills[0].overall_score <= 1.0
    assert "# Skill Evaluation Report" in markdown
    assert "weather" in markdown
    assert "Selection Accuracy" in markdown


def test_skill_evaluation_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    _write_skill(tmp_path, "weather", "Weather forecast lookup.", ["weather"])
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        '{"id":"hit","query":"weather today","expected_skills":["weather"]}\n',
        encoding="utf-8",
    )
    output_json = tmp_path / "report.json"
    output_md = tmp_path / "report.md"

    exit_code = skill_eval_main(
        [
            "--skills-dir",
            str(tmp_path),
            "--golden",
            str(golden),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert exit_code == 0
    assert '"routing"' in output_json.read_text(encoding="utf-8")
    assert "# Skill Evaluation Report" in output_md.read_text(encoding="utf-8")


def test_skill_info_includes_static_evaluation_summary(skill_dir: Path) -> None:
    registry = SkillRegistry(skill_dir)
    registry.load_skill("test-skill")

    info = _skill_info(registry.skills["test-skill"])

    assert info.evaluation is not None
    assert 0.0 <= info.evaluation.overall_score <= 1.0
    assert info.evaluation.description_tokens > 0
    assert info.evaluation.python_lines > 0
    assert info.evaluation.max_cyclomatic_complexity >= 1
    assert info.evaluation.tool_count == 1


def test_skill_info_includes_latest_persisted_evaluation(skill_dir: Path) -> None:
    registry = SkillRegistry(skill_dir)
    snapshot = SkillEvaluationSnapshot(
        id=7,
        created_at=datetime(2026, 7, 2, tzinfo=UTC),
        skill_name="test-skill",
        overall_score=0.72,
        routing_score=0.8,
        static_score=0.6,
        source="golden:sample.jsonl",
        report={"skill_name": "test-skill"},
    )

    info = _skill_info(registry.skills["test-skill"], latest_evaluation=snapshot)

    assert info.latest_evaluation == snapshot


class _EvaluationMemory:
    def __init__(self) -> None:
        self.recorded = None
        self.source = None

    async def record_skill_evaluation_results(self, report, *, source):
        self.recorded = report
        self.source = source

    async def list_latest_skill_evaluations(self):
        return [
            SkillEvaluationSnapshot(
                id=1,
                created_at=datetime(2026, 7, 2, tzinfo=UTC),
                skill_name="weather",
                overall_score=0.95,
                source=source,
                report={},
            )
            for source in [self.source]
        ]


@pytest.mark.asyncio
async def test_run_skill_evaluation_from_golden_file_persists_latest_results(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "weather", "Weather forecast lookup.", ["weather"])
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        '{"id":"hit","query":"weather today","expected_skills":["weather"]}\n',
        encoding="utf-8",
    )
    registry = SkillRegistry(tmp_path)
    memory = _EvaluationMemory()

    response = await _run_skill_evaluation_and_persist(registry, memory, str(golden))

    assert memory.recorded is not None
    assert memory.recorded.routing.selection_accuracy == 1.0
    assert memory.source == f"golden:{golden}"
    assert response.source == f"golden:{golden}"
    assert response.results[0].skill_name == "weather"
