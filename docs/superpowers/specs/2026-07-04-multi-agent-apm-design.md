# Multi-Agent APM Design

## Goal

Add an optional LangGraph multi-agent mode while preserving the current single ReAct agent and skill routing behavior.

## Scope

The first implementation introduces a request-level `agent_mode` switch with values `single` and `multi`. `single` remains the default for all existing callers. `multi` routes a turn through a supervisor and multiple APM child agents, then returns one synthesized answer.

## Architecture

- Keep the current `AgentHarness` and `compile_agent` path unchanged for `single`.
- Add `personal_assistant.agent.multi_agent.compile_multi_agent`.
- Add query rewrite and intent slots before supervisor planning.
- Use structured JSON messages between supervisor and APM subagents.
- Record multi-agent execution through existing PostgreSQL execution logs.
- Reuse existing Redis cache and Qdrant skill vector settings where available; user-vector retrieval is represented as an explicit state slot so a Qdrant-backed retriever can be attached without changing API contracts.

## LangGraph Nodes

- `rewrite_intent`: normalizes the query and extracts intent slots.
- `supervisor`: creates a JSON plan for child APM agents.
- `metrics_agent`: analyzes metric and Web Vitals concerns.
- `troubleshoot_agent`: analyzes incidents and RCA paths.
- `patrol_agent`: analyzes health-check and alert-rule concerns.
- `audit_agent`: analyzes logs, approvals, safety, and governance.
- `synthesize`: merges child JSON outputs into the final assistant response.

## Frontend

`agent_mode` lives in `App` so chat and evaluation can share it. The UI exposes a compact global segmented control in the header. `ChatPanel/useChat` and `WorkspacePanel` receive the current mode and include it in chat and evaluation requests.

## Evaluation

Skill evaluation requests accept `agent_mode`. Quick evaluation still evaluates routing only. E2E evaluation forwards the mode to `AgentHarness.run_user_turn`, so the same dataset can compare single and multi-agent behavior.

## Compatibility

Existing requests that omit `agent_mode` behave exactly as before. Existing approval, streaming, replay, audit, cache, and checkpoint flows are untouched for single-agent mode.
