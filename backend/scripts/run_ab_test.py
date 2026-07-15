#!/usr/bin/env python3
"""Run A/B tests comparing two agent variants on a shared query set.

Usage:
    python scripts/run_ab_test.py \\
      --queries queries.jsonl \\
      --variant-a '{"name":"pro","model":"deepseek-v4-pro"}' \\
      --variant-b '{"name":"flash","model":"deepseek-v4-flash"}' \\
      --n-samples 3 \\
      --output-dir ab_results/

    # Prompt comparison
    python scripts/run_ab_test.py \\
      --queries queries.jsonl \\
      --variant-a '{"name":"control"}' \\
      --variant-b '{"name":"experiment","prompt_overrides":{"supervisor":"..."}}' \\
      --n-samples 2

The queries file should be JSONL with one query per line:
    {"query": "分析服务延迟异常"}
    {"query": "查询过去1小时的错误率"}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Allow running from backend/ without PYTHONPATH
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from personal_assistant.agent.harness import AgentHarness
from personal_assistant.agent.hook import AgentHookManager
from personal_assistant.config import get_settings
from personal_assistant.memory.postgres import PostgresMemory
from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.ab_test import (
    ABTestRunner,
    ABVariant,
    format_ab_report,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_queries(path: str) -> list[str]:
    """Load queries from a JSONL file. Each line must have a 'query' key."""
    filepath = Path(path)
    if not filepath.exists():
        logger.error("Queries file not found: %s", filepath)
        sys.exit(1)
    queries: list[str] = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            logger.warning("Skipping invalid JSON line: %s", exc)
            continue
        query = obj.get("query") or obj.get("text") or obj.get("message")
        if query:
            queries.append(str(query))
    if not queries:
        logger.error("No queries found in %s", filepath)
        sys.exit(1)
    return queries


async def _run_ab_test(
    queries: list[str],
    variant_a: ABVariant,
    variant_b: ABVariant,
    n_samples: int,
    output_dir: str | None,
    *,
    no_judge: bool = False,
) -> int:
    """Execute A/B test and write reports."""
    settings = get_settings()
    registry = SkillRegistry(settings.skills_dir)
    memory = PostgresMemory(settings.database_url)
    hook_manager = AgentHookManager()

    harness = AgentHarness(
        settings=settings,
        registry=registry,
        memory=memory,
        hook_manager=hook_manager,
    )

    # Build judge LLM if needed
    judge_llm = None
    if not no_judge:
        try:
            from personal_assistant.agent.llm import build_llm
            from personal_assistant.api.schemas import LLMConfig

            judge_llm = build_llm(
                settings,
                LLMConfig(
                    model=settings.evaluation_judge_model,
                    temperature=0.0,
                ),
            )
            logger.info("Judge model: %s", settings.evaluation_judge_model)
        except Exception as exc:
            logger.warning("Could not build judge LLM, skipping judge: %s", exc)

    runner = ABTestRunner(
        harness=harness,
        settings=settings,
        judge_llm=judge_llm,
    )

    logger.info(
        "Starting A/B test: %s vs %s (%d queries × %d samples)",
        variant_a.name,
        variant_b.name,
        len(queries),
        n_samples,
    )

    report = await runner.run(
        queries=queries,
        variant_a=variant_a,
        variant_b=variant_b,
        n_samples=n_samples,
        run_judge=judge_llm is not None,
    )

    # Format and output
    table = format_ab_report(report)
    print()
    print(table)

    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        report_json = out_path / f"ab_report_{report.run_id}.json"
        report_json.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("JSON report: %s", report_json)

        report_md = out_path / f"ab_report_{report.run_id}.md"
        report_md.write_text(
            _render_markdown_report(report),
            encoding="utf-8",
        )
        logger.info("Markdown report: %s", report_md)

    return 0


def _render_markdown_report(report) -> str:
    """Render AB test report as Markdown."""
    lines: list[str] = []
    lines.append(f"# A/B Test Report: {report.variant_a.name} vs {report.variant_b.name}")
    lines.append("")
    lines.append(f"- **Run ID**: `{report.run_id}`")
    lines.append(f"- **Queries**: {report.n_queries} × {report.n_samples} samples")
    lines.append(f"- **Variant A**: {report.variant_a.model or 'default'} ({report.variant_a.agent_mode})")
    lines.append(f"- **Variant B**: {report.variant_b.model or 'default'} ({report.variant_b.agent_mode})")
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | A | B | Δ% | p-value | Significance |")
    lines.append("|--------|---|---|----|---------|--------------|")
    for m in report.metrics:
        sig = _md_sig(m)
        p_str = f"`{_fmt_p_simple(m.p_value)}`" if m.p_value is not None else "N/A"
        lines.append(
            f"| {m.metric_name} | {m.value_a:.2f} | {m.value_b:.2f} | "
            f"{m.delta_pct:+.1f}% | {p_str} | {sig} |"
        )
    lines.append("")

    lines.append("## Conclusion")
    lines.append("")
    lines.append(report.conclusion)

    if report.per_query_summary:
        lines.append("")
        lines.append("## Per-Query Summary")
        lines.append("")
        lines.append("| Query | A Latency | B Latency | A Success | B Success | Judge Winner |")
        lines.append("|-------|----------|----------|-----------|-----------|-------------|")
        for pq in report.per_query_summary:
            judge_winner = pq.get("judge_winner") or "-"
            lines.append(
                f"| {pq['query'][:40]} | {pq['a_latency_mean']:.0f}ms | "
                f"{pq['b_latency_mean']:.0f}ms | {pq['a_success_rate']:.0%} | "
                f"{pq['b_success_rate']:.0%} | {judge_winner} |"
            )

    return "\n".join(lines)


def _md_sig(m) -> str:
    if not m.significant:
        return "-"
    p = m.p_value or 1.0
    if p < 0.001:
        return "*** (p<0.001)"
    if p < 0.01:
        return f"** (p={p:.4f})"
    return f"* (p={p:.4f})"


def _fmt_p_simple(p: float | None) -> str:
    if p is None:
        return "N/A"
    if p < 0.001:
        return "<0.001"
    return f"{p:.4f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run A/B tests comparing two agent variants",
    )
    parser.add_argument(
        "--queries", required=True,
        help="Path to JSONL file with queries (one query per line with 'query' key)",
    )
    parser.add_argument(
        "--variant-a", required=True,
        help='JSON string for variant A, e.g. \'{"name":"pro","model":"deepseek-v4-pro"}\'',
    )
    parser.add_argument(
        "--variant-b", required=True,
        help="JSON string for variant B",
    )
    parser.add_argument(
        "--n-samples", type=int, default=1,
        help="Number of repeated measurements per query per variant (default: 1)",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory to write JSON and Markdown reports",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Disable LLM judge scoring",
    )

    args = parser.parse_args()

    queries = _load_queries(args.queries)

    try:
        variant_a = ABVariant.model_validate(json.loads(args.variant_a))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Invalid --variant-a JSON: %s", exc)
        sys.exit(1)

    try:
        variant_b = ABVariant.model_validate(json.loads(args.variant_b))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Invalid --variant-b JSON: %s", exc)
        sys.exit(1)

    exit_code = asyncio.run(
        _run_ab_test(
            queries=queries,
            variant_a=variant_a,
            variant_b=variant_b,
            n_samples=args.n_samples,
            output_dir=args.output_dir,
            no_judge=args.no_judge,
        )
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
