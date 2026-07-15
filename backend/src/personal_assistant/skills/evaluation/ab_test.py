"""
A/B Testing framework for agent variants.

Supports parallel execution of the same queries through two agent variants
(different models, prompts, or configurations), automatic multi-dimensional
metric collection, statistical significance testing, and quantified comparison
reports.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import mean, stdev
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Statistical helpers ────────────────────────────────────────────────


def _normal_cdf(x: float) -> float:
    """Standard normal CDF via complementary error function."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _normal_ppf(p: float) -> float:
    """Standard normal quantile (probit) via rational approximation."""
    if p <= 0:
        return float("-inf")
    if p >= 1:
        return float("inf")
    # Abramowitz and Stegun approximation (26.2.23)
    c0 = 2.515517
    c1 = 0.802853
    c2 = 0.010328
    d1 = 1.432788
    d2 = 0.189269
    d3 = 0.001308
    t = math.sqrt(-2.0 * math.log(min(p, 1 - p)))
    numerator = c0 + c1 * t + c2 * t * t
    denominator = 1 + d1 * t + d2 * t * t + d3 * t * t * t
    z = t - numerator / denominator
    return -z if p < 0.5 else z


def _betainc_reg(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a,b) via continued fraction.

    Implements Lentz's method for the continued fraction representation.
    """
    if x < 0 or x > 1:
        return float("nan")
    if x == 0:
        return 0.0
    if x == 1:
        return 1.0

    log_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    max_iter = 200

    for m in range(1, max_iter + 1):
        m2 = 2 * m
        # Even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        # Odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        dh = d * c
        h *= dh
        if abs(dh - 1.0) < 1e-12:
            break

    return math.exp(a * math.log(x) + b * math.log(1 - x) - log_beta - math.log(a)) * h


def _t_distribution_cdf(t: float, df: float) -> float:
    """CDF of Student's t-distribution using incomplete beta function.

    F(t, ν) = 1 - ½·I_{ν/(ν+t²)}(ν/2, ½)   for t ≥ 0
    F(t, ν) = ½·I_{ν/(ν+t²)}(ν/2, ½)        for t < 0
    """
    x = df / (df + t * t)
    beta_val = _betainc_reg(x, df / 2, 0.5)
    if t >= 0:
        return 1.0 - 0.5 * beta_val
    else:
        return 0.5 * beta_val


def _t_distribution_two_sided_p(t: float, df: float) -> float:
    """Two-sided p-value from t-statistic."""
    return 2.0 * (1.0 - _t_distribution_cdf(abs(t), df))


def _log_choose(n: int, k: int) -> float:
    """Log of binomial coefficient using lgamma."""
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


# ── Fisher's Exact Test ─────────────────────────────────────────────────


def fisher_exact_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher's exact test for 2×2 table [[a,b],[c,d]].

    Computes the exact p-value by summing hypergeometric probabilities
    of tables as or more extreme than observed.
    """
    n = a + b + c + d
    row1 = a + b
    col1 = a + c
    observed_log_p = _log_choose(row1, a) + _log_choose(n - row1, col1 - a) - _log_choose(n, col1)

    p_value = 0.0
    # Range of possible 'a' values given fixed margins
    min_a = max(0, col1 + row1 - n)
    max_a = min(row1, col1)

    for k in range(min_a, max_a + 1):
        log_p = _log_choose(row1, k) + _log_choose(n - row1, col1 - k) - _log_choose(n, col1)
        if log_p <= observed_log_p + 1e-12:
            p_value += math.exp(log_p)

    return min(p_value, 1.0)


# ── Mann-Whitney U Test ─────────────────────────────────────────────────


@dataclass
class _RankedValue:
    value: float
    group: int  # 0 or 1
    rank: float = 0.0


