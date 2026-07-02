# Evaluation Diagnostics Design

## Goal

Extend ClawEval so E2E results explain which stage failed and let users inspect case-level details in the frontend instead of only seeing aggregate scores.

## Scope

- Add case-level evaluation details for Quick and E2E runs.
- Add deterministic diagnosis for routing, tool, answer, and safety failures.
- Add LLM-as-judge for E2E quality and prompt/reasoning diagnosis.
- Default judge model must be a Pro model and must not use Flash.
- Render evaluation details in the Skill Evaluation workspace panel.

## Architecture

The backend keeps deterministic checks as the source of truth for measurable expectations. A new diagnostics layer converts each case outcome into structured `CaseEvaluationDetail` records with `checks`, `diagnosis`, optional `judge`, actual outputs, and compact log summaries. E2E runs call an injectable judge client after each case outcome is collected; judge failures are represented as unavailable diagnostics and do not prevent deterministic reporting.

The frontend receives `case_details` in stream progress and final report events. It renders a compact details section under the ClawEval summary, with one expandable row per case showing expected versus actual routing, tools, answer, logs, judge score, and recommended fix.

## Backend Data Model

- `EvaluationCheck`: `name`, `stage`, `passed`, `expected`, `actual`, `reason`.
- `CaseDiagnosis`: `stage`, `severity`, `summary`, `signals`, `recommendation`.
- `JudgeEvaluation`: `score`, `passed`, `failure_stage`, `reason`, `evidence`, `recommendation`, `model`, `available`.
- `CaseEvaluationDetail`: `case_id`, `mode`, `query`, `turns`, `expected_skills`, `selected_skills`, `expected_tool_calls`, `actual_tool_calls`, `final_answer`, `checks`, `diagnosis`, `judge`, `log_summary`.
- `SkillEvaluationReport.case_details`: list of case details.

## Judge Behavior

Default model: `deepseek-v4-pro`.

Configuration:

- `EVALUATION_JUDGE_ENABLED=true`
- `EVALUATION_JUDGE_MODEL=deepseek-v4-pro`
- `EVALUATION_JUDGE_BASE_URL` falls back to `LLM_BASE_URL`
- `EVALUATION_JUDGE_API_KEY` falls back to `OPENAI_API_KEY`

Validation prevents models containing `flash` from being used as the configured judge model. The judge prompt requests strict JSON and focuses on whether the final answer satisfies the case, whether failures look like prompt/reasoning issues, and what to fix next.

## Frontend Design

The Skill Evaluation workspace remains an operational dashboard, not a marketing page. The new detail area is a dense inspection surface:

- summary chips for total failed cases and main failure stages;
- expandable case rows with stage/status badges;
- expected vs actual columns for skills and tools;
- final answer and compact log JSON snippets;
- judge card with model, score, reason, evidence, and recommendation.

The design uses the existing restrained workspace styling, adding only stage-colored left borders and small badges so the page stays scannable.

## Testing

- Backend unit tests for deterministic case checks and failure-stage diagnosis.
- Backend unit tests for judge default model and Flash rejection.
- Backend stream tests proving `case_details` appears in progress and final report events.
- Frontend API type tests for streamed case details.
- Frontend component tests proving case details render and expand.

