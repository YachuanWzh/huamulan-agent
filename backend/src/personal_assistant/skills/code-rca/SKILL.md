---
name: code-rca
description: >-
  Root-cause analysis for code-level failures — parses tool-failure alerts from
  flavor-code or similar coding agents, traces error signatures through source
  code, and produces a structured RCA report with fix recommendations.
triggers:
  - code rca
  - code RCA
  - code analysis
  - code root cause
  - tool failure
  - flavor-code alert
scripts:
  - name: analyze_code_issue
    description: >-
      Parse a code-tool failure alert (from stdin) and search a workspace for
      relevant source code using grep and git. Outputs a structured RCA report.
    command: ["python", "scripts/analyze_code_issue.py"]
    params:
      workspace:
        type: string
        description: "Absolute path to the source code workspace to search."
        required: false
        default: ""
      max_results:
        type: integer
        description: "Maximum number of grep results per pattern."
        default: 20
---

# Code RCA Skill — 代码根因分析

## Purpose

Diagnose code-level tool failures reported by coding agents (flavor-code, etc.).
When a coding agent encounters a tool execution failure, the alert contains the
error signature (tool name, error code, error message), relevant file paths,
git context, and agent state. This skill guides the agent through systematic
root-cause analysis of the *source code* rather than of infrastructure metrics.

## When to Use

- An alert of type `FlavorToolFailure:*` arrives from the OTEL alert pipeline
- The alert's `service_name` is `flavor-code` (or another coding tool)
- The alert `annotations.description` contains code context (workspace path, tool input)
- User says: "analyze this code failure" / "代码根因分析" / "RCA this tool error"

## When NOT to Use

- Infrastructure-level RCA (use `troubleshoot` skill)
- OTEL trace/metric analysis (use `otel-query` skill)
- Patrol/scheduled inspection (use `patrol` skill)

## Procedure

### Step 1: Parse the Alert

Extract these fields from the alert annotations/description:
- **Tool name**: which tool failed (e.g., `Shell`, `Read`, `Edit`)
- **Error code**: the internal error code (`tool_error`, `permission_denied`, etc.)
- **Error message**: the human-readable failure message
- **Tool input**: the arguments the tool was called with
- **Workspace path**: the absolute path of the project where the failure occurred
- **Git branch & commit**: the code version at time of failure
- **Agent type**: `main` or `subagent`

### Step 2: Trace the Error Source

1. **For tool errors (P0)** — the most actionable failures:
   - Search for the tool implementation in the codebase using grep
   - For `Shell` tool failures: extract the command + args, examine the failing command pattern
   - Check git log for recent changes to the relevant source files
   - Look for error-handling gaps: patterns like `try { catch` or missing guards

2. **For permission/hook denials (P1)**:
   - Search for the permission check that rejected the tool
   - Cross-reference the hook event type with registered handlers

3. **For all levels**: run the `analyze_code_issue` script to automate the first pass
   of searching.

### Step 3: Build the RCA Report

Structure the output as:

```
## Root Cause Analysis: {alert_name}

### Error Summary
- Tool: {tool_name}
- Error Code: {error_code}
- Message: {error_message}
- Occurred in: {workspace} (branch: {branch}, commit: {commit})

### Source Code Trace
[Relevant code snippets linked to the error]

### Root Cause
[The identified cause with specific file:line references]

### Fix Recommendation
[Concrete code change recommendation]

### Prevention
[Suggestions for tests, guards, or monitoring]
```

## Analysis Script

The `analyze_code_issue` script reads a JSON alert from stdin and performs
automated code searches in the workspace. Expected input format:

```json
{
  "tool": "Shell",
  "error_code": "tool_error",
  "error_message": "...",
  "input": {"command": "git", "args": ["push"]},
  "workspace": "/path/to/project",
  "git_branch": "main",
  "git_commit": "abc1234",
  "agent": "main"
}
```

## Integration with flavor-code

The IncidentReporter in flavor-code sends alerts to `POST /api/otel/alerts`.
P0 alerts (tool errors) trigger automatic RCA via the agent harness. The agent
receives the alert context and should use this `code-rca` skill to perform the
code-level analysis.

The alert `annotations.description` field contains multi-line text:
```
Tool: Shell
Agent: main
Error: [tool_error] Command exited with code 1
Branch: main
Commit: abc1234
Workspace: C:\Users\...\project
Input: {"command":"git","args":["push"]}
```
