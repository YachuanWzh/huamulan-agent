# Multi-Agent Quick Check Evaluation 实现计划

> **For agentic workers:** Execute this plan task-by-task under the superharness:go workflow, Phase 2 (strict TDD per task). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让快检（quick evaluation）支持 multi-agent 模式，使用 `rewrite_query_and_slots()` 做路由+槽位检测，对比 `expected_intent` 而非 `expected_skills`，使测评结果落到具体子 agent 上。

**Architecture:** 当前快检 (`_run_quick_case`) 仅调用单 agent 三层路由 (`route_skill_names_with_trace`)。需在 `agent_mode="multi"` 时分叉：调用 `rewrite_query_and_slots()` 做意图+槽位提取，新增 `expected_intent` 字段到 Golden 数据集，诊断层增加 multi-agent 专用 checks（意图匹配、实体提取、指标识别），离线评估增加 `evaluate_multi_agent_intent_cases()` 函数。

**Tech Stack:** Python 3, Pydantic, pytest, langgraph

**核心概念映射:**
- Single-agent: `query → route_skill_names() → selected_skills` vs `expected_skills`
- Multi-agent: `query → rewrite_query_and_slots() → intent_slots {intent, metrics, entities}` vs `expected_intent` + 新增 `expected_metrics`/`expected_entities`

---

### Task 1: 扩展 GoldenSkillCase 模型，增加 multi-agent 期望字段

**Files:**
- Modify: `backend/src/personal_assistant/skills/evaluation/models.py`

- [ ] **Step 1: 添加 `expected_intent` 字段**

```python
class GoldenSkillCase(BaseModel):
    id: str
    query: str
    category: str | None = None
    difficulty: str = "medium"
    expected_skills: list[str] = Field(default_factory=list)
    negative_skills: list[str] = Field(default_factory=list)
    expected_tool: str | None = None
    expected_args: dict[str, Any] = Field(default_factory=dict)
    expected_tool_calls: list["ToolCallExpectation"] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_behavior: str | None = None
    expected_answer_contains: list[str] = Field(default_factory=list)
    forbidden_answer_contains: list[str] = Field(default_factory=list)
    expected_security_event: str | None = None
    judge_rubric: str | None = None
    # Multi-agent fields:
    expected_intent: str | None = None  # metrics | troubleshoot | patrol | audit | general
    expected_metrics: list[str] = Field(default_factory=list)  # e.g. ["p95", "LCP"]
    expected_entities: list[str] = Field(default_factory=list)  # e.g. ["checkout", "api"]
```

- [ ] **Step 2: 运行已有测试确认向后兼容**

```bash
cd backend && python -m pytest tests/test_agent_evaluation_models.py -v
```

Expected: PASS（所有已有测试通过，新字段有默认值不影响已有 Golden 数据）

- [ ] **Step 3: Commit**

```bash
git add backend/src/personal_assistant/skills/evaluation/models.py
git commit -m "feat(evaluation): add multi-agent expected fields to GoldenSkillCase

Add expected_intent, expected_metrics, and expected_entities fields
for multi-agent routing evaluation. All fields have defaults for
backward compatibility with existing single-agent golden datasets.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 新增 multi-agent 意图路由评估函数

**Files:**
- Create: `backend/tests/test_multi_agent_evaluation.py`
- Modify: `backend/src/personal_assistant/skills/evaluation/offline.py`

- [ ] **Step 1: 写失败测试 — multi-agent intent evaluation**

```python
# backend/tests/test_multi_agent_evaluation.py
import pytest
from personal_assistant.skills.evaluation.models import GoldenSkillCase, MultiAgentRoutingMetrics
from personal_assistant.skills.evaluation.offline import evaluate_multi_agent_intent_cases


def make_case(id: str, query: str, intent: str | None = None,
              metrics: list[str] | None = None,
              entities: list[str] | None = None) -> GoldenSkillCase:
    return GoldenSkillCase(
        id=id, query=query,
        expected_intent=intent,
        expected_metrics=metrics or [],
        expected_entities=entities or [],
    )


