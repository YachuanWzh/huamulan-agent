"""Analyze a code-tool failure alert and produce a structured RCA report.

Reads a JSON alert from stdin, searches the workspace for relevant source code
patterns using git and findstr (Windows) / grep (Unix), and outputs a
structured JSON RCA report.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    """Run a command with utf-8 encoding handling."""
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, stdout="", stderr=f"timeout after {timeout}s")
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, -1, stdout="", stderr="command not found")
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, -1, stdout="", stderr=str(exc))


def _safe_stdout(result: subprocess.CompletedProcess[str]) -> str:
    """Get stdout string safely."""
    return result.stdout or ""


def _safe_stderr(result: subprocess.CompletedProcess[str]) -> str:
    """Get stderr string safely."""
    return result.stderr or ""


def _search_code(pattern: str, workspace: str, max_results: int = 20) -> list[str]:
    """Search workspace source files for *pattern* (fixed-string).

    Uses grep on Unix, findstr on Windows.
    """
    if not workspace or not Path(workspace).is_dir():
        return []

    is_win = platform.system() == "Windows"
    if is_win:
        result = _run(
            ["cmd", "/c", f'findstr /s /n /i /c:"{pattern}" *.ts *.js *.py'],
            cwd=workspace,
        )
    else:
        result = _run(
            ["grep", "-rn", "-F", "--include=*.ts", "--include=*.js", "--include=*.py", pattern],
            cwd=workspace,
        )

    out = _safe_stdout(result)
    if result.returncode not in (0, 1):
        err = _safe_stderr(result)
        return [err] if err.strip() else []
    return [line.strip() for line in out.split("\n") if line.strip()][:max_results]


def _git_log(file_path: str, workspace: str, max_entries: int = 10) -> list[str]:
    """Get recent commits touching a file."""
    if not workspace or not Path(workspace).is_dir():
        return []
    result = _run(
        ["git", "-C", workspace, "log", "--oneline", f"-{max_entries}", "--", file_path],
    )
    out = _safe_stdout(result)
    if result.returncode != 0:
        return []
    return [line for line in out.strip().split("\n") if line]


def _git_diff(commit: str, workspace: str) -> str:
    """Show the stat diff for a single commit."""
    if not workspace:
        return ""
    result = _run(
        ["git", "-C", workspace, "show", "--stat", commit],
        timeout=10,
    )
    out = _safe_stdout(result)
    return out.strip() if result.returncode == 0 else ""


def _extract_error_signature(error_message: str) -> str:
    """Extract the most actionable part of an error message."""
    lines = error_message.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for prefix in ("Error:", "error:", "ERROR:", "Fatal:", "fatal:", "[tool_error]"):
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return line
    return error_message[:200]


def _file_patterns_for_tool(tool_name: str) -> list[str]:
    """Return candidate file patterns based on the failing tool."""
    tool_lower = tool_name.lower()
    patterns: dict[str, list[str]] = {
        "shell": ["src/tools/shell.ts"],
        "read": ["src/tools/files.ts"],
        "write": ["src/tools/files.ts"],
        "edit": ["src/tools/files.ts"],
        "applypatch": ["src/tools/file-diff.ts"],
        "glob": ["src/tools/search.ts"],
        "grep": ["src/tools/search.ts"],
        "lspfindrefs": ["src/tools/lsp.ts"],
        "lsphover": ["src/tools/lsp.ts"],
        "lspdiagnostics": ["src/tools/lsp.ts"],
        "askuserquestion": ["src/tools/ask-user-question.ts"],
        "task": ["src/agent/subagents.ts"],
        "taskplan": ["src/agent/task-plan.ts"],
        "todowrite": ["src/tools/todo-write.ts"],
        "taskoutput": ["src/tools/task-output.ts"],
        "skillresource": ["src/skills/tool.ts"],
    }
    return patterns.get(tool_lower, [])


def main() -> int:
    raw = sys.stdin.read() or "{}"
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid JSON input: {exc}"}, indent=2))
        return 1

    tool = str(data.get("tool", "unknown"))
    error_code = str(data.get("error_code", "unknown"))
    error_message = str(data.get("error_message", ""))
    workspace = str(data.get("workspace", "") or os.environ.get("FLAVOR_WORKSPACE", ""))
    git_branch = str(data.get("git_branch", ""))
    git_commit = str(data.get("git_commit", ""))
    agent = str(data.get("agent", "unknown"))
    max_results = int(data.get("max_results", 20))

    signature = _extract_error_signature(error_message)
    candidate_files = _file_patterns_for_tool(tool)

    grep_results: dict[str, list[str]] = {}
    if workspace:
        grep_results[f"tool:{tool}"] = _search_code(
            f"name.*{tool}|create{tool}", workspace, max_results,
        )
        if error_code == "tool_error":
            grep_results["error_handling"] = _search_code(
                "catch |throw.*Error|reject", workspace, max_results,
            )
        elif error_code in ("permission_denied", "hook_denied"):
            grep_results["permission_checks"] = _search_code(
                "decision.*deny|permission_denied", workspace, max_results,
            )
        for file_path in candidate_files:
            grep_results[f"git_log:{file_path}"] = _git_log(file_path, workspace)

    recent_diff = ""
    if git_commit:
        recent_diff = _git_diff(git_commit, workspace)
        grep_results["commit_diff"] = [recent_diff] if recent_diff else []

    recommendations: dict[str, str] = {
        "tool_error": (
            f"The {tool} tool failed with: {signature}. Check the tool's execute() "
            f"function in {' '.join(candidate_files) if candidate_files else 'the relevant module'} "
            f"for unhandled exceptions, input validation gaps, or subprocess errors."
        ),
        "permission_denied": (
            f"The {tool} tool was blocked by the permission engine. "
            f"Check permissions/engine.ts for the decision logic and verify "
            f"the tool's permissions() callback."
        ),
        "hook_denied": (
            f"A hook handler denied the {tool} tool. Check registered hook handlers "
            f"in production.ts and plugin configurations."
        ),
        "unknown_tool": (
            f"The model requested tool '{tool}' which is not registered. "
            f"Check production.ts for missing tool registrations."
        ),
        "invalid_input": (
            f"The model provided invalid arguments to {tool}. "
            f"Check the tool's inputSchema (Zod) for type mismatches."
        ),
        "approval_required": (
            f"The {tool} requires approval but no approval callback was available. "
            f"This is expected in non-interactive mode (approvalPolicy=deny)."
        ),
    }

    report = {
        "severity": "P0" if error_code == "tool_error" else "P1",
        "tool": tool,
        "error_code": error_code,
        "primary_signature": signature,
        "full_error": error_message[:500],
        "workspace": workspace,
        "git_branch": git_branch,
        "git_commit": git_commit,
        "agent": agent,
        "relevant_files": candidate_files,
        "grep_results": {k: v for k, v in grep_results.items() if v},
        "recommendation": recommendations.get(
            error_code,
            f"The {tool} tool failed with code {error_code}: {signature}",
        ),
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
