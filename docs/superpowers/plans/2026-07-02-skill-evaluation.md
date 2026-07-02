# Skill Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backend Skill evaluation capability that produces offline and online quality metrics through one shared report model.

**Architecture:** Add a focused `personal_assistant.skills.evaluation` package. The package reads golden JSONL cases, calls the existing Skill router, computes static Skill metrics from files, aggregates runtime metrics from existing execution-log records, and renders JSON/Markdown reports.

**Tech Stack:** Python 3.11, pytest, pydantic, existing `SkillRegistry`, existing `route_skill_names`, standard-library `ast`, `json`, and `argparse`.

## Global Constraints

- Use strict TDD: write failing tests before production changes.
- Keep the first version backend-only.
- Do not add new runtime database tables.
- Reuse `agent_execution_logs` shaped records for online metrics.
- Keep external dependencies unchanged.

---

### Task 1: Evaluation Models And Offline Routing Metrics

**Files:**
- Create: `backend/tests/test_skill_evaluation.py`
- Create: `backend/src/personal_assistant/skills/evaluation/__init__.py`
- Create: `backend/src/personal_assistant/skills/evaluation/models.py`
- Create: `backend/src/personal_assistant/skills/evaluation/offline.py`

**Interfaces:**
- Produces: `GoldenSkillCase(id: str, query: str, expected_skills: list[str], expected_tool: str | None = None, expected_args: dict[str, Any] = {})`
- Produces: `RoutingMetrics(total_cases: int, selection_accuracy: float | None, false_positive_rate: float | None, parameter_extraction_fidelity: float | None)`
- Produces: `evaluate_routing_cases(registry: SkillRegistry, cases: list[GoldenSkillCase], **router_kwargs) -> RoutingMetrics`

- [ ] **Step 1: Write failing tests for routing metrics**

Add tests that verify exact match accuracy for positive cases, false positive rate for negative cases, and `None` when a metric has no denominator.

- [ ] **Step 2: Run test to verify RED**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py -q`
Expected: FAIL because the evaluation package does not exist.

- [ ] **Step 3: Implement minimal models and offline evaluator**

Use pydantic models and call `route_skill_names` for each case. Compare selected and expected Skill sets exactly.

- [ ] **Step 4: Run test to verify GREEN**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py -q`
Expected: PASS for routing metric tests.

### Task 2: Static And Runtime Metrics

**Files:**
- Modify: `backend/tests/test_skill_evaluation.py`
- Create: `backend/src/personal_assistant/skills/evaluation/static.py`
- Create: `backend/src/personal_assistant/skills/evaluation/runtime.py`

**Interfaces:**
- Produces: `StaticSkillMetrics(skill_name: str, description_tokens: int, skill_md_lines: int, python_lines: int, max_cyclomatic_complexity: int, tool_count: int)`
- Produces: `RuntimeSkillMetrics(skill_name: str, tool_calls: int, successful_calls: int, failed_calls: int, retry_count: int, execution_success_rate: float | None, retry_ratio: float | None, p95_latency_ms: int | None, p99_latency_ms: int | None, token_consumption_per_call: float | None)`
- Produces: `evaluate_static_skill(skill: Skill) -> StaticSkillMetrics`
- Produces: `evaluate_runtime_logs(registry: SkillRegistry, logs: Sequence[Any]) -> dict[str, RuntimeSkillMetrics]`

- [ ] **Step 1: Write failing tests for static and runtime metrics**

Add tests covering description token counts, line counts, branch complexity, success rate, retry ratio, latency percentiles, and tool-to-skill mapping.

- [ ] **Step 2: Run test to verify RED**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py -q`
Expected: FAIL because static/runtime evaluators are missing.

- [ ] **Step 3: Implement minimal evaluators**

Use `ast` for complexity approximation and map tool logs to skills through loaded tool names and script declarations.

- [ ] **Step 4: Run test to verify GREEN**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py -q`
Expected: PASS for all evaluation unit tests.

### Task 3: Report Scoring, Rendering, And CLI

**Files:**
- Modify: `backend/tests/test_skill_evaluation.py`
- Create: `backend/src/personal_assistant/skills/evaluation/report.py`
- Create: `backend/src/personal_assistant/skills/evaluation/__main__.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `SkillEvaluationReport(skills: list[SkillEvaluationResult], routing: RoutingMetrics | None = None)`
- Produces: `evaluate_skill_registry(...) -> SkillEvaluationReport`
- Produces: `render_markdown_report(report: SkillEvaluationReport) -> str`
- Produces CLI options `--skills-dir`, `--golden`, `--output-json`, and `--output-md`.

- [ ] **Step 1: Write failing report and CLI tests**

Add tests that verify score normalization, Markdown contains key metrics, and CLI writes JSON/Markdown files.

- [ ] **Step 2: Run test to verify RED**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py -q`
Expected: FAIL because report and CLI modules are missing.

- [ ] **Step 3: Implement report, renderer, and CLI**

Combine routing, static, and optional runtime metrics into one report. Dump JSON via pydantic and render a concise Markdown scorecard.

- [ ] **Step 4: Run focused verification**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py -q`
Expected: PASS.

- [ ] **Step 5: Run final backend verification**

Run: `cd backend; uv run pytest tests/test_skill_evaluation.py tests/test_skill_loader.py tests/test_router.py -q`
Expected: PASS.