class TestMultiAgentIntentEvaluation:
    def test_exact_intent_match_returns_perfect_accuracy(self):
        cases = [
            make_case("m1", "排查 checkout API p95 超时并给出 RCA", intent="troubleshoot",
                       metrics=["p95"], entities=["checkout", "api"]),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.total_cases == 1
        assert result.intent_accuracy == 1.0

    def test_wrong_intent_detected(self):
        cases = [
            make_case("m2", "帮我检查巡检规则", intent="patrol"),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.intent_accuracy is not None
        # "检查巡检规则" -> contains "巡检" -> intent should be "patrol"
        assert result.intent_accuracy == 1.0

    def test_empty_cases_returns_none_metrics(self):
        result = evaluate_multi_agent_intent_cases([])

        assert result.total_cases == 0
        assert result.intent_accuracy is None

    def test_metric_extraction_fidelity(self):
        cases = [
            make_case("m3", "LCP p95 从 2.4s 涨到 6.7s", intent="troubleshoot",
                       metrics=["p95", "lcp"]),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.metric_extraction_recall is not None
        assert result.metric_extraction_recall == 1.0  # both p95 and lcp found

    def test_entity_extraction_fidelity(self):
        cases = [
            make_case("m4", "checkout API 超时", intent="troubleshoot",
                       entities=["checkout", "api"]),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.entity_extraction_recall == 1.0

    def test_no_expected_intent_skips_case(self):
        """Cases without expected_intent are ignored in multi-agent eval."""
        cases = [
            GoldenSkillCase(id="old", query="hello", expected_skills=["weather"]),
        ]
        result = evaluate_multi_agent_intent_cases(cases)

        assert result.total_cases == 0
        assert result.intent_accuracy is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py -v
```

Expected: FAIL — `evaluate_multi_agent_intent_cases` 未定义，`MultiAgentRoutingMetrics` 未定义

- [ ] **Step 3: 添加 `MultiAgentRoutingMetrics` 模型并实现 `evaluate_multi_agent_intent_cases`**

```python
# In models.py, add after RoutingMetrics:
class MultiAgentRoutingMetrics(BaseModel):
    total_cases: int
    intent_accuracy: float | None = None
    intent_precision: float | None = None
    intent_recall: float | None = None
    intent_f1: float | None = None
    metric_extraction_recall: float | None = None
    entity_extraction_recall: float | None = None


# In offline.py, add:
def evaluate_multi_agent_intent_cases(
    cases: list[GoldenSkillCase],
) -> MultiAgentRoutingMetrics:
    """Evaluate multi-agent intent routing against expected intent/slots."""
    from personal_assistant.agent.multi_agent import rewrite_query_and_slots
    from personal_assistant.skills.evaluation.models import MultiAgentRoutingMetrics

    relevant = [c for c in cases if c.expected_intent is not None]
    if not relevant:
        return MultiAgentRoutingMetrics(total_cases=0)

    intent_correct = 0
    total_metric_precision = 0
    total_metric_recall = 0
    total_entity_precision = 0
    total_entity_recall = 0
    intent_tp = 0
    intent_fp = 0
    intent_fn = 0

    for case in relevant:
        payload = rewrite_query_and_slots(case.query)
        actual_intent = payload["slots"].get("intent", "general")
        expected_intent = case.expected_intent

        # Intent match
        if actual_intent == expected_intent:
            intent_correct += 1
            intent_tp += 1
        else:
            intent_fn += 1
            intent_fp += 1

        # Metric recall: did we catch all expected metrics?
        actual_metrics = set(payload["slots"].get("metrics", []))
        expected_metrics_set = set(case.expected_metrics)
        if expected_metrics_set:
            metric_recall = len(actual_metrics & expected_metrics_set) / len(expected_metrics_set)
            total_metric_recall += metric_recall
            total_metric_precision += (
                len(actual_metrics & expected_metrics_set) / len(actual_metrics)
                if actual_metrics else 1.0
            )

        # Entity recall
        actual_entities = set(payload["slots"].get("entities", []))
        expected_entities_set = set(case.expected_entities)
        if expected_entities_set:
            entity_recall = len(actual_entities & expected_entities_set) / len(expected_entities_set)
            total_entity_recall += entity_recall
            total_entity_precision += (
                len(actual_entities & expected_entities_set) / len(actual_entities)
                if actual_entities else 1.0
            )

    n = len(relevant)
    precision = intent_tp / (intent_tp + intent_fp) if (intent_tp + intent_fp) > 0 else None
    recall = intent_tp / (intent_tp + intent_fn) if (intent_tp + intent_fn) > 0 else None

    return MultiAgentRoutingMetrics(
        total_cases=n,
        intent_accuracy=intent_correct / n,
        intent_precision=precision,
        intent_recall=recall,
        intent_f1=_f1(precision, recall),
        metric_extraction_recall=total_metric_recall / n if n > 0 else None,
        entity_extraction_recall=total_entity_recall / n if n > 0 else None,
    )
```

- [ ] **Step 4: 更新 `__init__.py` 导出**

```python
# In __init__.py, add to imports:
from personal_assistant.skills.evaluation.models import MultiAgentRoutingMetrics
from personal_assistant.skills.evaluation.offline import evaluate_multi_agent_intent_cases

# Add to __all__:
"MultiAgentRoutingMetrics",
"evaluate_multi_agent_intent_cases",
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py -v
```

Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_multi_agent_evaluation.py backend/src/personal_assistant/skills/evaluation/offline.py backend/src/personal_assistant/skills/evaluation/models.py backend/src/personal_assistant/skills/evaluation/__init__.py
git commit -m "feat(evaluation): add multi-agent intent routing evaluation

Add evaluate_multi_agent_intent_cases() and MultiAgentRoutingMetrics
for evaluating multi-agent intent classification, metric extraction,
and entity extraction against golden datasets.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 修改快检 `_run_quick_case` 支持 multi-agent 模式

**Files:**
- Modify: `backend/src/personal_assistant/api/server.py`

- [ ] **Step 1: 写失败测试**

```python
# In backend/tests/test_multi_agent_evaluation.py, add:
from unittest.mock import patch, MagicMock
import asyncio


class TestQuickCaseMultiAgent:
    def test_run_quick_case_multi_agent_uses_rewrite_query(self):
        """When agent_mode='multi', _run_quick_case should use rewrite_query_and_slots."""
        from personal_assistant.api.server import _run_quick_case
        from personal_assistant.skills import SkillRegistry

        registry = SkillRegistry.__new__(SkillRegistry)
        registry.skills = {}

        case = GoldenSkillCase(
            id="m-test",
            query="排查 checkout API p95 超时并给出 RCA",
            expected_intent="troubleshoot",
            expected_metrics=["p95"],
            expected_entities=["checkout"],
        )

        outcome = asyncio.run(_run_quick_case(registry, case, agent_mode="multi"))

        # Multi-agent output should have intent_slots, not selected_skills from route_skill_names
        assert "intent_slots" in outcome
        assert outcome["intent_slots"]["intent"] == "troubleshoot"
        # selected_skills should be empty for multi-agent (no skill routing)
        assert outcome["selected_skills"] == []

    def test_run_quick_case_still_works_for_single_agent(self):
        """Single-agent mode should still use route_skill_names_with_trace."""
        from personal_assistant.api.server import _run_quick_case
        from personal_assistant.skills import SkillRegistry

        registry = SkillRegistry.__new__(SkillRegistry)
        registry.skills = {}

        case = GoldenSkillCase(
            id="s-test",
            query="今天天气怎么样",
            expected_skills=["weather"],
        )

        outcome = asyncio.run(_run_quick_case(registry, case, agent_mode="single"))

        # Single-agent output should have selected_skills
        assert "selected_skills" in outcome
        assert "routing_trace" in outcome
        # intent_slots should not be present in single-agent mode
        assert "intent_slots" not in outcome
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py::TestQuickCaseMultiAgent -v
```

Expected: FAIL — `_run_quick_case` 不支持 `agent_mode` 参数

- [ ] **Step 3: 修改 `_run_quick_case` 实现**

```python
# In server.py, modify _run_quick_case:
async def _run_quick_case(registry: SkillRegistry, case: GoldenSkillCase, guard_llm=None, *, agent_mode: str = "single") -> dict:
    query = _case_query(case)
    logs: list[dict] = []
    # Layer 1: 正则快速拦截
    guard_match = scan_prompt_guard(query)
    # Layer 2: LLM语义安全判定
    if not guard_match and guard_llm is not None:
        guard_match = await scan_prompt_guard_with_llm(query, guard_llm)
    if guard_match:
        logs.append({
            "event_type": "security",
            "status": "blocked",
            "name": guard_match.category,
            "input": {"message": query[:200]},
            "error": {"reason": guard_match.reason},
            "metadata": {"severity": guard_match.severity, "source": f"{guard_match.source}_prompt_guard"},
        })
        return {
            "case": case,
            "selected_skills": [],
            "logs": logs,
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

    if agent_mode == "multi":
        # Multi-agent: use rewrite_query_and_slots for intent+slot routing
        from personal_assistant.agent.multi_agent import rewrite_query_and_slots
        payload = rewrite_query_and_slots(query)
        return {
            "case": case,
            "selected_skills": [],
            "intent_slots": payload["slots"],
            "rewritten_query": payload["rewritten_query"],
            "logs": logs,
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

    # 快检模式使用完整三层漏斗路由：正则→语义检索→LLM判定
    try:
        routing = await route_skill_names_with_trace(registry, query, **quick_eval_router_kwargs)
    except Exception as exc:
        logger.warning("Quick evaluation full routing failed, falling back to regex only: %s", exc)
        routing = await route_skill_names_with_trace(registry, query)
    return {
        "case": case,
        "selected_skills": routing.selected_skills,
        "routing_trace": routing.trace,
        "logs": logs,
        "final_answer": "",
        "tool_names": [],
        "tool_calls": [],
        "tool_completed": False,
        "tool_failed": False,
    }
```

- [ ] **Step 4: 修改调用处传递 `agent_mode`**

在 `_iter_skill_evaluation_events` 中，`_run_quick_case` 调用处传入 `agent_mode`:

```python
# Line ~514 in server.py, change:
outcome = await _run_quick_case(registry, case, guard_llm=quick_guard_llm)
# To:
outcome = await _run_quick_case(registry, case, guard_llm=quick_guard_llm, agent_mode=agent_mode)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py::TestQuickCaseMultiAgent -v
```

Expected: PASS (2 tests)

- [ ] **Step 6: 运行全部已有测试确认向后兼容**

```bash
cd backend && python -m pytest tests/test_agent_evaluation_models.py tests/test_skill_evaluation.py tests/test_multi_agent_contract.py -v
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/personal_assistant/api/server.py backend/tests/test_multi_agent_evaluation.py
git commit -m "feat(evaluation): support multi-agent mode in quick-check

_run_quick_case now accepts agent_mode parameter. When 'multi',
uses rewrite_query_and_slots() for intent+slot routing instead
of single-agent three-layer skill routing.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 扩展诊断层支持 multi-agent checks

**Files:**
- Modify: `backend/src/personal_assistant/skills/evaluation/diagnostics.py`
- Modify: `backend/tests/test_multi_agent_evaluation.py`

- [ ] **Step 1: 写失败测试**

```python
# In test_multi_agent_evaluation.py, add:
class TestMultiAgentDiagnostics:
    def test_build_detail_for_multi_agent_checks_intent(self):
        from personal_assistant.skills.evaluation.diagnostics import build_case_evaluation_detail
        from personal_assistant.agent.multi_agent import rewrite_query_and_slots

        case = GoldenSkillCase(
            id="m-diag-1",
            query="排查 checkout API p95 超时",
            expected_intent="troubleshoot",
            expected_metrics=["p95"],
            expected_entities=["checkout", "api"],
        )
        payload = rewrite_query_and_slots(case.query)
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": payload["slots"],
            "rewritten_query": payload["rewritten_query"],
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        # Should have multi-agent specific checks
        check_names = [c.name for c in detail.checks]
        assert "intent_match" in check_names

    def test_build_detail_intent_mismatch_marks_fail(self):
        from personal_assistant.skills.evaluation.diagnostics import build_case_evaluation_detail

        case = GoldenSkillCase(
            id="m-diag-2",
            query="帮我执行一次巡检",
            expected_intent="patrol",
        )
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": {"intent": "patrol", "domain": "apm", "metrics": [], "entities": [], "requires_user_vector_context": True},
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        # Intent matches -> should pass
        intent_check = next(c for c in detail.checks if c.name == "intent_match")
        assert intent_check.passed is True

    def test_build_detail_intent_wrong_marks_fail(self):
        from personal_assistant.skills.evaluation.diagnostics import build_case_evaluation_detail

        case = GoldenSkillCase(
            id="m-diag-3",
            query="帮我查下指标",
            expected_intent="troubleshoot",
        )
        outcome = {
            "case": case,
            "selected_skills": [],
            "intent_slots": {"intent": "metrics", "domain": "apm", "metrics": [], "entities": [], "requires_user_vector_context": True},
            "logs": [],
            "final_answer": "",
            "tool_names": [],
            "tool_calls": [],
            "tool_completed": False,
            "tool_failed": False,
        }

        detail = build_case_evaluation_detail(case, outcome, mode="quick")

        intent_check = next(c for c in detail.checks if c.name == "intent_match")
        assert intent_check.passed is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py::TestMultiAgentDiagnostics -v
```

Expected: FAIL — 诊断层没有 `intent_match` check

- [ ] **Step 3: 修改 `_build_checks` 添加 multi-agent checks**

```python
# In diagnostics.py, add after existing _build_checks content, before "if mode != 'e2e':":
    # Multi-agent intent checks
    intent_slots = outcome.get("intent_slots")
    if intent_slots is not None and isinstance(intent_slots, dict):
        actual_intent = intent_slots.get("intent", "general")
        if case.expected_intent is not None:
            intent_passed = actual_intent == case.expected_intent
            checks.append(
                EvaluationCheck(
                    name="intent_match",
                    stage="routing",
                    passed=intent_passed,
                    expected=case.expected_intent,
                    actual=actual_intent,
                    reason="" if intent_passed else f"Expected intent '{case.expected_intent}', got '{actual_intent}'",
                )
            )
        if case.expected_metrics:
            actual_metrics = set(intent_slots.get("metrics", []))
            expected_metrics_set = set(case.expected_metrics)
            extra = sorted(actual_metrics - expected_metrics_set)
            missing = sorted(expected_metrics_set - actual_metrics)
            metric_passed = not missing  # recall-oriented check
            checks.append(
                EvaluationCheck(
                    name="metric_extraction",
                    stage="routing",
                    passed=metric_passed,
                    expected={"required": case.expected_metrics},
                    actual={"missing": missing, "extra": extra} if not metric_passed else {"matched": sorted(actual_metrics & expected_metrics_set)},
                    reason="" if metric_passed else f"Missing expected metrics: {', '.join(missing)}",
                )
            )
        if case.expected_entities:
            actual_entities = set(intent_slots.get("entities", []))
            expected_entities_set = set(case.expected_entities)
            missing = sorted(expected_entities_set - actual_entities)
            entity_passed = not missing
            checks.append(
                EvaluationCheck(
                    name="entity_extraction",
                    stage="routing",
                    passed=entity_passed,
                    expected={"required": case.expected_entities},
                    actual={"missing": missing} if not entity_passed else {"matched": sorted(actual_entities & expected_entities_set)},
                    reason="" if entity_passed else f"Missing expected entities: {', '.join(missing)}",
                )
            )
```

- [ ] **Step 4: 修改 `build_case_evaluation_detail` 处理 multi-agent 的 P/R/F1**

```python
# In build_case_evaluation_detail, after the existing per-case P/R/F1 calculation block,
# add multi-agent specific score calculation:
    # For multi-agent mode, calculate intent + slot scores instead of skill P/R/F1
    intent_slots = outcome.get("intent_slots")
    if intent_slots is not None and case.expected_intent is not None:
        # Reuse precision/recall/f1 fields for intent accuracy
        actual_intent = intent_slots.get("intent", "general")
        precision = 1.0 if actual_intent == case.expected_intent else 0.0
        recall = precision  # binary match
        f1 = precision
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py::TestMultiAgentDiagnostics -v
```

Expected: PASS (3 tests)

- [ ] **Step 6: 运行全部已有测试确认向后兼容**

```bash
cd backend && python -m pytest tests/test_agent_evaluation_diagnostics.py tests/test_skill_evaluation.py -v
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/personal_assistant/skills/evaluation/diagnostics.py backend/tests/test_multi_agent_evaluation.py
git commit -m "feat(evaluation): add multi-agent intent checks to diagnostics

Build intent_match, metric_extraction, and entity_extraction checks
when intent_slots is present in the outcome. Compute per-case P/R/F1
for multi-agent intent accuracy.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 更新 APM golden 数据集，加入 `expected_intent`

**Files:**
- Modify: `backend/evaluation/golden/apm_realistic.jsonl`

- [ ] **Step 1: 给 apm_realistic.jsonl 每条数据添加 `expected_intent`、`expected_metrics`、`expected_entities`**

修改后的 `apm_realistic.jsonl`:

```jsonl
{"id":"apm-real-001","category":"apm_troubleshooting","difficulty":"hard","query":"生产发布 2026.07.04-rc.3 后 /checkout 页面 TypeError 集中爆发，LCP p95 从 2.4s 涨到 6.7s，cart_v2 feature flag 刚放量到 30%。请结合 RUM 和执行日志做 RCA 根因分析，给出修复和验证步骤。","expected_skills":["apm-metrics","troubleshoot"],"expected_intent":"troubleshoot","expected_metrics":["p95","lcp"],"expected_entities":["checkout","cart_v2"],"expected_tool_calls":[{"tool":"analyze_apm_incident"}],"expected_answer_contains":["Root Cause","LCP","TypeError","cart_v2","Verification"],"fixture":"evaluation/fixtures/apm_realistic/checkout_release_regression.json"}
{"id":"apm-real-002","category":"apm_runbook","difficulty":"hard","query":"刚发布后华东 CDN 区域大量用户白屏，/assets/checkout.chunk.8f32a1.js 返回 404。请按 resource failure runbook 排查 CDN、manifest、缓存头和回滚方案。","expected_skills":["troubleshoot-runbook"],"expected_intent":"troubleshoot","expected_metrics":[],"expected_entities":["cdn","checkout"],"expected_answer_contains":["Resource Failure","CDN","manifest","rollback"],"fixture":"evaluation/fixtures/apm_realistic/cdn_chunk_404.json"}
{"id":"apm-real-003","category":"apm_troubleshooting","difficulty":"hard","query":"订单详情页 TTFB p95 从 420ms 涨到 2.8s，/api/orders/detail p95 变成 4.6s，并且 query_order_detail 出现 retry chain。帮我做根因分析，看是不是 DB 慢查询或缓存命中率下降。","expected_skills":["apm-metrics","troubleshoot"],"expected_intent":"troubleshoot","expected_metrics":["p95","ttfb"],"expected_entities":["orders","query_order_detail"],"expected_tool_calls":[{"tool":"analyze_apm_incident"}],"expected_answer_contains":["TTFB","p95","retry","DB","cache"],"fixture":"evaluation/fixtures/apm_realistic/orders_api_slow_dependency.json"}
{"id":"apm-real-004","category":"apm_runbook","difficulty":"hard","query":"第三方支付回调 30s 超时，订单状态出现 paid_pending 和 paid_success 不一致。请按 APM runbook 给出排查步骤、降级策略和回滚方案。","expected_skills":["troubleshoot-runbook"],"expected_intent":"troubleshoot","expected_metrics":[],"expected_entities":["payment","callback"],"expected_answer_contains":["timeout","third-party","rollback","verification"],"fixture":"evaluation/fixtures/apm_realistic/payment_callback_timeout.json"}
{"id":"apm-real-005","category":"apm_troubleshooting","difficulty":"hard","query":"React dashboard 切换路由 20 分钟后 heap_used_mb 持续上涨，INP p95 到 680ms，怀疑 Memory Leak。请做 RCA 并指出要检查的 listeners、timers 和缓存。","expected_skills":["apm-metrics","troubleshoot","troubleshoot-runbook"],"expected_intent":"troubleshoot","expected_metrics":["p95","inp"],"expected_entities":["dashboard","heap","react"],"expected_tool_calls":[{"tool":"analyze_apm_incident"}],"expected_answer_contains":["Memory Leak","heap","INP","listeners","timers"],"fixture":"evaluation/fixtures/apm_realistic/dashboard_memory_leak.json"}
{"id":"apm-real-006","category":"apm_troubleshooting","difficulty":"medium","query":"WebSocket 网关 10 分钟内断连率到 18%，客户端 reconnect attempts p95=7，在线客服消息延迟明显。帮我做 RCA，判断是 gateway、网络区域还是客户端重连策略问题。","expected_skills":["apm-metrics","troubleshoot"],"expected_intent":"troubleshoot","expected_metrics":["p95"],"expected_entities":["websocket","gateway","reconnect"],"expected_tool_calls":[{"tool":"analyze_apm_incident"}],"expected_answer_contains":["WebSocket","reconnect","gateway","region"],"fixture":"evaluation/fixtures/apm_realistic/websocket_reconnect_storm.json"}
{"id":"apm-real-007","category":"apm_patrol","difficulty":"hard","query":"帮我执行一次闭环巡检：检查 LCP>4000ms、JS error rate>5%、api_timeout_retry>3、Redis hit rate<90%。先输出 pass/fail，再对失败项做 RCA。","expected_skills":["patrol","troubleshoot"],"expected_intent":"patrol","expected_metrics":["lcp"],"expected_entities":["redis"],"expected_tool_calls":[{"tool":"run_patrol_checks"},{"tool":"analyze_apm_incident"}],"expected_answer_contains":["pass","fail","巡检","RCA"],"fixture":"evaluation/fixtures/apm_realistic/closed_loop_patrol.json"}
{"id":"apm-real-008","category":"apm_knowledge","difficulty":"medium","query":"APM 里怎么定义和采集下单成功率、支付转化率和优惠券使用率？请给出 numerator、denominator、去重规则、维度和告警阈值。","expected_skills":["apm-metrics"],"expected_intent":"metrics","expected_metrics":[],"expected_entities":["conversion"],"expected_answer_contains":["numerator","denominator","dedupe","conversion","threshold"],"fixture":"evaluation/fixtures/apm_realistic/business_metrics_design.json"}
{"id":"apm-real-009","category":"governance_audit","difficulty":"hard","query":"检查所有活跃线程的 SLA 合规情况：tool_success_rate < 95%、approval_response_time > 30s、安全拦截率异常、token 单轮增长 > 40% 的标记为不合规，输出治理审计报告。","expected_skills":["audit-sop"],"expected_intent":"audit","expected_metrics":[],"expected_entities":["sla","compliance"],"expected_answer_contains":["SLA","compliance","tool_success_rate","approval","token"],"fixture":"evaluation/fixtures/apm_realistic/governance_sla_audit.json"}
```

- [ ] **Step 2: 运行测试确认 golden 数据解析兼容**

```bash
cd backend && python -m pytest tests/test_skill_evaluation.py -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/evaluation/golden/apm_realistic.jsonl
git commit -m "feat(evaluation): add expected_intent to APM golden dataset

Add expected_intent, expected_metrics, and expected_entities fields
to all apm_realistic golden cases for multi-agent evaluation support.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 集成报告层 multi-agent routing metrics

**Files:**
- Modify: `backend/src/personal_assistant/skills/evaluation/report.py`
- Modify: `backend/src/personal_assistant/api/server.py`

- [ ] **Step 1: 更新 `render_markdown_report` 支持 multi-agent metrics**

```python
# In report.py, add after routing section in render_markdown_report:
    if report.multi_agent_routing is not None:
        lines.extend(
            [
                "## Multi-Agent Routing",
                f"- Total Cases: {report.multi_agent_routing.total_cases}",
                f"- Intent Accuracy: {_fmt_rate(report.multi_agent_routing.intent_accuracy)}",
                f"- Intent Precision: {_fmt_rate(report.multi_agent_routing.intent_precision)}",
                f"- Intent Recall: {_fmt_rate(report.multi_agent_routing.intent_recall)}",
                f"- Intent F1: {_fmt_rate(report.multi_agent_routing.intent_f1)}",
                f"- Metric Extraction Recall: {_fmt_rate(report.multi_agent_routing.metric_extraction_recall)}",
                f"- Entity Extraction Recall: {_fmt_rate(report.multi_agent_routing.entity_extraction_recall)}",
                "",
            ]
        )
```

- [ ] **Step 2: 在 `SkillEvaluationReport` 模型中加入 `multi_agent_routing` 字段**

```python
# In models.py, add to SkillEvaluationReport:
class SkillEvaluationReport(BaseModel):
    skills: list[SkillEvaluationResult]
    routing: RoutingMetrics | None = None
    multi_agent_routing: MultiAgentRoutingMetrics | None = None  # NEW
    safety: SafetyEvaluationMetrics | None = None
    ...
```

- [ ] **Step 3: 在快检流程中执行 multi-agent evaluation 并填入 report**

```python
# In _iter_skill_evaluation_events, add after existing routing evaluation:
    ma_routing = None
    if agent_mode == "multi":
        ma_routing = evaluate_multi_agent_intent_cases(cases)

    report = SkillEvaluationReport(
        skills=results,
        routing=routing_metrics if agent_mode == "single" else None,
        multi_agent_routing=ma_routing,
        safety=...,
        ...
    )
```

- [ ] **Step 4: 运行测试确认完整流程**

```bash
cd backend && python -m pytest tests/test_multi_agent_evaluation.py tests/test_skill_evaluation.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/personal_assistant/skills/evaluation/report.py backend/src/personal_assistant/skills/evaluation/models.py backend/src/personal_assistant/api/server.py
git commit -m "feat(evaluation): integrate multi-agent routing metrics into report

Add MultiAgentRoutingMetrics to SkillEvaluationReport, render
multi-agent section in markdown report, and execute multi-agent
evaluation when agent_mode='multi'.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 端到端验证 — 运行完整测试套件

- [ ] **Step 1: 运行全部后端测试**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: ALL PASS

- [ ] **Step 2: 运行全部前端测试**

```bash
cd frontend && npx vitest run
```

Expected: ALL PASS

- [ ] **Step 3: 如有失败，用 systematic-debugging 定位修复**

---

## 变更总结

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/src/personal_assistant/skills/evaluation/models.py` | Modify | 添加 `expected_intent`, `expected_metrics`, `expected_entities` 到 `GoldenSkillCase`；新增 `MultiAgentRoutingMetrics`；`SkillEvaluationReport` 加 `multi_agent_routing` |
| `backend/src/personal_assistant/skills/evaluation/__init__.py` | Modify | 导出新模型和函数 |
| `backend/src/personal_assistant/skills/evaluation/offline.py` | Modify | 新增 `evaluate_multi_agent_intent_cases()` |
| `backend/src/personal_assistant/skills/evaluation/diagnostics.py` | Modify | `_build_checks` 添加 `intent_match`, `metric_extraction`, `entity_extraction` checks |
| `backend/src/personal_assistant/skills/evaluation/report.py` | Modify | `render_markdown_report` 渲染 multi-agent routing section |
| `backend/src/personal_assistant/api/server.py` | Modify | `_run_quick_case` 支持 `agent_mode`；`_iter_skill_evaluation_events` 集成 multi-agent eval |
| `backend/evaluation/golden/apm_realistic.jsonl` | Modify | 添加 `expected_intent`, `expected_metrics`, `expected_entities` |
| `backend/tests/test_multi_agent_evaluation.py` | Create | 所有新增功能的测试 |
