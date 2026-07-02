# Skill Evaluation Design

## Goal

Add a first-class Skill evaluation capability that gives `huamulan-agent` a measurable quality score for each Skill, combining offline golden-set checks with online execution-log aggregates.

## Scope

- Add a backend evaluation module under `personal_assistant.skills.evaluation`.
- Add an offline golden dataset runner for routing quality and static Skill quality checks.
- Add an online aggregation path that computes reliability, latency, retry, and token metrics from existing execution logs.
- Return machine-readable JSON and human-readable Markdown reports.
- Keep the first version backend-only. Frontend governance views can consume the same report model later.

## Architecture

The evaluation system uses one shared report model:

- `SkillEvaluationReport` is the top-level object.
- `RoutingMetrics` covers discoverability and routing quality.
- `StaticSkillMetrics` covers metadata/token efficiency, script/tool metadata, line counts, and cyclomatic complexity.
- `RuntimeSkillMetrics` covers execution success rate, retry ratio, latency percentiles, and token consumption.
- `SkillScore` combines the available metric groups with conservative default weights.

Offline evaluation reads a JSONL golden dataset. Each case contains a user query, expected selected skills, and optional expected tool/argument data. It calls the existing `route_skill_names` function so evaluation exercises the same router used by the Agent.

Online evaluation reads existing `ExecutionLog`-shaped records, not a new database table. Tool logs are mapped back to skills through the loaded `SkillRegistry` tool map. This keeps runtime metrics compatible with the existing `agent_execution_logs` table and Langfuse callback path.

## Golden Dataset Format

Each line is JSON:

```json
{"id":"weather-001","query":"Will it rain in Beijing tomorrow?","expected_skills":["weather"],"expected_tool":"weather_forecast","expected_args":{"location":"Beijing"}}
```

Fields:

- `id`: stable case id.
- `query`: user text passed to the router.
- `expected_skills`: selected Skill names expected for the query. Empty means no Skill should be selected.
- `expected_tool`: optional expected tool name for future parameter extraction checks.
- `expected_args`: optional expected argument subset for future tool-call extraction checks.

## Metrics

Offline routing metrics:

- `selection_accuracy`: exact expected skill set match ratio for cases that expect at least one Skill.
- `false_positive_rate`: cases with no expected Skill but at least one selected Skill, divided by no-skill cases.
- `parameter_extraction_fidelity`: reserved as `null` unless tool-call traces are provided.
- `description_token_efficiency`: measured statically by token-estimating Skill descriptions.

Static metrics:

- `description_tokens`: whitespace/CJK-aware approximation.
- `skill_md_lines`: lines in `SKILL.md`.
- `python_lines`: lines across `skill.py` and `scripts/*.py`.
- `max_cyclomatic_complexity`: AST-based branch complexity approximation.
- `tool_count`: loaded tool/script count when available.

Runtime metrics:

- `execution_success_rate`: completed tool events divided by all completed/failed tool events for that Skill.
- `retry_ratio`: retry events divided by tool events.
- `p95_latency_ms` and `p99_latency_ms`: percentile latency over tool events.
- `token_consumption_per_call`: total logged tokens divided by tool calls when token usage exists.

## Score

`overall_score` is a weighted average of available components:

- Routing quality: 40%
- Runtime reliability: 30%
- Static maintainability and efficiency: 20%
- Usage signal: 10%

Unavailable components are skipped and weights are normalized. This avoids punishing new Skills before runtime data exists.

## Interfaces

- `evaluate_skill_registry(registry, cases, ...) -> SkillEvaluationReport`
- `evaluate_runtime_logs(registry, logs) -> dict[str, RuntimeSkillMetrics]`
- `render_markdown_report(report) -> str`
- CLI module: `python -m personal_assistant.skills.evaluation --skills-dir ... --golden ... --output-json ... --output-md ...`

## Testing

- Unit tests for golden routing metrics using fake registries and router inputs.
- Unit tests for static complexity and description token metrics.
- Unit tests for runtime log aggregation with synthetic tool and retry logs.
- CLI smoke test that writes JSON and Markdown report files.

