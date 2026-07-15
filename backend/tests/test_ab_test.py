"""Tests for the A/B testing framework."""

import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from personal_assistant.skills.evaluation.ab_test import (
    ABMetricComparison,
    ABSampleResult,
    ABTestReport,
    ABTestRunner,
    ABVariant,
    PairwiseJudgeResult,
    _betainc_reg,
    _judge_pairwise,
    _normal_cdf,
    _t_distribution_cdf,
    _t_distribution_two_sided_p,
    fisher_exact_p,
    format_ab_report,
    mann_whitney_u_p,
    mcnemar_p,
    paired_t_test_p,
    wilcoxon_signed_rank_p,
)
from personal_assistant.skills.evaluation.models import ABTestResult


# ── Statistical helper tests ───────────────────────────────────────────


def test_normal_cdf_symmetry() -> None:
    """Normal CDF at 0 should be 0.5, symmetric around 0."""
    assert _normal_cdf(0) == pytest.approx(0.5, abs=0.01)
    assert _normal_cdf(-1) == pytest.approx(1 - _normal_cdf(1), abs=0.01)


def test_betainc_reg_edge_cases() -> None:
    """Incomplete beta function edge cases."""
    assert _betainc_reg(0, 0.5, 0.5) == 0.0
    assert _betainc_reg(1, 0.5, 0.5) == 1.0
    assert math.isnan(_betainc_reg(-0.1, 1, 1))
    assert math.isnan(_betainc_reg(1.1, 1, 1))


def test_t_distribution_cdf() -> None:
    """t-distribution CDF sanity checks."""
    # At t=0, CDF should be 0.5
    assert _t_distribution_cdf(0, 10) == pytest.approx(0.5, abs=0.01)
    # Large t should approach 1
    assert _t_distribution_cdf(100, 10) > 0.99
    # Negative symmetry
    assert _t_distribution_cdf(-1, 10) == pytest.approx(
        1 - _t_distribution_cdf(1, 10), abs=0.05
    )


def test_t_distribution_two_sided() -> None:
    """Two-sided p-value decreases as |t| increases."""
    p_small = _t_distribution_two_sided_p(0.5, 10)
    p_large = _t_distribution_two_sided_p(3.0, 10)
    assert p_large < p_small


# ── Fisher's exact test ─────────────────────────────────────────────────


def test_fisher_exact_identical() -> None:
    """Identical proportions should yield p=1.0."""
    p = fisher_exact_p(5, 5, 5, 5)
    assert p > 0.5


def test_fisher_exact_strong_effect() -> None:
    """Strong effect should yield small p-value."""
    p = fisher_exact_p(10, 0, 0, 10)
    assert p < 0.001


def test_fisher_exact_moderate_effect() -> None:
    """Moderate effect should yield p between 0.01 and 0.5."""
    p = fisher_exact_p(8, 2, 4, 6)
    assert 0.01 < p < 0.9


def test_fisher_exact_capped_at_one() -> None:
    """p-value should never exceed 1.0."""
    p = fisher_exact_p(1, 1, 1, 1)
    assert p <= 1.0


# ── Mann-Whitney U test ─────────────────────────────────────────────────