def mann_whitney_u_p(sample_a: Sequence[float], sample_b: Sequence[float]) -> float:
    """Two-sided Mann-Whitney U test p-value using normal approximation.

    For very small samples (< 8 per group), the p-value is approximate.
    """
    n_a = len(sample_a)
    n_b = len(sample_b)
    if n_a == 0 or n_b == 0:
        return 1.0

    # Merge and rank
    items: list[_RankedValue] = []
    for v in sample_a:
        items.append(_RankedValue(value=v, group=0))
    for v in sample_b:
        items.append(_RankedValue(value=v, group=1))
    items.sort(key=lambda x: x.value)

    # Assign ranks (average for ties)
    i = 0
    while i < len(items):
        j = i
        while j + 1 < len(items) and items[j + 1].value == items[i].value:
            j += 1
        avg_rank = (i + j + 2) / 2.0  # 1-indexed ranks averaged
        for k in range(i, j + 1):
            items[k].rank = avg_rank
        i = j + 1

    # Sum of ranks for group A
    r_a = sum(item.rank for item in items if item.group == 0)

    # U statistic
    u_a = r_a - n_a * (n_a + 1) / 2.0
    u_b = n_a * n_b - u_a
    u = min(u_a, u_b)

    # Normal approximation
    mu = n_a * n_b / 2.0
    sigma = math.sqrt(n_a * n_b * (n_a + n_b + 1) / 12.0)

    if sigma < 1e-12:
        return 1.0 if abs(u - mu) < 1e-12 else 0.0

    z = abs(u - mu) / sigma
    # Continuity correction
    z = max(0, z - 0.5 / sigma)
    return 2.0 * (1.0 - _normal_cdf(z))


# ── Paired t-test ───────────────────────────────────────────────────────


def paired_t_test_p(differences: Sequence[float]) -> float:
    """Two-sided paired t-test p-value."""
    n = len(differences)
    if n < 2:
        return 1.0
    m = mean(differences)
    sd = stdev(differences)
    if sd == 0:
        return 1.0 if m == 0 else 0.0
    t = m / (sd / math.sqrt(n))
    return _t_distribution_two_sided_p(t, n - 1)


# ── Wilcoxon Signed-Rank Test ───────────────────────────────────────────


