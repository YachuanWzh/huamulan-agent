from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from personal_assistant.skills.evaluation.ops import (
    EvaluationComparison,
    EvaluationRun,
    compare_evaluation_runs,
)


def render_markdown(comparison: EvaluationComparison) -> str:
    lines = [
        "# Agent Evaluation Regression Report",
        "",
        f"- Baseline: `{comparison.baseline_run_id}`",
        f"- Candidate: `{comparison.candidate_run_id}`",
        f"- Gate: **{comparison.status.upper()}**",
        f"- Pass rate: {comparison.baseline_pass_rate:.1%} → {comparison.candidate_pass_rate:.1%}",
        "",
        "| Severity | Rule | Case | Baseline | Candidate | Message |",
        "|---|---|---|---|---|---|",
    ]
    for item in comparison.findings:
        lines.append(
            f"| {item.severity} | {item.rule} | {item.case_id or '-'} | "
            f"{_cell(item.baseline)} | {_cell(item.candidate)} | {item.message} |"
        )
    if not comparison.findings:
        lines.append("| info | no_regression | - | - | - | 未发现回归 |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two Agent evaluation runs")
    parser.add_argument("--baseline-json", required=True)
    parser.add_argument("--candidate-json", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)
    try:
        baseline = EvaluationRun.model_validate_json(
            Path(args.baseline_json).read_text(encoding="utf-8")
        )
        candidate = EvaluationRun.model_validate_json(
            Path(args.candidate_json).read_text(encoding="utf-8")
        )
        if baseline.status != "completed" or candidate.status != "completed":
            return 2
        comparison = compare_evaluation_runs(baseline, candidate)
        if args.output_json:
            Path(args.output_json).write_text(
                comparison.model_dump_json(indent=2), encoding="utf-8"
            )
        if args.output_md:
            Path(args.output_md).write_text(render_markdown(comparison), encoding="utf-8")
        return 1 if comparison.status == "failed" else 0
    except (OSError, ValueError, json.JSONDecodeError):
        return 2


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    sys.exit(main())
