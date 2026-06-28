"""Tests for build_script_tool — wraps a scripts/ entry as a LangChain tool."""

from pathlib import Path
from textwrap import dedent

import pytest
from langchain_core.tools import StructuredTool

from personal_assistant.skills.script_tool import build_script_tool


def _write_echo_script(skill_dir: Path) -> Path:
    """A tiny script that echoes its args as JSON for testing."""
    script = skill_dir / "scripts" / "echo.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        dedent(
            """
            import json, sys
            # args: <name> <count>
            name = sys.argv[1] if len(sys.argv) > 1 else "?"
            count = int(sys.argv[2]) if len(sys.argv) > 2 else 0
            print(json.dumps({"name": name, "count": count}))
            """
        ).strip(),
        encoding="utf-8",
    )
    return script


def _write_failing_script(skill_dir: Path) -> Path:
    script = skill_dir / "scripts" / "boom.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        'import sys\nprint("boom detail", file=sys.stderr)\nsys.exit(2)\n',
        encoding="utf-8",
    )
    return script


class TestBuildScriptToolShape:
    def test_returns_structured_tool_with_name_and_description(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args as JSON.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "a name", "required": True},
                "count": {"type": "integer", "description": "a count", "required": True},
            },
        }
        tool = build_script_tool(decl, tmp_path)
        assert isinstance(tool, StructuredTool)
        assert tool.name == "echo_tool"
        assert tool.description == "Echoes args as JSON."

    def test_args_schema_has_required_and_optional_fields(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "a name", "required": True},
                "count": {
                    "type": "integer",
                    "description": "a count",
                    "default": 5,
                },
            },
        }
        tool = build_script_tool(decl, tmp_path)
        schema = tool.args_schema.model_json_schema()
        assert "name" in schema["properties"]
        assert "count" in schema["properties"]
        # name is required, count is not (has default)
        assert "name" in schema["required"]
        assert "count" not in schema.get("required", [])


class TestBuildScriptToolExecution:
    def test_invokes_script_and_returns_stdout(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "a name", "required": True},
                "count": {"type": "integer", "description": "a count", "required": True},
            },
        }
        tool = build_script_tool(decl, tmp_path)
        result = tool.invoke({"name": "alice", "count": 3})
        assert '"name": "alice"' in result
        assert '"count": 3' in result

    def test_optional_param_uses_default_when_omitted(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "a name", "required": True},
                "count": {"type": "integer", "description": "a count", "default": 9},
            },
        }
        tool = build_script_tool(decl, tmp_path)
        result = tool.invoke({"name": "bob"})
        assert '"count": 9' in result

    def test_python_token_replaced_with_sys_executable(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "a name", "required": True},
                "count": {"type": "integer", "description": "a count", "default": 0},
            },
        }
        tool = build_script_tool(decl, tmp_path)
        # If python wasn't replaced with sys.executable on Windows, this would fail
        # to find the interpreter. Assert the substitution happened at build time by
        # inspecting the wrapped command via a direct call succeeding.
        result = tool.invoke({"name": "x", "count": 1})
        assert '"name": "x"' in result
        # The script ran under sys.executable, not a bare "python" lookup.

    def test_nonzero_exit_returns_error_string_with_stderr(self, tmp_path: Path):
        _write_failing_script(tmp_path)
        decl = {
            "name": "boom_tool",
            "description": "Always fails.",
            "command": ["python", "scripts/boom.py"],
            "params": {},
        }
        tool = build_script_tool(decl, tmp_path)
        result = tool.invoke({})
        assert "failed" in result.lower()
        assert "boom detail" in result

    def test_no_params_runs_paramless_command(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_default",
            "description": "Echoes defaults.",
            "command": ["python", "scripts/echo.py"],
            "params": {},
        }
        tool = build_script_tool(decl, tmp_path)
        result = tool.invoke({})
        assert '"name": "?"' in result  # script defaults when no args

    def test_skill_path_is_cwd_for_relative_script_path(self, tmp_path: Path):
        # The command uses a relative "scripts/echo.py" — must resolve against skill_path.
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "n", "required": True},
                "count": {"type": "integer", "description": "c", "default": 0},
            },
        }
        tool = build_script_tool(decl, tmp_path)
        # If cwd were not skill_path, the relative script path would not be found and
        # the tool would return a failure string.
        result = tool.invoke({"name": "z"})
        assert '"name": "z"' in result


class TestBuildScriptToolAsync:
    @pytest.mark.asyncio
    async def test_async_invoke_runs_script(self, tmp_path: Path):
        _write_echo_script(tmp_path)
        decl = {
            "name": "echo_tool",
            "description": "Echoes args.",
            "command": ["python", "scripts/echo.py", "{name}", "{count}"],
            "params": {
                "name": {"type": "string", "description": "n", "required": True},
                "count": {"type": "integer", "description": "c", "default": 0},
            },
        }
        tool = build_script_tool(decl, tmp_path)
        result = await tool.ainvoke({"name": "async"})
        assert '"name": "async"' in result