def wilcoxon_signed_rank_p(differences: Sequence[float]) -> float:
    """Two-sided Wilcoxon signed-rank test p-value using normal approximation."""
    # Remove zero differences
    nonzero = [d for d in differences if d != 0]
    n = len(nonzero)
    if n < 3:
        return 1.0

    # Rank absolute differences
    ranked = sorted(enumerate(nonzero), key=lambda x: abs(x[1]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(ranked[j + 1][1]) == abs(ranked[i][1]):
            j += 1
        avg = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[ranked[k][0]] = avg
        i = j + 1

    # Sum of ranks for positive differences
    w = sum(ranks[k] for k in range(n) if nonzero[k] > 0)

    # Normal approximation
    mu = n * (n + 1) / 4.0
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)

    if sigma < 1e-12:
        return 1.0 if abs(w - mu) < 1e-12 else 0.0

    z = abs(w - mu) / sigma
    # Continuity correction
    z = max(0, z - 0.5 / sigma)
    return 2.0 * (1.0 - _normal_cdf(z))


# ── McNemar's Test ──────────────────────────────────────────────────────


def mcnemar_p(n_ab: int, n_ba: int) -> float:
    """McNemar's test for paired binary data.

    n_ab: count where A passed and B failed
    n_ba: count where A failed and B passed
    """
    n_discordant = n_ab + n_ba
    if n_discordant < 5:
        # Exact binomial test
        p_exact = 0.0
        for k in range(min(n_ab, n_ba) + 1):
            p_exact += math.exp(_log_choose(n_discordant, k))
        p_exact *= 2.0 ** (-n_discordant)
        return min(2.0 * p_exact, 1.0)

    # Chi-square with continuity correction
    chi2 = (abs(n_ab - n_ba) - 1) ** 2 / n_discordant if n_discordant > 0 else 0
    # Chi-square(1) -> normal: sqrt(chi2) ~ |N(0,1)|
    z = math.sqrt(chi2)
    p = 2.0 * (1.0 - _normal_cdf(z))
    return min(p, 1.0)


# ── Data models ─────────────────────────────────────────────────────────


class ABVariant(BaseModel):
    """Configuration for a single A/B test variant."""

    name: str
    model: str | None = None
    agent_mode: Literal["single", "multi"] = "multi"
    temperature: float | None = None
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class ABMetricComparison(BaseModel):
    """Per-metric statistical comparison between two variants."""

    metric_name: str
    value_a: float
    value_b: float
    delta_pct: float  # (B - A) / A * 100
    p_value: float | None = None
    significant: bool = False
    test_method: str = ""
    direction: Literal["lower_is_better", "higher_is_better"] = "lower_is_better"
    interpretation: str = ""


class ABSampleResult(BaseModel):
    """Raw result for a single query × variant × sample."""

    query: str
    variant_name: str
    sample_index: int
    status: str
    message: str = ""
    latency_ms: float = 0.0
    token_usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None


class ABTestReport(BaseModel):
    """Complete A/B test report with statistical analysis."""

    run_id: str
    variant_a: ABVariant
    variant_b: ABVariant
    n_queries: int
    n_samples: int
    metrics: list[ABMetricComparison] = Field(default_factory=list)
    per_query_summary: list[dict[str, Any]] = Field(default_factory=list)
    conclusion: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_results: list[ABSampleResult] = Field(default_factory=list)


# ── LLM Judge for pairwise comparison ───────────────────────────────────

_PAIRWISE_JUDGE_PROMPT = """You are an evaluation judge comparing two AI agent responses to the same query.

Query: {query}

Response A:
{response_a}

Response B:
{response_b}

Evaluate both responses on these dimensions (score 1-5 for each):
- correctness: factual accuracy and absence of errors
- completeness: covers all aspects of the query
- clarity: well-structured and easy to understand
- usefulness: actionable and relevant to the query

Return strict JSON with keys:
- score_a: overall score for A (1-5)
- score_b: overall score for B (1-5)
- winner: "A", "B", or "tie"
- reason: brief explanation of the comparison
- dimension_scores: dict with per-dimension scores for A and B

Do NOT include markdown, only JSON."""


@dataclass
class PairwiseJudgeResult:
    score_a: float
    score_b: float
    winner: Literal["A", "B", "tie"]
    reason: str
    dimension_scores: dict[str, Any] = field(default_factory=dict)


async def _judge_pairwise(
    query: str,
    response_a: str,
    response_b: str,
    judge_llm: Any,
) -> PairwiseJudgeResult | None:
    """Run pairwise LLM judge comparison. Returns None on failure."""
    try:
        prompt = _PAIRWISE_JUDGE_PROMPT.format(
            query=query,
            response_a=response_a or "(empty)",
            response_b=response_b or "(empty)",
        )
        result = await judge_llm.ainvoke(prompt)
        content = getattr(result, "content", str(result))
        # Extract JSON from response
        json_str = content.strip()
        if json_str.startswith("```"):
            json_str = json_str.strip("`")
            if json_str.startswith("json"):
                json_str = json_str[4:]
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start >= 0 and end >= start:
            json_str = json_str[start : end + 1]
        data = json.loads(json_str)
        return PairwiseJudgeResult(
            score_a=float(data.get("score_a", 0)),
            score_b=float(data.get("score_b", 0)),
            winner=data.get("winner", "tie"),
            reason=str(data.get("reason", "")),
            dimension_scores=data.get("dimension_scores", {}),
        )
    except Exception:
        return None


# ── ABTestRunner ────────────────────────────────────────────────────────


class ABTestRunner:
    """Execute A/B tests comparing two agent variants on a shared set of queries."""

    def __init__(
        self,
        harness: Any,
        settings: Any,
        judge_llm: Any = None,
    ):
        self.harness = harness
        self.settings = settings
        self.judge_llm = judge_llm

    def _build_llm_config(self, variant: ABVariant) -> Any:
        """Build LLMConfig from variant settings."""
        from personal_assistant.api.schemas import LLMConfig

        return LLMConfig(
            model=variant.model or self.settings.llm_model,
            temperature=variant.temperature,
        )

    async def _run_one(
        self,
        query: str,
        variant: ABVariant,
        sample_index: int,
    ) -> ABSampleResult:
        """Execute a single query against a single variant."""
        thread_id = f"ab-{variant.name}-{sample_index}-{uuid.uuid4().hex[:8]}"
        llm_config = self._build_llm_config(variant)

        t0 = time.perf_counter()
        try:
            response = await self.harness.run_user_turn(
                thread_id=thread_id,
                message=query,
                llm_config=llm_config,
                agent_mode=variant.agent_mode,
            )
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ABSampleResult(
                query=query,
                variant_name=variant.name,
                sample_index=sample_index,
                status=response.status,
                message=response.message or "",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return ABSampleResult(
                query=query,
                variant_name=variant.name,
                sample_index=sample_index,
                status="failed",
                latency_ms=latency_ms,
                error=str(exc),
            )

    async def run(
        self,
        queries: list[str],
        variant_a: ABVariant,
        variant_b: ABVariant,
        *,
        n_samples: int = 1,
        run_judge: bool = True,
    ) -> ABTestReport:
        """Execute full A/B test.

        Args:
            queries: List of query strings to test.
            variant_a: Control variant configuration.
            variant_b: Treatment variant configuration.
            n_samples: Number of repeated measurements per query per variant.
            run_judge: Whether to run LLM pairwise judge on outputs.

        Returns:
            ABTestReport with statistical analysis.
        """
        run_id = uuid.uuid4().hex[:12]
        all_results: list[ABSampleResult] = []

        # ── Execute all runs ──────────────────────────────────────
        for qi, query in enumerate(queries):
            for si in range(n_samples):
                # Run A and B in parallel for this sample
                result_a, result_b = await asyncio.gather(
                    self._run_one(query, variant_a, si),
                    self._run_one(query, variant_b, si),
                )
                all_results.extend([result_a, result_b])

        # ── Aggregate by query ────────────────────────────────────
        per_query: list[dict[str, Any]] = []
        for qi, query in enumerate(queries):
            a_results = [
                r for r in all_results
                if r.query == query and r.variant_name == variant_a.name
            ]
            b_results = [
                r for r in all_results
                if r.query == query and r.variant_name == variant_b.name
            ]

            a_latencies = [r.latency_ms for r in a_results]
            b_latencies_vals = [r.latency_ms for r in b_results]

            a_success = sum(1 for r in a_results if r.status == "completed")
            b_success = sum(1 for r in b_results if r.status == "completed")

            # Run judge if requested and both have outputs
            judge_result = None
            if run_judge and self.judge_llm and a_results and b_results:
                resp_a = a_results[-1].message
                resp_b = b_results[-1].message
                if resp_a or resp_b:
                    judge_result = await _judge_pairwise(
                        query, resp_a, resp_b, self.judge_llm,
                    )

            per_query.append({
                "query": query[:120],
                "a_latency_mean": mean(a_latencies) if a_latencies else 0,
                "b_latency_mean": mean(b_latencies_vals) if b_latencies_vals else 0,
                "a_success_rate": a_success / len(a_results) if a_results else 0,
                "b_success_rate": b_success / len(b_results) if b_results else 0,
                "judge_score_a": judge_result.score_a if judge_result else None,
                "judge_score_b": judge_result.score_b if judge_result else None,
                "judge_winner": judge_result.winner if judge_result else None,
            })

        # ── Compute overall metrics ───────────────────────────────
        metrics: list[ABMetricComparison] = []

        # 1. Success rate (pass rate)
        a_success_total = sum(1 for r in all_results if r.variant_name == variant_a.name and r.status == "completed")
        b_success_total = sum(1 for r in all_results if r.variant_name == variant_b.name and r.status == "completed")
        n_a = sum(1 for r in all_results if r.variant_name == variant_a.name)
        n_b = sum(1 for r in all_results if r.variant_name == variant_b.name)
        a_pass = a_success_total / n_a if n_a else 0
        b_pass = b_success_total / n_b if n_b else 0
        delta_pass = (b_pass - a_pass) / a_pass * 100 if a_pass else 0

        # Count discordant pairs for McNemar
        n_ab_pass = 0  # A pass, B fail
        n_ba_pass = 0  # A fail, B pass
        for qi, query in enumerate(queries):
            a_ok = all(r.status == "completed" for r in all_results if r.query == query and r.variant_name == variant_a.name)
            b_ok = all(r.status == "completed" for r in all_results if r.query == query and r.variant_name == variant_b.name)
            if a_ok and not b_ok:
                n_ab_pass += 1
            if not a_ok and b_ok:
                n_ba_pass += 1

        mcnemar_p_val = mcnemar_p(n_ab_pass, n_ba_pass) if n_ab_pass + n_ba_pass > 0 else 1.0

        metrics.append(ABMetricComparison(
            metric_name="pass_rate",
            value_a=a_pass * 100,
            value_b=b_pass * 100,
            delta_pct=delta_pass,
            p_value=mcnemar_p_val,
            significant=mcnemar_p_val < 0.05,
            test_method="McNemar" if n_ab_pass + n_ba_pass >= 5 else "exact_binomial",
            direction="higher_is_better",
            interpretation=_significance_label(mcnemar_p_val),
        ))

        # 2. Latency
        a_latencies_all = [r.latency_ms for r in all_results if r.variant_name == variant_a.name]
        b_latencies_all = [r.latency_ms for r in all_results if r.variant_name == variant_b.name]
        if a_latencies_all and b_latencies_all:
            a_lat_mean = mean(a_latencies_all)
            b_lat_mean = mean(b_latencies_all)
            delta_lat = (b_lat_mean - a_lat_mean) / a_lat_mean * 100 if a_lat_mean else 0
            mw_p = mann_whitney_u_p(a_latencies_all, b_latencies_all)
            metrics.append(ABMetricComparison(
                metric_name="latency_mean",
                value_a=a_lat_mean,
                value_b=b_lat_mean,
                delta_pct=delta_lat,
                p_value=mw_p,
                significant=mw_p < 0.05,
                test_method="Mann-Whitney U",
                direction="lower_is_better",
                interpretation=_significance_label(mw_p),
            ))

            # P95 latency
            a_lat_sorted = sorted(a_latencies_all)
            b_lat_sorted = sorted(b_latencies_all)
            a_p95 = a_lat_sorted[int(len(a_lat_sorted) * 0.95)]
            b_p95 = b_lat_sorted[int(len(b_lat_sorted) * 0.95)]
            delta_p95 = (b_p95 - a_p95) / a_p95 * 100 if a_p95 else 0
            metrics.append(ABMetricComparison(
                metric_name="latency_p95",
                value_a=a_p95,
                value_b=b_p95,
                delta_pct=delta_p95,
                p_value=None,
                significant=False,
                test_method="descriptive",
                direction="lower_is_better",
                interpretation="",
            ))

        # 3. Judge scores
        judge_scores_a = [
            pq["judge_score_a"] for pq in per_query
            if pq["judge_score_a"] is not None
        ]
        judge_scores_b = [
            pq["judge_score_b"] for pq in per_query
            if pq["judge_score_b"] is not None
        ]
        if judge_scores_a and judge_scores_b and len(judge_scores_a) == len(judge_scores_b):
            a_judge_mean = mean(judge_scores_a)
            b_judge_mean = mean(judge_scores_b)
            delta_judge = (b_judge_mean - a_judge_mean) / a_judge_mean * 100 if a_judge_mean else 0
            diffs = [b - a for a, b in zip(judge_scores_a, judge_scores_b)]
            wilcox_p = wilcoxon_signed_rank_p(diffs) if len(diffs) >= 3 else 1.0
            metrics.append(ABMetricComparison(
                metric_name="judge_score",
                value_a=a_judge_mean,
                value_b=b_judge_mean,
                delta_pct=delta_judge,
                p_value=wilcox_p,
                significant=wilcox_p < 0.05,
                test_method="Wilcoxon signed-rank",
                direction="higher_is_better",
                interpretation=_significance_label(wilcox_p),
            ))

        # ── Conclusion ────────────────────────────────────────────
        sig_metrics = [m for m in metrics if m.significant]
        conclusion_parts: list[str] = []
        for m in sig_metrics:
            direction_word = "提升" if (
                (m.direction == "higher_is_better" and m.delta_pct > 0)
                or (m.direction == "lower_is_better" and m.delta_pct < 0)
            ) else "下降"
            conclusion_parts.append(
                f"{m.metric_name} {direction_word} {abs(m.delta_pct):.1f}% (p={m.p_value:.3f})"
            )
        if conclusion_parts:
            conclusion = f"Variants significantly differ on: {'; '.join(conclusion_parts)}. "
        else:
            conclusion = "No statistically significant difference detected. "

        # Winner determination
        if judge_scores_a and judge_scores_b:
            a_wins = sum(
                1 for pq in per_query
                if pq.get("judge_winner") == "A"
            )
            b_wins = sum(
                1 for pq in per_query
                if pq.get("judge_winner") == "B"
            )
            ties = sum(
                1 for pq in per_query
                if pq.get("judge_winner") == "tie"
            )
            if b_wins > a_wins:
                conclusion += f"LLM judge prefers variant B ({b_wins} wins vs {a_wins} wins, {ties} ties)."
            elif a_wins > b_wins:
                conclusion += f"LLM judge prefers variant A ({a_wins} wins vs {b_wins} wins, {ties} ties)."
            else:
                conclusion += f"LLM judge shows tie between variants ({a_wins} wins each, {ties} ties)."

        return ABTestReport(
            run_id=run_id,
            variant_a=variant_a,
            variant_b=variant_b,
            n_queries=len(queries),
            n_samples=n_samples,
            metrics=metrics,
            per_query_summary=per_query,
            conclusion=conclusion,
            raw_results=all_results,
        )


def _significance_label(p_value: float | None) -> str:
    """Human-readable significance label."""
    if p_value is None:
        return ""
    if p_value < 0.001:
        return "*** (p<0.001)"
    if p_value < 0.01:
        return "** (p<0.01)"
    if p_value < 0.05:
        return "* (p<0.05)"
    return "not significant"


def format_ab_report(report: ABTestReport) -> str:
    """Format ABTestReport as a human-readable text table."""
    lines: list[str] = []
    lines.append(
        f"A/B Test Report: {report.variant_a.name} vs {report.variant_b.name}"
    )
    lines.append("─" * 72)
    lines.append(
        f"{'Metric':<18} {'A':>10} {'B':>10} {'Δ%':>8} {'p-value':>8} {'Sig':>6}"
    )
    lines.append("─" * 72)

    for m in report.metrics:
        sig = ""
        if m.significant:
            sig = "***" if (m.p_value or 1) < 0.001 else (
                "**" if (m.p_value or 1) < 0.01 else "*"
            )
        lines.append(
            f"{m.metric_name:<18} "
            f"{m.value_a:>10.2f} "
            f"{m.value_b:>10.2f} "
            f"{m.delta_pct:>+7.1f}% "
            f"{_fmt_p(m.p_value):>8} "
            f"{sig:>6}"
        )

    lines.append("─" * 72)
    lines.append("")
    lines.append(f"n = {report.n_queries} queries × {report.n_samples} samples")
    lines.append("Test methods: " + ", ".join(
        f"{m.metric_name}: {m.test_method}" for m in report.metrics if m.test_method
    ))
    lines.append("")
    lines.append(f"Conclusion: {report.conclusion}")
    return "\n".join(lines)


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "   N/A"
    if p < 0.001:
        return "<0.001"
    if p < 0.01:
        return f"{p:.4f}"
    return f"{p:.3f}"
