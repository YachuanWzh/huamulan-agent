# Extensible Routing Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad hoc skill-routing regex patches with a small, extensible deterministic routing rule layer that supports multi-intent queries, explainable traces, and priority-based suppression.

**Architecture:** Keep the existing regex -> semantic -> LLM funnel, but replace the flat `_DEFAULT_SKILL_REGEXES` matching path with typed `SkillRouteRule` objects and a `DeterministicRouteMatch` collection pass. Deterministic routing will collect all matching skill intents before deciding whether to short-circuit, then apply suppression policies such as "drop bare resolve-time trigger when a primary domain skill matched" while preserving explicit date-resolution matches. Prompt Guard will use typed prioritized rules so classification does not rely on tuple placement accidents.

**Tech Stack:** Python 3.14, pytest, existing LangGraph/LangChain stack, no new runtime dependencies.

## Global Constraints

- Use TDD: write failing tests before implementation changes.
- Keep changes isolated to the new worktree branch `codex/extensible-routing-rules`.
- Do not modify unrelated frontend files or main worktree dirty changes.
- Preserve the existing public router functions: `_keyword_route`, `route_skill_names`, and `route_skill_names_with_trace`.
- Update `README.md` and `技术方案报告.md` after code changes.

---

### Task 1: Multi-Intent Regression Coverage

**Files:**
- Modify: `backend/tests/test_router.py`

**Interfaces:**
- Consumes: existing `_keyword_route(registry, user_text) -> list[str]`.
- Produces: regression tests for multi-intent routing and deterministic trace match metadata.

- [ ] **Step 1: Write failing tests**

Add tests for `multi-004` and deterministic trace rule metadata.

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest backend\tests\test_router.py -k "multi_weather_api_performance_query or deterministic_route_trace" -q`

Expected: FAIL because current routing returns only `["weather"]` and trace lacks rule metadata.

### Task 2: Typed Skill Routing Rules

**Files:**
- Modify: `backend/src/personal_assistant/agent/router.py`
- Test: `backend/tests/test_router.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SkillRouteRule`
  - `@dataclass(frozen=True) class DeterministicRouteMatch`
  - `_deterministic_route(registry, user_text) -> tuple[list[str], list[dict]]`

- [ ] **Step 1: Implement minimal typed rule model**

Introduce route rule objects with `skill`, `rule_id`, `patterns`, `priority`, and `source`.

- [ ] **Step 2: Move existing regexes into rule objects**

Keep current matching behavior by expressing existing patterns as `SkillRouteRule` entries. Give explicit rules meaningful IDs such as `weather.basic`, `weather.air_quality`, `weather.temperature_detail`, `troubleshoot.api_performance`, `audit.security_events`, and `time.explicit_date_question`.

- [ ] **Step 3: Implement collection before suppression**

Collect all matching regex rules and trigger/token matches, then sort selected skills by priority and registry order. Do not return after the first matching skill.

- [ ] **Step 4: Preserve suppression policies**

Drop `resolve-time` trigger-only matches when a primary domain regex match exists, but keep `resolve-time` when its explicit date rule matches or when the weather rule has a future relative date requiring date resolution.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest backend\tests\test_router.py -k "multi_weather_api_performance_query or deterministic_route_trace or air_quality or temperature_swing or tool_failure_query or security_block_trend" -q`

Expected: PASS.

### Task 3: Prompt Guard Rule Objects

**Files:**
- Modify: `backend/src/personal_assistant/agent/harness.py`
- Test: `backend/tests/test_security_harness.py`

**Interfaces:**
- Produces: `PromptGuardRule` with `category`, `severity`, `reason`, `pattern`, and `priority`.
- Preserves: `scan_prompt_guard(message: str) -> GuardMatch | None`.

- [ ] **Step 1: Add rule dataclass and priority sorting**

Convert `_PROMPT_PATTERNS` from tuples to `PromptGuardRule` objects. Scan in sorted priority order, then declaration order.

- [ ] **Step 2: Keep existing categories and newer safety regressions**

Retain instruction override, system prompt leak, role-play jailbreak, and identity spoof coverage including safety-prompt-009 and safety-prompt-010.

- [ ] **Step 3: Run safety tests**

Run: `python -m pytest backend\tests\test_security_harness.py -q`

Expected: PASS.

### Task 4: Documentation

**Files:**
- Modify: `README.md`
- Modify: `技术方案报告.md`

**Interfaces:**
- Produces: documented routing architecture and maintenance guidance.

- [ ] **Step 1: Update README**

Document deterministic rule IDs, multi-intent collection, suppression policy, and prompt guard priority.

- [ ] **Step 2: Update technical report**

Add an architecture subsection explaining rule objects, trace output, and how to add future skill intents without patching core logic.

- [ ] **Step 3: Verify docs mention new terms**

Run: `rg -n "SkillRouteRule|rule_id|multi-intent|PromptGuardRule|suppression" README.md 技术方案报告.md`

Expected: all key concepts are present.

### Task 5: Final Verification

**Files:**
- Verify only.

**Interfaces:**
- Consumes all previous tasks.

- [ ] **Step 1: Run targeted router and security tests**

Run: `python -m pytest backend\tests\test_router.py backend\tests\test_security_harness.py -q`

If pre-existing caplog failures remain, record them clearly and run narrower behavior tests that exclude known baseline log-capture assertions.

- [ ] **Step 2: Run real registry probes**

Check:

```text
multi-004 -> ["resolve-time", "weather", "troubleshoot"]
w-021 -> ["weather"]
w-024 -> ["weather"]
au-017 -> ["audit-sop"]
au-018 -> ["audit-sop"]
safety-prompt-009 -> instruction_override
safety-prompt-010 -> identity_spoof
```

- [ ] **Step 3: Review diff**

Run: `git diff --stat` and inspect scoped files.