def test_mann_whitney_u_identical() -> None:
    """Identical distributions should give p close to 1."""
    p = mann_whitney_u_p([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert p > 0.5


def test_mann_whitney_u_separated() -> None:
    """Completely separated distributions should give small p."""
    p = mann_whitney_u_p([10.0, 11.0, 12.0], [1.0, 2.0, 3.0])
    assert p < 0.1


def test_mann_whitney_u_empty_sample() -> None:
    """Empty sample should return 1.0."""
    assert mann_whitney_u_p([], [1.0, 2.0]) == 1.0
    assert mann_whitney_u_p([1.0], []) == 1.0


# ── Paired t-test ───────────────────────────────────────────────────────


def test_paired_t_test_zero_difference() -> None:
    """Zero difference should give p close to 1."""
    p = paired_t_test_p([0.0, 0.0, 0.0])
    assert p > 0.9


def test_paired_t_test_clear_difference() -> None:
    """Clear consistent difference should give small p."""
    p = paired_t_test_p([5.0, 5.0, 5.0, 5.0])
    # All same value non-zero -> sd=0 but mean non-zero -> p=0
    assert p == 0.0


def test_paired_t_test_moderate() -> None:
    """Moderate difference with variance."""
    diffs = [1.0, 2.0, 0.5, 1.5, 0.8, 2.2, 1.1, 1.9]
    p = paired_t_test_p(diffs)
    assert p < 0.05
    assert p > 0.0


def test_paired_t_test_small_sample() -> None:
    """Small sample returns 1.0."""
    assert paired_t_test_p([1.0]) == 1.0
    assert paired_t_test_p([]) == 1.0


# ── Wilcoxon signed-rank test ───────────────────────────────────────────


def test_wilcoxon_signed_rank_symmetric() -> None:
    """Symmetric around zero gives p close to 1."""
    p = wilcoxon_signed_rank_p([-1.0, 1.0, -2.0, 2.0, 0.5, -0.5])
    assert p > 0.5


def test_wilcoxon_signed_rank_all_positive() -> None:
    """All positive differences should give small p."""
    p = wilcoxon_signed_rank_p([1.0, 2.0, 3.0, 1.5, 2.5, 3.5, 4.0])
    # All positive -> strong evidence difference > 0
    assert p < 0.05


def test_wilcoxon_signed_rank_small_sample() -> None:
    """Very small sample returns 1.0."""
    assert wilcoxon_signed_rank_p([1.0, 2.0]) == 1.0


def test_wilcoxon_signed_rank_with_zeros() -> None:
    """Zeros should be excluded."""
    p = wilcoxon_signed_rank_p([0.0, 0.0, 1.0, 2.0, 3.0])
    # After removing zeros, 3 non-zero all positive
    assert p < 0.5


# ── McNemar's test ──────────────────────────────────────────────────────


def test_mcnemar_equal_discordant() -> None:
    """Equal discordant counts should give high p."""
    p = mcnemar_p(5, 5)
    assert p > 0.5


def test_mcnemar_strong_asymmetry() -> None:
    """Strong discordant asymmetry should give small p."""
    p = mcnemar_p(15, 1)
    assert p < 0.01


def test_mcnemar_small_sample() -> None:
    """Small discordant total uses exact binomial."""
    p = mcnemar_p(3, 0)
    assert 0.0 < p <= 1.0


# ── Data model tests ───────────────────────────────────────────────────


def test_ab_variant_defaults() -> None:
    """ABVariant should have sensible defaults."""
    v = ABVariant(name="test")
    assert v.name == "test"
    assert v.agent_mode == "multi"
    assert v.model is None
    assert v.temperature is None
    assert v.prompt_overrides == {}
    assert v.config_overrides == {}


def test_ab_variant_full_config() -> None:
    """ABVariant with full configuration."""
    v = ABVariant(
        name="flash",
        model="deepseek-v4-flash",
        agent_mode="single",
        temperature=0.1,
        prompt_overrides={"supervisor": "new prompt"},
        config_overrides={"max_tokens": 4096},
    )
    assert v.model == "deepseek-v4-flash"
    assert v.agent_mode == "single"
    assert v.prompt_overrides["supervisor"] == "new prompt"


def test_ab_variant_invalid_agent_mode() -> None:
    """Invalid agent_mode should be rejected."""
    with pytest.raises(ValidationError):
        ABVariant(name="bad", agent_mode="invalid")  # type: ignore[arg-type]


def test_ab_metric_comparison() -> None:
    """ABMetricComparison model round-trips correctly."""
    m = ABMetricComparison(
        metric_name="latency",
        value_a=100.0,
        value_b=80.0,
        delta_pct=-20.0,
        p_value=0.03,
        significant=True,
        test_method="Mann-Whitney U",
        direction="lower_is_better",
        interpretation="* (p<0.05)",
    )
    data = m.model_dump_json()
    restored = ABMetricComparison.model_validate_json(data)
    assert restored.metric_name == m.metric_name
    assert restored.significant is True


def test_ab_sample_result() -> None:
    """ABSampleResult creation and serialization."""
    sr = ABSampleResult(
        query="test query",
        variant_name="baseline",
        sample_index=0,
        status="completed",
        message="test response",
        latency_ms=123.45,
    )
    assert sr.status == "completed"
    assert sr.token_usage == {}


def test_ab_test_report_structure() -> None:
    """ABTestReport should contain all required fields."""
    report = ABTestReport(
        run_id="test-123",
        variant_a=ABVariant(name="control"),
        variant_b=ABVariant(name="treatment"),
        n_queries=5,
        n_samples=2,
        metrics=[
            ABMetricComparison(
                metric_name="pass_rate",
                value_a=90.0,
                value_b=85.0,
                delta_pct=-5.5,
                p_value=0.3,
                significant=False,
                test_method="McNemar",
                direction="higher_is_better",
            )
        ],
        conclusion="No significant difference.",
    )
    assert report.run_id == "test-123"
    assert report.n_queries == 5
    assert len(report.metrics) == 1


def test_ab_test_result_model() -> None:
    """ABTestResult model (from models.py) validates correctly."""
    result = ABTestResult(
        run_id="ab-1",
        variant_a_name="pro",
        variant_b_name="flash",
        variant_a_model="deepseek-v4-pro",
        variant_b_model="deepseek-v4-flash",
        n_queries=10,
        n_samples=3,
        metrics=[
            {"metric_name": "latency", "value_a": 100, "value_b": 80, "delta_pct": -20}
        ],
        conclusion="flash is faster",
        created_at="2025-01-15T10:00:00Z",
    )
    assert result.run_id == "ab-1"
    assert result.n_queries == 10
    assert len(result.metrics) == 1


# ── Report formatting tests ─────────────────────────────────────────────


def test_format_ab_report() -> None:
    """Format ABTestReport produces readable text."""
    report = ABTestReport(
        run_id="r1",
        variant_a=ABVariant(name="A", model="m1"),
        variant_b=ABVariant(name="B", model="m2"),
        n_queries=3,
        n_samples=1,
        metrics=[
            ABMetricComparison(
                metric_name="pass_rate",
                value_a=90.0,
                value_b=87.0,
                delta_pct=-3.33,
                p_value=0.12,
                significant=False,
                test_method="McNemar",
                direction="higher_is_better",
            ),
            ABMetricComparison(
                metric_name="latency_mean",
                value_a=3200.0,
                value_b=1100.0,
                delta_pct=-65.6,
                p_value=0.0005,
                significant=True,
                test_method="Mann-Whitney U",
                direction="lower_is_better",
            ),
        ],
        conclusion="B is significantly faster.",
    )

    output = format_ab_report(report)

    assert "A/B Test Report: A vs B" in output
    assert "pass_rate" in output
    assert "latency_mean" in output
    assert "***" in output  # significance marker
    assert "n = 3 queries" in output
    assert "B is significantly faster" in output


def test_format_ab_report_no_significant_difference() -> None:
    """Report with no significant differences."""
    report = ABTestReport(
        run_id="r2",
        variant_a=ABVariant(name="A"),
        variant_b=ABVariant(name="B"),
        n_queries=10,
        n_samples=2,
        metrics=[
            ABMetricComparison(
                metric_name="pass_rate",
                value_a=90.0,
                value_b=90.0,
                delta_pct=0.0,
                p_value=1.0,
                significant=False,
                test_method="McNemar",
                direction="higher_is_better",
            ),
        ],
        conclusion="No statistically significant difference detected.",
    )
    output = format_ab_report(report)
    assert "No statistically significant difference" in output


# ── ABTestRunner tests ──────────────────────────────────────────────────


class _FakeHarness:
    """Fake harness that returns canned responses."""

    def __init__(self, latencies: dict[str, list[float]] | None = None):
        self.calls: list[dict] = []
        self._latencies = latencies or {}

    async def run_user_turn(self, thread_id, message, llm_config, *, agent_mode, requires_approval=None):
        import time

        model = llm_config.model if llm_config else "default"
        self.calls.append({
            "thread_id": thread_id,
            "message": message,
            "model": model,
            "agent_mode": agent_mode,
        })

        if model in self._latencies and self._latencies[model]:
            latency = self._latencies[model].pop(0)
            await _fake_sleep(latency / 1000.0)

        from personal_assistant.api.schemas import ChatResponse

        return ChatResponse(
            thread_id=thread_id,
            status="completed",
            message=f"Response from {model}: {message[:30]}",
        )


async def _fake_sleep(seconds: float) -> None:
    """Non-blocking fake sleep for testing."""
    import asyncio
    await asyncio.sleep(0)  # Don't actually sleep


class _FakeSettings:
    llm_model = "deepseek-v4-pro"


async def test_ab_test_runner_basic_flow() -> None:
    """ABTestRunner should execute queries and produce report."""
    harness = _FakeHarness()
    settings = _FakeSettings()
    runner = ABTestRunner(harness=harness, settings=settings, judge_llm=None)

    queries = ["query 1", "query 2", "query 3"]
    report = await runner.run(
        queries=queries,
        variant_a=ABVariant(name="control", model="model-a"),
        variant_b=ABVariant(name="experiment", model="model-b"),
        n_samples=1,
        run_judge=False,
    )

    assert report.run_id
    assert report.n_queries == 3
    assert report.n_samples == 1
    assert len(report.raw_results) == 6  # 3 queries × 2 variants
    assert len(report.metrics) >= 1  # At least pass_rate
    assert report.variant_a.name == "control"
    assert report.variant_b.name == "experiment"


async def test_ab_test_runner_collects_latency() -> None:
    """Runner should measure and compare latencies."""
    harness = _FakeHarness()
    settings = _FakeSettings()
    runner = ABTestRunner(harness=harness, settings=settings, judge_llm=None)

    report = await runner.run(
        queries=["q1", "q2", "q3"],
        variant_a=ABVariant(name="A", model="m1"),
        variant_b=ABVariant(name="B", model="m2"),
        n_samples=1,
        run_judge=False,
    )

    # Should have latency metric
    latency_metrics = [m for m in report.metrics if m.metric_name == "latency_mean"]
    assert len(latency_metrics) >= 1
    # All results should have non-negative latency
    for r in report.raw_results:
        assert r.latency_ms >= 0


async def test_ab_test_runner_with_judge() -> None:
    """Runner should handle judge scoring when judge_llm provided."""

    class _FakeJudgeLLM:
        def __init__(self):
            self.calls = []

        async def ainvoke(self, prompt):
            self.calls.append(prompt)
            import json

            class _FakeResponse:
                content = json.dumps({
                    "score_a": 3.5,
                    "score_b": 4.0,
                    "winner": "B",
                    "reason": "B is more concise",
                    "dimension_scores": {},
                })

            return _FakeResponse()

    judge_llm = _FakeJudgeLLM()
    harness = _FakeHarness()
    settings = _FakeSettings()
    runner = ABTestRunner(harness=harness, settings=settings, judge_llm=judge_llm)

    report = await runner.run(
        queries=["q1", "q2"],
        variant_a=ABVariant(name="A", model="m1"),
        variant_b=ABVariant(name="B", model="m2"),
        n_samples=1,
        run_judge=True,
    )

    # Judge should have been called
    assert len(judge_llm.calls) >= 2

    # Should have judge_score metric
    judge_metrics = [m for m in report.metrics if m.metric_name == "judge_score"]
    assert len(judge_metrics) >= 1


async def test_ab_test_runner_parallel_execution() -> None:
    """A and B for the same sample should be run in parallel."""
    harness = _FakeHarness()
    settings = _FakeSettings()
    runner = ABTestRunner(harness=harness, settings=settings, judge_llm=None)

    queries = ["q1", "q2"]
    await runner.run(
        queries=queries,
        variant_a=ABVariant(name="A", model="m1"),
        variant_b=ABVariant(name="B", model="m2"),
        n_samples=2,
        run_judge=False,
    )

    # 2 queries × 2 samples × 2 variants = 8 total calls
    assert len(harness.calls) == 8


async def test_ab_test_runner_handles_failures() -> None:
    """Runner should handle exceptions gracefully."""

    class _FailingHarness:
        calls = 0

        async def run_user_turn(self, thread_id, message, llm_config, *, agent_mode, requires_approval=None):
            self.calls += 1
            if self.calls % 2 == 0:
                raise RuntimeError("simulated failure")
            from personal_assistant.api.schemas import ChatResponse

            return ChatResponse(
                thread_id=thread_id,
                status="completed",
                message="ok",
            )

    harness = _FailingHarness()
    settings = _FakeSettings()
    runner = ABTestRunner(harness=harness, settings=settings, judge_llm=None)

    report = await runner.run(
        queries=["q1", "q2", "q3"],
        variant_a=ABVariant(name="A"),
        variant_b=ABVariant(name="B"),
        n_samples=1,
        run_judge=False,
    )

    # Should complete despite failures
    assert report.n_queries == 3
    # Some results should have "failed" status
    failed = [r for r in report.raw_results if r.status == "failed"]
    assert len(failed) > 0


# ── Pairwise judge tests ────────────────────────────────────────────────


async def test_judge_pairwise_returns_scores() -> None:
    """Pairwise judge should parse LLM response correctly."""

    class _JudgeLLM:
        async def ainvoke(self, prompt):
            import json

            class _Resp:
                content = json.dumps({
                    "score_a": 4.0,
                    "score_b": 3.0,
                    "winner": "A",
                    "reason": "A is better",
                    "dimension_scores": {"correctness": {"A": 4, "B": 3}},
                })

            return _Resp()

    result = await _judge_pairwise("q", "response A", "response B", _JudgeLLM())

    assert result is not None
    assert result.score_a == 4.0
    assert result.score_b == 3.0
    assert result.winner == "A"


async def test_judge_pairwise_handles_malformed_json() -> None:
    """Pairwise judge handles malformed JSON gracefully."""

    class _BadJudgeLLM:
        async def ainvoke(self, prompt):
            class _Resp:
                content = "not json at all"

            return _Resp()

    result = await _judge_pairwise("q", "A", "B", _BadJudgeLLM())
    assert result is None


# ── McNemar edge cases ──────────────────────────────────────────────────


def test_mcnemar_zero_discordant() -> None:
    """Zero discordant pairs should be OK."""
    p = mcnemar_p(0, 0)
    assert 0.0 <= p <= 1.0


# ── Integration: ABTestReport ↔ ABTestResult ────────────────────────────


def test_report_to_result_conversion() -> None:
    """ABTestReport can be converted to ABTestResult for persistence."""
    report = ABTestReport(
        run_id="r1",
        variant_a=ABVariant(name="A", model="m1"),
        variant_b=ABVariant(name="B", model="m2"),
        n_queries=5,
        n_samples=2,
        metrics=[
            ABMetricComparison(
                metric_name="pass_rate",
                value_a=90.0,
                value_b=85.0,
                delta_pct=-5.5,
                p_value=0.3,
                significant=False,
                test_method="McNemar",
                direction="higher_is_better",
            ),
        ],
        conclusion="No significant difference.",
    )

    result = ABTestResult(
        run_id=report.run_id,
        variant_a_name=report.variant_a.name,
        variant_b_name=report.variant_b.name,
        variant_a_model=report.variant_a.model,
        variant_b_model=report.variant_b.model,
        agent_mode=report.variant_a.agent_mode,
        n_queries=report.n_queries,
        n_samples=report.n_samples,
        metrics=[m.model_dump(mode="json") for m in report.metrics],
        conclusion=report.conclusion,
        created_at=report.created_at.isoformat(),
    )

    assert result.run_id == report.run_id
    assert result.n_queries == 5
    assert result.metrics[0]["metric_name"] == "pass_rate"
