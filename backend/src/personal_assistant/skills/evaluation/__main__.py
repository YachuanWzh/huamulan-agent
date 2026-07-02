import argparse
import asyncio
import json
from pathlib import Path

from personal_assistant.skills import SkillRegistry
from personal_assistant.skills.evaluation.models import AgentEvaluationCase
from personal_assistant.skills.evaluation.report import (
    evaluate_skill_registry,
    render_markdown_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Skill quality metrics.")
    parser.add_argument("--skills-dir", required=True)
    parser.add_argument("--golden")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args(argv)

    registry = SkillRegistry(args.skills_dir)
    cases = _load_golden(Path(args.golden)) if args.golden else None
    report = asyncio.run(evaluate_skill_registry(registry, cases=cases))

    if args.output_json:
        Path(args.output_json).write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
    else:
        print(report.model_dump_json(indent=2))

    markdown = render_markdown_report(report)
    if args.output_md:
        Path(args.output_md).write_text(markdown, encoding="utf-8")
    return 0


def _load_golden(path: Path) -> list[AgentEvaluationCase]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cases.append(AgentEvaluationCase.model_validate(json.loads(stripped)))
    return cases


if __name__ == "__main__":
    raise SystemExit(main())
