# Skill Routing Rerank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add an optional Ollama `qllama/bge-reranker-v2-m3` rerank stage after semantic Skill recall and before threshold selection / LLM fallback.

**Architecture:** Keep regex routing as the deterministic first-stage short circuit. When semantic routing returns candidates, optionally call a reranker provider configured from env, replace candidate scores with rerank scores, sort candidates by rerank score, then apply the existing threshold and LLM judge flow.

**Tech Stack:** Python 3.11, pytest, pydantic-settings, urllib-based Ollama HTTP calls, existing Skill router abstractions.

## Global Constraints

- Use strict TDD: write failing tests before production changes.
- Keep rerank optional and off by default.
- Maintain all new knobs in `Settings` and `backend/.env.example`.
- Update `README.md` and `技术方案报�?md` after code changes.

---

### Task 1: Rerank Router Behavior And Env Config

**Files:**
- Modify: `backend/tests/test_router.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/src/personal_assistant/agent/router.py`
- Modify: `backend/src/personal_assistant/config.py`

**Interfaces:**
- Produces: `SkillReranker` protocol with `rerank(query: str, candidates: list[SkillSemanticCandidate]) -> list[SkillSemanticCandidate]`
- Produces: `OllamaBgeM3Reranker` using `POST /api/rerank` and fallback response parsing for score fields.
- Consumes: existing `route_skill_names(...)` semantic candidates and threshold logic.

- [x] **Step 1: Write failing router tests**

Add tests proving rerank reorders low semantic candidates before threshold selection and that rerank failures preserve semantic candidates and still fall back to LLM.

- [x] **Step 2: Run router tests to verify RED**

Run: `cd backend; uv run pytest tests/test_router.py -q`
Expected: FAIL because `route_skill_names` does not accept or call a reranker yet.

- [x] **Step 3: Write failing config tests**

Add default and env override assertions for `SKILL_ROUTING_RERANK_ENABLED`, `SKILL_ROUTING_RERANK_MODEL`, `SKILL_ROUTING_RERANK_THRESHOLD`, and `SKILL_ROUTING_RERANK_TOP_K`.

- [x] **Step 4: Run config tests to verify RED**

Run: `cd backend; uv run pytest tests/test_config.py -q`
Expected: FAIL because config fields do not exist.

- [x] **Step 5: Implement minimal code**

Add config fields, router protocol/provider, and rerank stage immediately after semantic recall.

- [x] **Step 6: Run focused tests to verify GREEN**

Run: `cd backend; uv run pytest tests/test_router.py tests/test_config.py -q`
Expected: PASS.

### Task 2: Agent Wiring And Documentation

**Files:**
- Modify: `backend/tests/test_hooks.py`
- Modify: `backend/src/personal_assistant/agent/agent.py`
- Modify: `backend/.env.example`
- Modify: `README.md`
- Modify: `技术方案报�?md`

**Interfaces:**
- Consumes: `Settings.skill_routing_rerank_*`.
- Produces: `compile_agent` passes reranker and rerank threshold/top-K into `build_skill_router` when rerank is enabled.

- [x] **Step 1: Write failing wiring tests**

Add tests that `_build_skill_reranker` creates an Ollama reranker from settings and that disabled rerank returns `None`.

- [x] **Step 2: Run wiring tests to verify RED**

Run: `cd backend; uv run pytest tests/test_hooks.py -q`
Expected: FAIL because the builder does not exist.

- [x] **Step 3: Implement wiring**

Instantiate the reranker from settings, pass it into `build_skill_router`, and keep warmup focused on vector embeddings.

- [x] **Step 4: Update docs and env example**

Document rerank as optional stage 2.5 using local Ollama `qllama/bge-reranker-v2-m3`, including env variables and thresholds.

- [x] **Step 5: Run final verification**

Run: `cd backend; uv run pytest tests/test_router.py tests/test_config.py tests/test_hooks.py -q`
Expected: PASS.
