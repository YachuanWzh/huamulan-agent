from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import StructuredTool


DEFAULT_MAX_READ_BYTES = 200_000
DEFAULT_MAX_SEARCH_BYTES = 1_000_000


def build_basic_tools(workspace_dir: str | Path) -> list[StructuredTool]:
    workspace = Path(workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    def shell_command(
        command: str,
        cwd: str = ".",
        timeout_seconds: int = 30,
    ) -> str:
        """Run a shell command inside the assistant workspace."""
        workdir = _resolve_inside_workspace(workspace, cwd)
        if isinstance(workdir, str):
            return workdir
        timeout = max(1, min(int(timeout_seconds), 120))
        try:
            result = subprocess.run(
                command,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Script failed (timeout after {timeout}s)"
        except OSError as exc:
            return f"Script failed: {exc}"

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        parts = [f"exit_code={result.returncode}"]
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        return "\n".join(parts)

    def read_file(path: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> str:
        """Read a UTF-8 text file from the assistant workspace."""
        target = _resolve_inside_workspace(workspace, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return f"FileNotFoundError: {path}"
        if not target.is_file():
            return f"IsADirectoryError: {path}"
        limit = max(1, min(int(max_bytes), DEFAULT_MAX_READ_BYTES))
        data = target.read_bytes()
        suffix = ""
        if len(data) > limit:
            data = data[:limit]
            suffix = f"\n\n[truncated after {limit} bytes]"
        return data.decode("utf-8", errors="replace") + suffix

    def write_file(path: str, content: str, append: bool = False) -> str:
        """Write UTF-8 text to a file in the assistant workspace."""
        target = _resolve_inside_workspace(workspace, path)
        if isinstance(target, str):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8", newline="") as fh:
            fh.write(content)
        byte_count = len(content.encode("utf-8"))
        action = "appended" if append else "wrote"
        return f"{action} {byte_count} bytes to {_relative(target, workspace)}"

    def list_directory(path: str = ".") -> str:
        """List direct children of a directory in the assistant workspace."""
        target = _resolve_inside_workspace(workspace, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return f"FileNotFoundError: {path}"
        if not target.is_dir():
            return f"NotADirectoryError: {path}"
        rows = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            suffix = "/" if child.is_dir() else ""
            rows.append(f"{_relative(child, workspace)}{suffix}")
        return "\n".join(rows)

    def search_files(
        query: str,
        path: str = ".",
        glob: str = "*",
        max_results: int = 50,
    ) -> str:
        """Search file names and UTF-8 text content in the assistant workspace."""
        target = _resolve_inside_workspace(workspace, path)
        if isinstance(target, str):
            return target
        if not target.exists():
            return f"FileNotFoundError: {path}"
        if not target.is_dir():
            return f"NotADirectoryError: {path}"
        limit = max(1, min(int(max_results), 200))
        matches: list[str] = []
        needle = query.lower()
        for candidate in sorted(target.rglob(glob), key=lambda p: str(p).lower()):
            if not candidate.is_file():
                continue
            rel = _relative(candidate, workspace)
            if needle in rel.lower() or _file_contains(candidate, needle):
                matches.append(rel)
            if len(matches) >= limit:
                break
        return "\n".join(matches)

    return [
        StructuredTool.from_function(
            shell_command,
            name="shell_command",
            description="Run a shell command inside the assistant workspace.",
        ),
        StructuredTool.from_function(
            read_file,
            name="read_file",
            description="Read a UTF-8 text file from the assistant workspace.",
        ),
        StructuredTool.from_function(
            write_file,
            name="write_file",
            description="Write or append UTF-8 text to a file in the assistant workspace.",
        ),
        StructuredTool.from_function(
            list_directory,
            name="list_directory",
            description="List direct children of a workspace directory.",
        ),
        StructuredTool.from_function(
            search_files,
            name="search_files",
            description="Search file names and UTF-8 text content in the assistant workspace.",
        ),
    ]


def _resolve_inside_workspace(workspace: Path, raw_path: str) -> Path | str:
    try:
        target = (workspace / raw_path).resolve()
    except OSError as exc:
        return f"SecurityError: invalid path: {exc}"
    if target != workspace and workspace not in target.parents:
        return f"SecurityError: path is outside workspace: {raw_path}"
    return target


def _relative(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _file_contains(path: Path, needle: str) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if len(data) > DEFAULT_MAX_SEARCH_BYTES:
        data = data[:DEFAULT_MAX_SEARCH_BYTES]
    text = data.decode("utf-8", errors="ignore").lower()
    return needle in text
