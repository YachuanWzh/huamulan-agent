"""Wrap a skill's `scripts/` entry as a LangChain tool.

A script declaration (parsed from SKILL.md frontmatter) looks like:

    name: resolve_date_by_offset
    description: Calculate a date by day offset from today.
    command: ["python", "scripts/resolve_date.py", "offset", "{day_offset}", "{timezone}"]
    params:
      day_offset:
        type: integer
        description: Days from today (positive=future, negative=past).
        required: true
      timezone:
        type: string
        description: IANA timezone.
        default: Asia/Shanghai

The built tool subprocesses `command` with `{param}` placeholders substituted by the
caller's arguments, captures stdout, and returns it to the agent. The agent then reasons
over the output before replying to the user. Scripts may be any language — only the
subprocess contract (argv in, stdout out) matters.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

# Map frontmatter type names to Python types for the args schema.
_TYPE_MAP: dict[str, type] = {
    "integer": int,
    "int": int,
    "string": str,
    "str": str,
    "number": float,
    "float": float,
    "boolean": bool,
    "bool": bool,
}

# Commands that should run under the current interpreter rather than a PATH lookup.
_PYTHON_TOKENS = {"python", "python3"}

# Hard cap so a misbehaving script can't hang the agent loop.
_TIMEOUT_SECONDS = 30


def build_script_tool(decl: dict[str, Any], skill_path: Path) -> StructuredTool:
    """Build a LangChain ``StructuredTool`` from a script declaration.

    ``decl`` keys: ``name``, ``description``, ``command`` (list[str] with ``{param}``
    placeholders), ``params`` (dict[param_name, {type, description, required?, default?}]).
    ``skill_path`` is used as the subprocess cwd so relative ``scripts/...`` paths resolve.
    """
    name = decl["name"]
    description = decl.get("description", "")
    command = list(decl.get("command", []))
    params = decl.get("params") or {}

    args_schema = _build_args_schema(name, params)

    def _render(argv_args: dict[str, Any]) -> list[str]:
        rendered: list[str] = []
        for token in command:
            if _is_placeholder(token):
                key = token.strip("{}")
                value = argv_args.get(key)
                if value is None:
                    # fall back to declared default (already in args_schema, so this is a
                    # safety net for direct .invoke with partial dicts)
                    value = params.get(key, {}).get("default", "")
                rendered.append(str(value))
            else:
                rendered.append(token)
        return _resolve_interpreter(rendered)

    def _run(**argv_args: Any) -> str:
        return _execute(_render(argv_args), skill_path)

    async def _arun(**argv_args: Any) -> str:
        return _execute(_render(argv_args), skill_path)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=name,
        description=description,
        args_schema=args_schema,
    )


# ── helpers ────────────────────────────────────────────────────


def _build_args_schema(tool_name: str, params: dict[str, Any]) -> type:
    """Build a pydantic model for the tool's parameters from the declaration."""
    fields: dict[str, Any] = {}
    for param_name, spec in params.items():
        spec = spec or {}
        py_type = _TYPE_MAP.get(spec.get("type", "string"), str)
        desc = spec.get("description", "")
        required = bool(spec.get("required", False))
        if "default" in spec:
            fields[param_name] = (py_type, Field(default=spec["default"], description=desc))
        elif required:
            fields[param_name] = (py_type, Field(..., description=desc))
        else:
            # optional, no declared default → None
            fields[param_name] = (py_type | None, Field(default=None, description=desc))
    # A paramless tool simply has an empty schema model (no fields).
    return create_model(f"{tool_name}_ArgsSchema", **fields)


def _is_placeholder(token: str) -> bool:
    return token.startswith("{") and token.endswith("}") and "{" not in token[1:-1]


def _resolve_interpreter(command: list[str]) -> list[str]:
    """Replace a bare ``python``/``python3`` interpreter token with ``sys.executable``."""
    if command and command[0].lower() in _PYTHON_TOKENS:
        command = [sys.executable, *command[1:]]
    return command


def _execute(command: list[str], cwd: Path) -> str:
    """Run the command, return trimmed stdout, or a failure string on non-zero exit."""
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        return f"Script failed (not found): {exc}"
    except subprocess.TimeoutExpired:
        return f"Script failed (timeout after {_TIMEOUT_SECONDS}s)"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return f"Script failed (exit {result.returncode}): {stderr}"
    return (result.stdout or "").strip()
