# Audit And APM Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve deterministic skill routing so audit governance cases select `audit-sop`, APM metric knowledge cases select `apm-metrics`, and mixed patrol/audit cases can select both skills.

**Architecture:** Keep the behavior in `backend/src/personal_assistant/agent/router.py` because regex routing currently short-circuits later semantic and LLM stages. Tighten `audit-sop` to agent execution governance signals, add explicit APM metric knowledge patterns, and allow patrol routing to append audit governance when both intents are present.

**Tech Stack:** Python, pytest, existing `SkillRegistry` test helpers.

## Global Constraints

- Strict TDD: add failing router regression tests before production code changes.
- Keep changes scoped to routing metadata and skill descriptions.
- Preserve existing patrol-first behavior for alert rule execution cases.

---

### Task 1: Add Routing Regressions

**Files:**
- Modify: `backend/tests/test_router.py`

**Interfaces:**
- Consumes: `_keyword_route(registry, query) -> list[str]`
- Produces: regression coverage for `audit-sop`, `apm-metrics`, and `patrol` + `audit-sop`

- [ ] **Step 1: Write failing tests**

Add tests that assert:
- `帮我跑一次系统巡检，看看有没有异常指标。如果有的话，审计一下对应的执行日志看看根因是什么。` routes to `["patrol", "audit-sop"]`.
- `检查所有活跃线程的 SLA 合规情况：tool_success_rate < 95%、approval_response_time > 30s 的标记为不合规，输出合规报告。` routes to `["audit-sop"]`.
- `APM 里怎么定义和采集自定义业务指标？比如用户下单成功率、支付转化率这些。` routes to `["apm-metrics"]`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest backend/tests/test_router.py -k "audit_apm" -q`

Expected: FAIL because current routing misses or over-selects these skills.

### Task 2: Fix Deterministic Routing

**Files:**
- Modify: `backend/src/personal_assistant/agent/router.py`

**Interfaces:**
- Consumes: `_DEFAULT_SKILL_REGEXES`, `_route_patrol_intent`
- Produces: updated deterministic route selection

- [ ] **Step 1: Implement minimal routing changes**

Update regexes to:
- Add `apm-metrics` explicit patterns for metric definition, collection, Web Vitals, custom business metrics, conversion, SLO, error budget, percentiles, and metric interpretation.
- Remove broad Chinese `成功率|错误率` from generic `audit-sop` routing unless paired with agent governance terms like thread, execution log, approval, tool, compliance, security, token, SLA.
- Let `_route_patrol_intent` append `audit-sop` when audit governance intent is also present.

- [ ] **Step 2: Verify GREEN**

Run: `python -m pytest backend/tests/test_router.py -k "audit_apm" -q`

Expected: PASS.

### Task 3: Clarify Skill Descriptions

**Files:**
- Modify: `backend/src/personal_assistant/skills/audit-sop/SKILL.md`
- Modify: `backend/src/personal_assistant/skills/apm-metrics/SKILL.md`

**Interfaces:**
- Consumes: skill frontmatter descriptions and triggers used by semantic routing
- Produces: clearer semantic separation between audit governance and metric knowledge

- [ ] **Step 1: Edit descriptions**

Clarify:
- `audit-sop` is for agent execution logs, thread governance, tool retries, approval/security/token/SLA compliance.
- `apm-metrics` is for metric definitions, thresholds, collection schema, custom business metrics, SLO/error budget interpretation.

- [ ] **Step 2: Run targeted and full router tests**

Run: `python -m pytest backend/tests/test_router.py -q`

Expected: PASS.
