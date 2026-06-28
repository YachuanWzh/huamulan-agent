# Plan: Skill Loader Refactor — progressive loading + script-as-tool + resolve-time rewrite

## Goal (one sentence)
Make skill loading genuinely progressive (meta-first, full-on-match), add the ability for
skills to expose `scripts/` scripts as agent-callable tools whose output the agent analyzes
before replying, and fix the broken `resolve-time` skill whose `SKILL.md` documents tools that
don't exist.

## Current state — what's wrong ("屎")
1. **Progressive loading is undermined.** `route_skills` falls back to *loading all skills*
   when no keyword matches (`router.py:23`), defeating the whole point of progressive loading.
   `_keyword_route` ignores the `triggers:` frontmatter field and crudely tokenizes the
   description.
2. **No script-as-tool capability.** `resolve-time/skill.py` was deleted; `SKILL.md` references
   three LangChain tools (`resolve_current_time`, `resolve_date_by_offset`,
   `resolve_date_by_weekday`) that **do not exist**. The script `scripts/resolve_date.py`
   exists and works, but the agent has no way to call it. End-to-end the skill is broken.
3. **`resolve-time/SKILL.md` is inaccurate** — documents non-existent tools.

Unrelated WIP in the working tree (streaming endpoints in `server.py`/`harness.py`,
frontend changes) is left untouched.

## Design

### A. New module `personal_assistant/skills/script_tool.py`
A factory `build_script_tool(decl, skill_path) -> StructuredTool`:
- `decl = {name, description, command: [...], params: {param_name: {type, description, required?, default?}}}`
- Builds a pydantic `args_schema` from `params` (type map: `integer→int`, `string→str`,
  `number→float`, `boolean→bool`). Required fields are mandatory; optional fields use `default`.
- The tool function substitutes `{param_name}` placeholders in each `command` token with the
  stringified arg, runs `subprocess.run(command, cwd=skill_path, capture_output=True,
  text=True, timeout=30)`, and returns `stdout.strip()`. On non-zero exit it returns
  `f"Script failed (exit {code}): {stderr.strip()}"`.
- Portability rule: if `command[0]` is `"python"` / `"python3"`, replace with `sys.executable`.
- Language-agnostic (scripts can be python/bash/node) — only the subprocess contract matters.

### B. `skills/base.py`
Add `script_decls: list[dict] = field(default_factory=list)` to the `Skill` dataclass.
Lightweight — just declarations, no tool objects built until `load_skill`.

### C. `skills/loader.py`
- Switch frontmatter parsing to `yaml.safe_load` (handles nested `scripts:` list and
  `triggers:` list). Keep a minimal hand-rolled fallback if `yaml` import fails.
- `scan_metadata`: parse `name`, `description`, `triggers`, `scripts` → store on `Skill`.
  No tool building, no `skill.py` import. `loaded=False`.
- `load_skill`: read full instructions + build script tools (`build_script_tool` per decl) +
  import `skill.py` TOOLS if present. `skill.tools = script_tools + skill_py_tools`.

### D. `agent/router.py`
- `_keyword_route`: if a skill has `triggers`, match if any trigger appears as a substring of
  the user text; otherwise fall back to the existing name+description token match.
- Remove the "load all on no match" fallback. No-match → empty selection. The agent still
  sees the lightweight meta overview of all skills (so it can ask a clarifying question).
  This is what progressive loading actually means.

### E. `skills/resolve-time/SKILL.md`
Rewrite frontmatter to declare the three script tools under `scripts:` pointing at
`scripts/resolve_date.py`. Keep `triggers`. Body documents the real tools accurately.

### F. `skills/resolve-time/scripts/resolve_date.py`
Core logic is well-tested and sound — keep it. Minor cleanup only (consistent JSON output,
clearer usage). No behavioral change to the calc functions.

## TDD tasks (each: RED → GREEN → REFACTOR → commit)

### Task 1 — Script tool factory (`script_tool.py`)
- RED: `tests/test_script_tool.py`
  - builds a `StructuredTool` with given name/description/args_schema
  - calling the tool subprocesses a real script and returns its stdout
  - substitutes `{param}` placeholders; applies defaults for omitted optional params
  - non-zero exit → returns error string containing stderr
  - `command[0]=="python"` is replaced with `sys.executable`
- GREEN: implement `build_script_tool`.
- Commit.

### Task 2 — Loader parses `scripts` + `triggers`; builds script tools on load
- RED: `tests/test_skill_loader.py` additions
  - `scan_metadata` populates `script_decls` and `triggers` from frontmatter
  - `load_skill` builds script tools from `script_decls` (skill with scripts but no
    `skill.py` now has non-empty `tools`)
  - script tool actually invokes the skill's script and returns output
- GREEN: yaml frontmatter parse + integrate `build_script_tool` into `load_skill`.
- Update `base.py` with `script_decls` field (covered by tests above).
- Commit.

### Task 3 — Router: trigger-based routing, no load-all fallback
- RED: `tests/test_router.py` / `tests/test_progressive_loading.py` additions
  - `_keyword_route` matches via `triggers` when present
  - no-match returns `[]` and `route_skills` does NOT load all skills (only meta overview)
- GREEN: implement trigger matching + remove fallback.
- Update `test_fallback_loads_all_when_no_match` to assert the new progressive behavior.
- Commit.

### Task 4 — resolve-time rewrite
- RED: `tests/test_resolve_time.py` updates
  - frontmatter has `scripts:` with 3 declarations
  - `load_skill("resolve-time")` builds 3 tools named `resolve_current_time`,
    `resolve_date_by_offset`, `resolve_date_by_weekday`
  - each tool, when called, runs the script and returns valid JSON / ISO time
  - drop the obsolete `test_no_skill_py_means_no_langchain_tools` assertion (replace with
    "scripts become tools" assertion)
- GREEN: rewrite `SKILL.md`; minor script cleanup.
- Commit.

### Task 5 — Verify + dependency
- Run full suite (`python -m pytest -q`).
- Add `pyyaml` to `pyproject.toml` dependencies (we now rely on it explicitly).
- Lint with `ruff`.

## Assumptions
- Working **in place** on `master` (not a fresh worktree): the code being refactored is
  uncommitted in the working tree, so a fresh worktree would discard it. Justified per
  superharness "work in place when worktree would block" guidance.
- No-match → no skills loaded (agent sees meta overview, may ask clarifying question).
  Replaces the previous load-all fallback.
- `command[0]=="python"` → `sys.executable` convention for portability.
- Script tool execution is synchronous subprocess with a 30s timeout.

## Out of scope
- Streaming endpoints / frontend changes (separate WIP feature).
- LLM-based semantic routing (keyword/trigger routing is sufficient for now).
