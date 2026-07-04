from pathlib import Path
from textwrap import dedent
from urllib.error import HTTPError
import io
import json
import logging

import pytest

from personal_assistant.skills.loader import SkillRegistry
from personal_assistant.agent.router import (
    OllamaBgeM3Reranker,
    QdrantSkillVectorIndex,
    RerankUnavailableError,
    SkillSemanticCandidate,
    build_skill_router,
    build_system_prompt,
    route_skill_names,
    route_skill_names_with_trace,
    _keyword_route,
)
from personal_assistant.memory.long_term import LongTermMemoryStore


def _make_triggered_skill(tmp_path: Path) -> Path:
    """A skill whose trigger words do NOT appear in its name or description."""
    d = tmp_path / "cal"
    d.mkdir()
    (d / "SKILL.md").write_text(
        dedent(
            """
            ---
            name: cal
            description: Performs calendar arithmetic.
            triggers:
              - 今天
              - tomorrow
              - 星期几
            ---

            # Cal
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_named_skill(tmp_path: Path, name: str, description: str = "Test skill") -> None:
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )


def _make_named_skill_with_triggers(
    tmp_path: Path,
    name: str,
    triggers: list[str],
    description: str = "Test skill",
) -> None:
    d = tmp_path / name
    d.mkdir()
    trigger_lines = "\n".join(f"  - {trigger}" for trigger in triggers)
    (d / "SKILL.md").write_text(
        (
            f"---\nname: {name}\ndescription: {description}\n"
            f"triggers:\n{trigger_lines}\n---\n# {name}\n"
        ),
        encoding="utf-8",
    )


class TestKeywordRoute:
    def test_matches_by_name_and_description(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # "alpha" appears in skill-a's name and description
        result = _keyword_route(registry, "I need help with alpha tasks")
        assert "skill-a" in result

    def test_no_match_returns_empty(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        result = _keyword_route(registry, "completely unrelated xyz query")
        assert result == []

    def test_matches_multiple_skills(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        # "skill" appears in all skills' descriptions (meta only)
        result = _keyword_route(registry, "I need a skill for this")
        assert len(result) == 3

    def test_does_not_match_on_full_instructions_only(self, multi_skill_dir: Path):
        """Keywords that appear only in full SKILL.md (not meta) should not match."""
        registry = SkillRegistry(multi_skill_dir)
        # "handles" appears in full SKILL.md ("Handles alpha tasks") but not in meta
        result = _keyword_route(registry, "please handle this request")
        assert result == []


class TestTriggerRouting:
    def test_matches_via_triggers_when_present(self, tmp_path: Path):
        """A trigger word not in name/description still routes the skill."""
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        # "今天" is a trigger but does not appear in name "cal" or description
        assert _keyword_route(registry, "今天是几号") == ["cal"]

    def test_matches_english_trigger(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "what about tomorrow") == ["cal"]

    def test_no_trigger_match_returns_empty(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        assert _keyword_route(registry, "completely unrelated xyz") == []

    def test_skills_without_triggers_fall_back_to_token_match(self, multi_skill_dir: Path):
        """Skills without a triggers list still match on name/description tokens."""
        registry = SkillRegistry(multi_skill_dir)
        result = _keyword_route(registry, "I need help with alpha tasks")
        assert "skill-a" in result


class TestChineseRegexRouting:
    @pytest.mark.parametrize(
        ("skill_name", "query"),
        [
            ("weather", "北京明天会下雨吗"),
            ("resolve-time", "今天是几号"),
            ("audit-sop", "审计一下这个线程的执行日志"),
        ],
    )
    def test_current_skill_regexes_match_chinese_queries(
        self,
        tmp_path: Path,
        skill_name: str,
        query: str,
    ):
        _make_named_skill(tmp_path, skill_name)
        registry = SkillRegistry(tmp_path)

        assert _keyword_route(registry, query) == [skill_name]

    def test_air_quality_query_routes_to_weather_not_resolve_time(self, tmp_path: Path):
        _make_named_skill(tmp_path, "weather")
        _make_named_skill(tmp_path, "resolve-time")
        registry = SkillRegistry(tmp_path)

        query = (
            "\u5317\u4eac\u4eca\u5929\u7a7a\u6c14\u8d28\u91cfAQI"
            "\u591a\u5c11\uff1f\u9002\u5408\u8dd1\u6b65\u5417"
        )

        assert _keyword_route(registry, query) == ["weather"]

    def test_air_quality_query_ignores_resolve_time_relative_day_trigger(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "weather")
        _make_named_skill_with_triggers(
            tmp_path,
            "resolve-time",
            ["\u4eca\u5929"],
        )
        registry = SkillRegistry(tmp_path)

        query = (
            "\u5317\u4eac\u4eca\u5929\u7a7a\u6c14\u8d28\u91cfAQI"
            "\u591a\u5c11\uff1f\u9002\u5408\u8dd1\u6b65\u5417"
        )

        assert _keyword_route(registry, query) == ["weather"]

    def test_temperature_swing_query_ignores_resolve_time_relative_day_trigger(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "weather")
        _make_named_skill_with_triggers(
            tmp_path,
            "resolve-time",
            ["\u660e\u5929"],
        )
        registry = SkillRegistry(tmp_path)

        query = (
            "\u54c8\u5c14\u6ee8\u660e\u5929\u65e9\u665a"
            "\u6e29\u5dee\u5927\u4e0d\u5927\uff1f"
            "\u65e9\u4e0a\u548c\u665a\u4e0a\u5206\u522b"
            "\u591a\u5c11\u5ea6"
        )

        assert _keyword_route(registry, query) == ["weather"]

    def test_date_query_still_routes_to_resolve_time(self, tmp_path: Path):
        _make_named_skill(tmp_path, "weather")
        _make_named_skill(tmp_path, "resolve-time")
        registry = SkillRegistry(tmp_path)

        assert _keyword_route(
            registry,
            "\u4eca\u5929\u662f\u51e0\u6708\u51e0\u53f7\uff1f",
        ) == ["resolve-time"]

    def test_tool_failure_query_routes_to_audit_sop(self, tmp_path: Path):
        _make_named_skill(tmp_path, "audit-sop")
        registry = SkillRegistry(tmp_path)

        query = (
            "\u6392\u67e5\u4e00\u4e0b\u4e3a\u4ec0\u4e48shell_command"
            "\u8fd9\u4e2a\u5de5\u5177\u6700\u8fd1\u603b\u662f\u5931\u8d25"
        )

        assert _keyword_route(registry, query) == ["audit-sop"]

    def test_security_block_trend_query_ignores_resolve_time_period_trigger(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "audit-sop")
        _make_named_skill_with_triggers(
            tmp_path,
            "resolve-time",
            ["\u4e00\u5468"],
        )
        registry = SkillRegistry(tmp_path)

        query = (
            "\u5206\u6790\u6700\u8fd1\u4e00\u5468\u7684"
            "\u5b89\u5168\u62e6\u622a\u8d8b\u52bf\uff0c"
            "\u662f\u8d8a\u6765\u8d8a\u591a\u8fd8\u662f"
            "\u8d8a\u6765\u8d8a\u5c11"
        )

        assert _keyword_route(registry, query) == ["audit-sop"]

    def test_multi_weather_api_performance_query_routes_all_required_skills(
        self,
        tmp_path: Path,
    ):
        for name in ("resolve-time", "weather", "troubleshoot"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        query = (
            "\u540e\u5929\u4e0a\u6d77\u5929\u6c14\u600e\u6837\uff1f"
            "\u5982\u679c\u4e0b\u96e8\u7684\u8bdd\u5e2e\u6211\u6392\u67e5"
            "\u4e00\u4e0b\u6700\u8fd1\u7684API\u662f\u4e0d\u662f"
            "\u6709\u96e8\u5929\u7684\u6027\u80fd\u95ee\u9898\u3002"
        )

        assert _keyword_route(registry, query) == [
            "resolve-time",
            "weather",
            "troubleshoot",
        ]

    def test_real_registry_multi_weather_api_query_keeps_auxiliary_time(self):
        registry = SkillRegistry(
            Path(__file__).parents[1] / "src" / "personal_assistant" / "skills"
        )

        query = (
            "\u540e\u5929\u4e0a\u6d77\u5929\u6c14\u600e\u6837\uff1f"
            "\u5982\u679c\u4e0b\u96e8\u7684\u8bdd\u5e2e\u6211\u6392\u67e5"
            "\u4e00\u4e0b\u6700\u8fd1\u7684API\u662f\u4e0d\u662f"
            "\u6709\u96e8\u5929\u7684\u6027\u80fd\u95ee\u9898\u3002"
        )

        assert _keyword_route(registry, query) == [
            "resolve-time",
            "weather",
            "troubleshoot",
        ]

    def test_multi_005_month_end_date_audit_routes_resolve_time_and_audit(self):
        """multi-005: \u8fd9\u6708\u5e95\u6700\u540e\u4e00\u5929\u662f\u51e0\u53f7\uff1f\u5e2e\u6211\u5ba1\u8ba1\u90a3\u5929\u7684\u6240\u6709\u6267\u884c\u65e5\u5fd7\u770b\u770b\u6709\u6ca1\u6709\u5f02\u5e38\u3002
        Should select BOTH resolve-time (date question) and audit-sop (audit logs)."""
        # Use real registry with actual skills
        registry = SkillRegistry(
            Path(__file__).parents[1] / "src" / "personal_assistant" / "skills"
        )

        query = "\u8fd9\u6708\u5e95\u6700\u540e\u4e00\u5929\u662f\u51e0\u53f7\uff1f\u5e2e\u6211\u5ba1\u8ba1\u90a3\u5929\u7684\u6240\u6709\u6267\u884c\u65e5\u5fd7\u770b\u770b\u6709\u6ca1\u6709\u5f02\u5e38\u3002"

        result = _keyword_route(registry, query)
        assert "resolve-time" in result, f"resolve-time missing, got: {result}"
        assert "audit-sop" in result, f"audit-sop missing, got: {result}"

    @pytest.mark.asyncio
    async def test_deterministic_route_trace_includes_rule_metadata(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "weather")
        registry = SkillRegistry(tmp_path)

        result = await route_skill_names_with_trace(
            registry,
            "\u5317\u4eac\u4eca\u5929\u7a7a\u6c14\u8d28\u91cfAQI"
            "\u591a\u5c11\uff1f",
        )

        regex_stage = result.trace[0]
        assert regex_stage["stage"] == "regex"
        assert regex_stage["matches"][0]["skill"] == "weather"
        assert regex_stage["matches"][0]["rule_id"] == "weather.air_quality"
        assert regex_stage["matches"][0]["source"] == "regex"


class TestPatrolRouting:
    def test_routes_alert_rule_patrol_before_audit_or_find_skills(self, tmp_path: Path):
        for name in ("patrol", "audit-sop", "find-skills"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            "配置一条巡检规则：frontend_error_rate > 0.02 for 5m，帮我跑业务治理巡检并输出异常发现。",
        )

        assert result == ["patrol"]

    def test_routes_night_patrol_then_troubleshoot(self, tmp_path: Path):
        for name in (
            "patrol",
            "troubleshoot",
            "apm-metrics",
            "audit-sop",
            "troubleshoot-runbook",
        ):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            "夜间巡检发现 LCP p95、JS error rate、tool retry ratio 都异常，请先做自动巡检，再触发智能排障分析根因。",
        )

        assert result == ["patrol", "troubleshoot"]

    def test_routes_full_patrol_with_chinese_thresholds(self, tmp_path: Path):
        """e2e-patrol-019: 全量巡检+中文阈值场景应该命中patrol技能"""
        _make_named_skill(tmp_path, "patrol")
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            "帮我做一次全量巡检：当前 API 健康通过率 99.2%、数据库连接正常、Redis 命中率 87%、前端 JS 错误率 2.1%、订单接口 p95 延迟 3200ms。告警阈值：API 健康<99%、Redis 命中率<90%、JS 错误率>5%、p95 延迟>2000ms。输出每项 pass/fail 和修复建议。",
        )

        assert result == ["patrol"]

    def test_audit_apm_routes_patrol_and_audit_for_system_patrol_log_audit(
        self,
        tmp_path: Path,
    ):
        for name in ("patrol", "audit-sop", "troubleshoot"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "帮我跑一次系统巡检，看看有没有异常指标。"
                "如果有的话，审计一下对应的执行日志看看根因是什么。"
            ),
        )

        assert result == ["patrol", "audit-sop"]

    def test_audit_apm_routes_sla_compliance_report_to_audit_sop(
        self,
        tmp_path: Path,
    ):
        for name in ("patrol", "audit-sop", "apm-metrics"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "检查所有活跃线程的 SLA 合规情况：tool_success_rate < 95%、"
                "approval_response_time > 30s 的标记为不合规，输出合规报告。"
            ),
        )

        assert result == ["audit-sop"]

    def test_audit_apm_routes_custom_business_metric_definition_to_apm_metrics(
        self,
        tmp_path: Path,
    ):
        for name in ("audit-sop", "apm-metrics", "patrol"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "APM 里怎么定义和采集自定义业务指标？"
                "比如用户下单成功率、支付转化率这些。"
            ),
        )

        assert result == ["apm-metrics"]

    def test_audit_apm_routes_cross_thread_governance_patrol_to_audit_only(
        self,
        tmp_path: Path,
    ):
        for name in ("patrol", "audit-sop", "troubleshoot"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "帮我做一次跨线程业务治理巡检，聚合最近的 tool error rate、"
                "retry rate、安全拦截率、审批拒绝率和 token 增长，找系统性风险。"
            ),
        )

        assert result == ["audit-sop"]

    def test_audit_apm_does_not_route_find_skills_when_skill_names_are_mentioned(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "audit-sop")
        _make_named_skill(tmp_path, "troubleshoot")
        _make_named_skill(
            tmp_path,
            "find-skills",
            "Helps users discover and install agent skills.",
        )
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "最近多个会话都有前端白屏和 agent 工具重试，请把 audit-sop "
                "的跨线程治理报告和 troubleshoot 的根因分析结合起来，"
                "输出业务影响和修复优先级。"
            ),
        )

        assert result == ["audit-sop", "troubleshoot"]

    def test_audit_apm_routes_error_rate_metric_explanation_to_apm_metrics_only(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "apm-metrics")
        _make_named_skill(tmp_path, "audit-sop")
        _make_named_skill_with_triggers(
            tmp_path,
            "troubleshoot-runbook",
            ["JS error"],
            description="APM troubleshooting runbook library for JS errors.",
        )
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "什么是 JS error rate、resource error rate 和 Apdex？"
                "这些 APM 指标应该怎样在前端性能监控 SDK 里采集？"
            ),
        )

        assert result == ["apm-metrics"]

    def test_audit_apm_routes_business_metric_threshold_design_before_patrol(
        self,
        tmp_path: Path,
    ):
        for name in ("apm-metrics", "patrol", "audit-sop"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "APM 里怎么定义和采集下单成功率、支付转化率和优惠券使用率？"
                "请给出 numerator、denominator、去重规则、维度和告警阈值。"
            ),
        )

        assert result == ["apm-metrics"]

    def test_audit_apm_runbook_query_does_not_token_match_every_apm_skill(
        self,
        tmp_path: Path,
    ):
        _make_named_skill(tmp_path, "apm-metrics", "APM metric knowledge base.")
        _make_named_skill(tmp_path, "audit-sop", "Agent execution audit and APM governance.")
        _make_named_skill(tmp_path, "troubleshoot", "APM incident RCA.")
        _make_named_skill_with_triggers(
            tmp_path,
            "troubleshoot-runbook",
            ["runbook"],
            description="APM troubleshooting runbook library.",
        )
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "第三方支付回调 30s 超时，订单状态出现 paid_pending 和 "
                "paid_success 不一致。请按 APM runbook 给出排查步骤、"
                "降级策略和回滚方案。"
            ),
        )

        assert result == ["troubleshoot-runbook"]

    def test_audit_apm_rca_with_execution_logs_does_not_overselect_audit(
        self,
        tmp_path: Path,
    ):
        for name in ("apm-metrics", "audit-sop", "troubleshoot"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            (
                "生产发布后 /checkout 页面 TypeError 集中爆发，LCP p95 从 "
                "2.4s 涨到 6.7s。请结合 RUM 和执行日志做 RCA 根因分析，"
                "给出修复和验证步骤。"
            ),
        )

        assert result == ["apm-metrics", "troubleshoot"]


class FakeSemanticIndex:
    def __init__(self, candidates: list[SkillSemanticCandidate]):
        self.candidates = candidates
        self.calls: list[str] = []

    async def search(self, registry: SkillRegistry, query: str, top_k: int):
        self.calls.append(query)
        return self.candidates[:top_k]


class FailingSemanticIndex:
    async def search(self, registry: SkillRegistry, query: str, top_k: int):
        raise RuntimeError("embedding service unavailable")


class FakeReranker:
    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, candidates: list[SkillSemanticCandidate]):
        self.calls.append((query, [candidate.name for candidate in candidates]))
        reranked = [
            SkillSemanticCandidate(
                name=candidate.name,
                description=candidate.description,
                score=self.scores.get(candidate.name, candidate.score),
            )
            for candidate in candidates
        ]
        return sorted(reranked, key=lambda candidate: candidate.score, reverse=True)


class FailingReranker:
    async def rerank(self, query: str, candidates: list[SkillSemanticCandidate]):
        raise RuntimeError("rerank service unavailable")


class UnavailableReranker:
    async def rerank(self, query: str, candidates: list[SkillSemanticCandidate]):
        raise RerankUnavailableError("model lacks embedding capability")


class CountingEmbeddingProvider:
    def __init__(self):
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0, 0.0, 0.0]


class FakeQdrantSkillVectorIndex(QdrantSkillVectorIndex):
    def __init__(
        self,
        embedding_provider,
        existing_points: list[dict] | None = None,
        search_results: list[dict] | None = None,
    ):
        super().__init__(
            embedding_provider,
            url="http://qdrant.example.test:6333",
            collection="skill_routes",
        )
        self.existing_points = existing_points or []
        self.search_results = search_results or []
        self.upserted_points: list[dict] = []

    def _scroll_sync(self) -> dict:
        return {"result": {"points": self.existing_points}}

    def _upsert_sync(self, points: list[dict]) -> dict:
        self.upserted_points.extend(points)
        return {"result": {"operation_id": 1}}

    def _search_sync(self, vector: list[float], top_k: int) -> dict:
        return {"result": self.search_results[:top_k]}


class FakeStructuredLLM:
    def __init__(self, outputs: list[object]):
        self.outputs = outputs
        self.payloads: list[dict] = []
        self.structured_output_calls = 0

    def with_structured_output(self, schema):
        self.structured_output_calls += 1
        self.schema = schema
        return self

    async def ainvoke(self, payload: dict):
        self.payloads.append(payload)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class TestSkillRoutingFunnel:
    @pytest.mark.asyncio
    async def test_regex_match_short_circuits_semantic_and_llm(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="other", description="Other", score=0.99)]
        )
        llm = FakeStructuredLLM([{"selectedSkill": "other", "confidence": 1.0, "reason": "x"}])

        result = await route_skill_names(
            registry,
            "what about tomorrow",
            semantic_index=semantic,
            llm=llm,
        )

        assert result == ["cal"]
        assert semantic.calls == []
        assert llm.payloads == []

    @pytest.mark.asyncio
    async def test_semantic_match_selects_top_candidate_above_threshold(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: other\ndescription: Other stuff.\n---\n# Other\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="other", description="Other stuff.", score=0.91)]
        )
        llm = FakeStructuredLLM([{"selectedSkill": "cal", "confidence": 1.0, "reason": "x"}])

        result = await route_skill_names(
            registry,
            "semantically related but no regex hit",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result == ["other"]
        assert semantic.calls == ["semantically related but no regex hit"]
        assert llm.payloads == []

    @pytest.mark.asyncio
    async def test_rerank_reorders_semantic_candidates_before_threshold_selection(
        self,
        tmp_path: Path,
    ):
        _make_triggered_skill(tmp_path)
        _make_named_skill(tmp_path, "other", "Other stuff.")
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [
                SkillSemanticCandidate(name="other", description="Other stuff.", score=0.77),
                SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.74),
            ]
        )
        reranker = FakeReranker({"cal": 0.94, "other": 0.12})
        llm = FakeStructuredLLM(
            [{"selectedSkill": "other", "confidence": 1.0, "reason": "not needed"}]
        )

        result = await route_skill_names(
            registry,
            "semantically related but no regex hit",
            semantic_index=semantic,
            reranker=reranker,
            semantic_threshold=0.8,
            rerank_threshold=0.9,
            llm=llm,
        )

        assert result == ["cal"]
        assert reranker.calls == [
            ("semantically related but no regex hit", ["other", "cal"])
        ]
        assert llm.payloads == []

    @pytest.mark.asyncio
    async def test_rerank_failure_keeps_semantic_candidates_for_llm_fallback(
        self,
        tmp_path: Path,
    ):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": "cal", "confidence": 0.75, "reason": "calendar intent"}]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            reranker=FailingReranker(),
            semantic_threshold=0.8,
            rerank_threshold=0.8,
            llm=llm,
        )

        assert result == ["cal"]
        assert len(llm.payloads) == 1
        assert "cal: Calendar arithmetic." in llm.payloads[0]

    @pytest.mark.asyncio
    async def test_rerank_unavailable_logs_without_traceback(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                reranker=UnavailableReranker(),
                semantic_threshold=0.8,
                rerank_threshold=0.8,
            )

        assert result == []
        assert "Skill routing rerank stage unavailable" in caplog.text
        assert "model lacks embedding capability" in caplog.text
        assert "Traceback" not in caplog.text

    @pytest.mark.asyncio
    async def test_logs_rerank_stage_when_enabled(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        _make_named_skill(tmp_path, "other", "Other stuff.")
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [
                SkillSemanticCandidate(name="other", description="Other stuff.", score=0.77),
                SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.74),
            ]
        )
        reranker = FakeReranker({"cal": 0.94, "other": 0.12})

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "semantically related but no regex hit",
                semantic_index=semantic,
                reranker=reranker,
                semantic_threshold=0.8,
                rerank_threshold=0.9,
                rerank_top_k=2,
            )

        assert result == ["cal"]
        assert "Skill routing rerank stage started" in caplog.text
        assert "input_candidates=other:0.7700, cal:0.7400" in caplog.text
        assert "top_k=2" in caplog.text
        assert "Skill routing rerank stage completed" in caplog.text
        assert "output_candidates=cal:0.9400, other:0.1200" in caplog.text

    @pytest.mark.asyncio
    async def test_low_semantic_score_falls_back_to_llm_judge(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": "cal", "confidence": 0.75, "reason": "calendar intent"}]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result == ["cal"]
        assert len(llm.payloads) == 1
        assert llm.structured_output_calls == 0
        assert isinstance(llm.payloads[0], str)
        assert '"userInput": "ambiguous date wording"' in llm.payloads[0]
        assert '"relatedFind": [' in llm.payloads[0]
        assert "cal: Calendar arithmetic." in llm.payloads[0]

    @pytest.mark.asyncio
    async def test_route_trace_records_each_funnel_stage(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": None, "confidence": 0.22, "reason": "not enough evidence"}]
        )

        result = await route_skill_names_with_trace(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result.selected_skills == []
        assert result.trace == [
            {
                "stage": "regex",
                "status": "missed",
                "selected_skills": [],
                "reason": "no regex or trigger matched",
            },
            {
                "stage": "semantic",
                "status": "below_threshold",
                "candidates": [
                    {
                        "name": "cal",
                        "description": "Calendar arithmetic.",
                        "score": 0.31,
                    }
                ],
                "threshold": 0.8,
                "top_candidate": "cal",
                "reason": "top candidate score below threshold",
            },
            {
                "stage": "llm_judge",
                "status": "rejected",
                "selected_skill": None,
                "confidence": 0.22,
                "reason": "not enough evidence",
            },
        ]

    @pytest.mark.asyncio
    async def test_llm_judge_accepts_chat_message_json_content(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [FakeMessage('{"selectedSkill":"cal","confidence":0.75,"reason":"calendar intent"}')]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
        )

        assert result == ["cal"]
        assert llm.structured_output_calls == 0

    @pytest.mark.asyncio
    async def test_llm_api_error_degrades_to_no_match(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM([RuntimeError("Thinking mode does not support this tool_choice")])

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                llm=llm,
                semantic_threshold=0.8,
            )

        assert result == []
        assert llm.structured_output_calls == 0
        assert "Skill routing LLM judge request failed" in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_llm_output_is_retried_with_error_context(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [
                {"skill": "cal"},
                {"selectedSkill": "cal", "confidence": 0.83, "reason": "fixed json"},
            ]
        )

        result = await route_skill_names(
            registry,
            "ambiguous date wording",
            semantic_index=semantic,
            llm=llm,
            semantic_threshold=0.8,
            llm_retry_count=1,
        )

        assert result == ["cal"]
        assert len(llm.payloads) == 2
        assert "previousError" in llm.payloads[1]
        assert "previousOutput" in llm.payloads[1]
        assert '\\"skill\\": \\"cal\\"' in llm.payloads[1]

    @pytest.mark.asyncio
    async def test_semantic_failure_degrades_to_no_match(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)

        result = await route_skill_names(
            registry,
            "no deterministic match",
            semantic_index=FailingSemanticIndex(),
            llm=FakeStructuredLLM(
                [{"selectedSkill": "cal", "confidence": 0.9, "reason": "would match"}]
            ),
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_logs_semantic_threshold_and_llm_no_match(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM(
            [{"selectedSkill": None, "confidence": 0.22, "reason": "not enough evidence"}]
        )

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                llm=llm,
                semantic_threshold=0.8,
            )

        assert result == []
        assert "Skill routing regex stage missed" in caplog.text
        assert "Skill routing semantic stage candidates=cal:0.3100 threshold=0.8000" in caplog.text
        assert "Skill routing semantic top candidate below threshold" in caplog.text
        assert "Skill routing LLM judge rejected candidates" in caplog.text
        assert "selected=None" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_llm_validation_error_before_retry(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        semantic = FakeSemanticIndex(
            [SkillSemanticCandidate(name="cal", description="Calendar arithmetic.", score=0.31)]
        )
        llm = FakeStructuredLLM([{"skill": "cal"}])

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            result = await route_skill_names(
                registry,
                "ambiguous date wording",
                semantic_index=semantic,
                llm=llm,
                semantic_threshold=0.8,
                llm_retry_count=0,
            )

        assert result == []
        assert "Skill routing LLM judge validation failed" in caplog.text
        assert "attempt=1" in caplog.text


class TestQdrantSkillVectorIndexWarmup:
    @pytest.mark.asyncio
    async def test_warmup_skips_embedding_when_qdrant_has_same_source_hash(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["cal"]
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(
            embedding_provider,
            existing_points=[
                {
                    "payload": {
                        "skill_name": "cal",
                        "source_hash": skill.source_hash,
                    }
                }
            ],
        )

        await index.warmup(registry)

        assert embedding_provider.texts == []
        assert index.upserted_points == []

    @pytest.mark.asyncio
    async def test_warmup_embeds_and_upserts_changed_skill(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(
            embedding_provider,
            existing_points=[
                {
                    "payload": {
                        "skill_name": "cal",
                        "source_hash": "old-hash",
                    }
                }
            ],
        )

        await index.warmup(registry)

        assert embedding_provider.texts == ["cal\nPerforms calendar arithmetic.\n今天, tomorrow, 星期几"]
        assert len(index.upserted_points) == 1
        assert index.upserted_points[0]["payload"]["skill_name"] == "cal"

    @pytest.mark.asyncio
    async def test_warmup_logs_qdrant_sync_summary(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(embedding_provider, existing_points=[])

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            await index.warmup(registry)

        assert "Qdrant skill vector sync started" in caplog.text
        assert "Qdrant skill vector sync completed" in caplog.text
        assert "upserted=1" in caplog.text

    @pytest.mark.asyncio
    async def test_search_logs_qdrant_recall_results(self, tmp_path: Path, caplog):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        skill = registry.skills["cal"]
        embedding_provider = CountingEmbeddingProvider()
        index = FakeQdrantSkillVectorIndex(
            embedding_provider,
            existing_points=[
                {
                    "payload": {
                        "skill_name": "cal",
                        "source_hash": skill.source_hash,
                    }
                }
            ],
            search_results=[
                {
                    "score": 0.91,
                    "payload": {
                        "skill_name": "cal",
                        "description": "Performs calendar arithmetic.",
                    },
                }
            ],
        )

        with caplog.at_level(logging.INFO, logger="personal_assistant.agent.router"):
            candidates = await index.search(registry, "明天是星期几", top_k=3)

        assert [candidate.name for candidate in candidates] == ["cal"]
        assert "Qdrant skill vector search started" in caplog.text
        assert "Qdrant skill vector search completed" in caplog.text
        assert "candidates=cal:0.9100" in caplog.text

    def test_qdrant_http_error_includes_collection_and_endpoint(self, monkeypatch):
        embedding_provider = CountingEmbeddingProvider()
        index = QdrantSkillVectorIndex(
            embedding_provider,
            url="http://qdrant.example.test:6333",
            collection="skill_routes",
        )

        def raise_404(request, timeout):
            raise HTTPError(
                request.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"status":{"error":"Not found: Collection skill_routes"}}'),
            )

        monkeypatch.setattr("urllib.request.urlopen", raise_404)

        with pytest.raises(RuntimeError) as exc_info:
            index._scroll_sync()

        message = str(exc_info.value)
        assert "Qdrant request failed" in message
        assert "collection=skill_routes" in message
        assert "POST /collections/skill_routes/points/scroll" in message
        assert "HTTP 404" in message
        assert "Collection skill_routes" in message


class TestOllamaBgeM3Reranker:
    def test_reranker_scores_candidates_with_ollama_embed_response(self, monkeypatch):
        requests: list[dict] = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"embeddings": [[0.91]]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            requests.append(
                {
                    "url": request.full_url,
                    "body": json.loads(request.data.decode("utf-8")),
                    "timeout": timeout,
                }
            )
            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        reranker = OllamaBgeM3Reranker(
            base_url="http://ollama.example.test:11434",
            model="custom-reranker",
            timeout_seconds=3,
        )

        result = reranker._rerank_sync(
            "date question",
            [SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.12)],
        )

        assert result == [
            SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.91)
        ]
        assert requests == [
            {
                "url": "http://ollama.example.test:11434/api/embed",
                "body": {
                    "model": "custom-reranker",
                    "input": "query: date question\npassage: cal\nCalendar math.",
                },
                "timeout": 3,
            }
        ]

    def test_reranker_rejects_model_without_embedding_capability(self, monkeypatch):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "models": [
                            {
                                "name": "qllama/bge-reranker-v2-m3:latest",
                                "model": "qllama/bge-reranker-v2-m3:latest",
                                "capabilities": ["completion"],
                            }
                        ]
                    }
                ).encode("utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
        reranker = OllamaBgeM3Reranker(
            base_url="http://ollama.example.test:11434",
            model="qllama/bge-reranker-v2-m3",
            timeout_seconds=3,
        )

        with pytest.raises(RuntimeError) as exc_info:
            reranker._rerank_sync(
                "date question",
                [SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.12)],
            )

        message = str(exc_info.value)
        assert "does not advertise embedding capability" in message
        assert "capabilities=completion" in message

    def test_reranker_http_error_includes_response_body(self, monkeypatch):
        def raise_500(request, timeout):
            raise HTTPError(
                request.full_url,
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b'{"error":"llama-server process has terminated"}'),
            )

        def fake_urlopen(request, timeout):
            if request.full_url.endswith("/api/tags"):
                class FakeTagsResponse:
                    def __enter__(self):
                        return self

                    def __exit__(self, exc_type, exc, traceback):
                        return False

                    def read(self):
                        return json.dumps(
                            {
                                "models": [
                                    {
                                        "name": "custom-reranker:latest",
                                        "model": "custom-reranker:latest",
                                        "capabilities": ["embedding"],
                                    }
                                ]
                            }
                        ).encode("utf-8")

                return FakeTagsResponse()
            raise_500(request, timeout)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        reranker = OllamaBgeM3Reranker(
            base_url="http://ollama.example.test:11434",
            model="custom-reranker",
        )

        with pytest.raises(RuntimeError) as exc_info:
            reranker._rerank_sync(
                "date question",
                [SkillSemanticCandidate(name="cal", description="Calendar math.", score=0.12)],
            )

        message = str(exc_info.value)
        assert "HTTP 500 Internal Server Error" in message
        assert "llama-server process has terminated" in message


class TestRouteSkillsNoFallback:
    """route_skills must not force-load all skills when nothing matches."""

    @pytest.mark.asyncio
    async def test_no_match_loads_no_skills(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        state = await router({"messages": [], "selected_skills": []})
        # Nothing matched "random unrelated text"
        assert state["selected_skills"] == []
        # Skill stays unloaded — only meta overview is in the system prompt
        assert not registry.skills["cal"].loaded

    @pytest.mark.asyncio
    async def test_match_loads_only_matched_skill(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        # add a second skill that won't match
        other = tmp_path / "other"
        other.mkdir()
        (other / "SKILL.md").write_text(
            "---\nname: other\ndescription: Other stuff.\n---\n# Other\n", encoding="utf-8"
        )
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        from langchain_core.messages import HumanMessage

        state = await router({"messages": [HumanMessage(content="今天怎么样")]})
        assert state["selected_skills"] == ["cal"]
        assert registry.skills["cal"].loaded
        assert not registry.skills["other"].loaded

    @pytest.mark.asyncio
    async def test_system_prompt_omits_skill_meta_when_unmatched(self, tmp_path: Path):
        _make_triggered_skill(tmp_path)
        registry = SkillRegistry(tmp_path)
        router = build_skill_router(registry)

        state = await router({"messages": [], "selected_skills": []})
        system = state["messages"][0]
        assert "Available Skills" not in system.content
        assert "- **cal**" not in system.content
        assert "Performs calendar arithmetic." not in system.content


class TestBuildSystemPrompt:
    def test_omits_skill_meta_when_none_selected(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[])
        content = msg.content
        assert "Available Skills" not in content
        assert "skill-a" not in content
        assert "Skill Alpha" not in content
        assert "skill-b" not in content
        assert "Skill Beta" not in content

    def test_includes_full_instructions_for_selected(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        msg = build_system_prompt(registry, selected=["skill-a"])
        content = msg.content
        assert "## Available Skills" in content
        assert "- **skill-a**: Skill Alpha" in content
        assert "Available tools:" in content  # from SKILL.md full content
        assert "## Skill: skill-a" in content
        assert "skill-b" not in content
        assert "skill-c" not in content

    def test_base_preamble_present(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[])
        assert "personal assistant" in msg.content.lower()

    def test_unselected_skills_are_not_in_prompt(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        registry.load_skill("skill-a")
        msg = build_system_prompt(registry, selected=["skill-a"])
        content = msg.content
        # skill-a should have full instructions section
        assert "## Skill: skill-a" in content
        assert "- **skill-a**: Skill Alpha" in content
        assert "skill-b" not in content
        assert "Skill Beta" not in content
        assert "## Skill: skill-b" not in content


class TestMemoryInjection:
    def test_system_prompt_includes_memory_when_store_provided(self, multi_skill_dir: Path, tmp_path: Path):
        registry = SkillRegistry(multi_skill_dir)
        memory_store = LongTermMemoryStore(tmp_path / ".memory")
        memory_store.ensure_files()
        (tmp_path / ".memory" / "USER.md").write_text(
            "# User\n\nCall me Yazuki.\n", encoding="utf-8"
        )

        msg = build_system_prompt(registry, selected=[], long_term_memory=memory_store)
        assert "Yazuki" in msg.content

    def test_system_prompt_works_without_memory_store(self, multi_skill_dir: Path):
        registry = SkillRegistry(multi_skill_dir)
        msg = build_system_prompt(registry, selected=[], long_term_memory=None)
        assert "personal assistant" in msg.content.lower()

    def test_memory_appears_before_skills_in_prompt(self, multi_skill_dir: Path, tmp_path: Path):
        registry = SkillRegistry(multi_skill_dir)
        memory_store = LongTermMemoryStore(tmp_path / ".memory")
        memory_store.add_memory(
            slug="test-mem",
            title="Test Memory",
            summary="A test memory",
            body="This is a test memory entry.",
        )

        msg = build_system_prompt(registry, selected=[], long_term_memory=memory_store)
        content = msg.content
        assert "Test Memory" in content
        assert "Available Skills" not in content
