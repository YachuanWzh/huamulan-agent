# Patrol Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve skill routing so APM patrol requests select `patrol`, and mixed patrol/RCA requests select both `patrol` and `troubleshoot`.

**Architecture:** Keep the fix in deterministic routing because current regex routing short-circuits semantic and LLM fallback. Add targeted APM patrol regexes and tighten discovery routing so capability-install queries still select `find-skills` while business tasks containing the word "skill" do not steal routing.

**Tech Stack:** Python, pytest, existing `personal_assistant.agent.router` regex routing.

## Global Constraints

Strict TDD: write failing tests first, verify red, implement minimal code, verify green.

---

### Task 1: Add Patrol Routing Regression Tests

**Files:**
- Modify: `backend/tests/test_router.py`

**Interfaces:**
- Consumes: `_keyword_route(registry, query) -> list[str]`
- Produces: regression coverage for `patrol` and `patrol` + `troubleshoot`

- [x] **Step 1: Write failing tests**

```python
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
        for name in ("patrol", "troubleshoot", "apm-metrics", "audit-sop", "troubleshoot-runbook"):
            _make_named_skill(tmp_path, name)
        registry = SkillRegistry(tmp_path)

        result = _keyword_route(
            registry,
            "夜间巡检发现 LCP p95、JS error rate、tool retry ratio 都异常，请先做自动巡检，再触发智能排障分析根因。",
        )

        assert result == ["patrol", "troubleshoot"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_router.py::TestPatrolRouting -q`
Expected: FAIL because routing does not yet return the expected patrol selections.

- [x] **Step 3: Implement minimal deterministic routing fix**

Modify `backend/src/personal_assistant/agent/router.py`:
- Add patrol regexes for Chinese patrol, alert rule, scheduled check, anomaly finding, and English metric rule shapes.
- Add troubleshoot regex coverage for Chinese root-cause/troubleshooting language.
- Prefer explicit regex/triggers and remove broad token fallback from `_regex_route`.

- [x] **Step 4: Run targeted tests**

Run: `cd backend; uv run pytest tests/test_router.py::TestPatrolRouting -q`
Expected: PASS.

- [x] **Step 5: Run router regression tests**

Run: `cd backend; uv run pytest tests/test_router.py -q`
Expected: PASS.
