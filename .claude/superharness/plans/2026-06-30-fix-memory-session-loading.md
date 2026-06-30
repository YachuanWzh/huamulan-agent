# Fix Memory Session Loading

**Date:** 2026-06-30
**Goal:** Memory files in `backend/.memory/` are not loaded into the system prompt by the SessionStart hook — only HARNESS.md and STACK.md are injected. Extend the hook to read and inject memory files.

## Problem Analysis

1. `session-start.ps1` currently only reads HARNESS.md and STACK.md
2. `backend/.memory/` contains MEMORY.md (index), SYSTEM.md, USER.md, and individual memory files like `user-preferred-name-yachuan.md`
3. Nothing reads these memory files and injects them into the session context
4. Result: user preferences (like being called "亚川") are lost across sessions

## Architecture

- **Memory storage**: `backend/.memory/` — gitignored local directory
  - `MEMORY.md` — index of user memories (one link per line, same format as Claude Code memory)
  - `SYSTEM.md` — system-level context
  - `USER.md` — user profile/preferences
  - `*.md` — individual memory files referenced from MEMORY.md
- **SessionStart hook**: `.claude/superharness/plugins/superharness/hooks/session-start.ps1`
  - Outputs JSON with `hookSpecificOutput.additionalContext`
  - Injected into Claude Code system prompt at session start
- **Claude Code memory**: `C:\Users\wangzh\.claude\projects\C--idea-langgraph-claw\memory\`
  - Separate system, currently empty — not the target of this fix

## Tasks

### Task 1: Add memory loading to session-start.ps1

**RED** — Write test script that runs session-start.ps1 and asserts memory content is in output:
```powershell
# test-session-start-memory.ps1
$env:CLAUDE_PLUGIN_ROOT = ".claude/superharness/plugins/superharness"
$output = & ".claude/superharness/plugins/superharness/hooks/session-start.ps1"
$json = $output | ConvertFrom-Json
$ctx = $json.hookSpecificOutput.additionalContext
# Assert memory content present
if ($ctx -match "亚川") { "PASS: memory content found" } else { throw "FAIL: memory content missing" }
if ($ctx -match "MEMORY INDEX|记忆索引") { "PASS: memory index found" } else { throw "FAIL: index missing" }
```

**GREEN** — Add memory file loading logic to `session-start.ps1`:
After existing STACK.md loading, add:
1. Determine project root from CLAUDE_PLUGIN_ROOT
2. Look for `backend/.memory/MEMORY.md`
3. If present, parse it for linked memory files
4. Read SYSTEM.md and USER.md
5. Read each referenced memory file
6. Format all content into a memory context block
7. Append to `$context` before JSON output

**REFACTOR** — Ensure clean PowerShell, proper encoding, silent error handling matching existing hook patterns.

### Task 2: Verify end-to-end

- Run hook manually with `$env:CLAUDE_PLUGIN_ROOT` set
- Confirm output JSON includes "亚川" from memory
- Confirm output JSON includes MEMORY.md content
- Confirm hook exits 0 even when `.memory/` doesn't exist (graceful degradation)
