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


class _CISemanticIndex:
    """CI 用轻量语义层：不依赖 embedding 服务，返回全部 skill 作为候选。

    这保证三级漏斗代码路径全覆盖（正则 → 语义 → LLM），因为语义层总是
    返回低于阈值的 score，让每个 case 都能走到 LLM 判定。
    """

    async def warmup(self, registry: SkillRegistry) -> None:
        pass

    async def search(self, registry: SkillRegistry, _query: str, top_k: int):
        from personal_assistant.agent.router import SkillSemanticCandidate

        return [
            SkillSemanticCandidate(name=s.name, description=s.description, score=0.0)
            for s in list(registry.skills.values())[:top_k]
        ]


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
        # CI 环境下无法使用真实 Ollama embedding（无 GPU / 网络限制），
        # 用 _CISemanticIndex 确保语义层代码路径仍然执行，每个 case 都能走到 LLM。
        router_kwargs["semantic_index"] = _CISemanticIndex()
        router_kwargs["semantic_threshold"] = 0.01  # score=0.0 必低于此阈值，触发 LLM
        router_kwargs["semantic_top_k"] = 5

    registry = SkillRegistry(args.skills_dir)
    cases = _load_golden(Path(args.golden)) if args.golden else None

    async def _run_eval() -> Any:
        semantic_index = router_kwargs.get("semantic_index")
        if semantic_index is not None:
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
