import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

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
    parser.add_argument(
        "--llm-base-url",
        default=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
    )
    parser.add_argument(
        "--llm-api-key",
        default=os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""),
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("LLM_MODEL", ""),
    )
    args = parser.parse_args(argv)

    router_kwargs = {}
    if args.llm_model and args.llm_api_key:
        from langchain_deepseek import ChatDeepSeek

        import httpx

        llm = ChatDeepSeek(
            api_base=args.llm_base_url,
            api_key=args.llm_api_key,
            model=args.llm_model,
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=10.0),
            max_retries=3,
        )
        router_kwargs["llm"] = llm
        router_kwargs["llm_retry_count"] = 1
        # 真实语义层：连接内网 Ollama bge-m3，走完整三级漏斗
        from personal_assistant.agent.router import (
            InMemorySkillVectorIndex,
            OllamaBgeM3EmbeddingProvider,
        )

        embedding_url = os.environ.get("SKILL_EMBEDDING_URL", "http://192.168.5.107:11434")
        router_kwargs["semantic_index"] = InMemorySkillVectorIndex(
            OllamaBgeM3EmbeddingProvider(base_url=embedding_url, timeout_seconds=30.0),
        )
        router_kwargs["semantic_threshold"] = 0.72
        router_kwargs["semantic_top_k"] = 3

    registry = SkillRegistry(args.skills_dir)
    cases = _load_golden(Path(args.golden)) if args.golden else None

    async def _run_eval() -> Any:
        semantic_index = router_kwargs.get("semantic_index")
        if semantic_index is not None:
            # warmup 会在 Ollama 上对每个 skill 做一次 bge-m3 embedding
            await semantic_index.warmup(registry)
        return await evaluate_skill_registry(registry, cases=cases, **router_kwargs)

    report = asyncio.run(_run_eval())

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
