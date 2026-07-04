#!/usr/bin/env python3
"""Search public skills with structured output."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable


CLI_TIMEOUT_SECONDS = 45
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
PACKAGE_RE = re.compile(r"(?P<package>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+)")
INSTALLS_RE = re.compile(r"(?P<installs>[0-9.]+[KMB]?\s+installs)", re.IGNORECASE)
URL_RE = re.compile(r"https://skills\.sh/\S+")
KNOWN_UNINSTALLABLE_PACKAGES: set[str] = set()


def run_skills_cli(query: str) -> str:
    result = subprocess.run(
        skills_cli_command(query),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    return "\n".join(part for part in [result.stdout, result.stderr] if part)


def skills_cli_command(query: str) -> list[str]:
    executable = shutil.which("npx")
    if executable is None and os.name == "nt":
        executable = shutil.which("npx.cmd")
    return [executable or "npx", "--yes", "skills", "find", query]


def search_skills(
    query: str,
    *,
    run_cli: Callable[[str], str] = run_skills_cli,
) -> dict:
    raw_output = ""
    cli_error = None
    try:
        raw_output = run_cli(query)
    except Exception as exc:
        cli_error = f"{exc.__class__.__name__}: {exc}"

    parsed = parse_cli_output(raw_output)
    if parsed:
        return {
            "query": query,
            "source": "skills-cli",
            "results": parsed,
            "note": "Parsed package specs from Skills CLI output.",
        }

    return {
        "query": query,
        "source": "none",
        "results": [],
        "note": "Skills CLI produced no parseable output.",
        "cli_error": cli_error,
    }


def parse_cli_output(output: str) -> list[dict[str, str]]:
    clean = ANSI_RE.sub("", output or "")
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    results: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        package_match = PACKAGE_RE.search(line)
        if not package_match:
            continue
        package = package_match.group("package")
        if package == "owner/repo@skill" or package in KNOWN_UNINSTALLABLE_PACKAGES:
            continue
        installs_match = INSTALLS_RE.search(line)
        url = _nearest_url(lines, index)
        results.append(
            {
                "package": package,
                "installs": installs_match.group("installs") if installs_match else "",
                "url": url,
            }
        )
    return results


def _nearest_url(lines: list[str], index: int) -> str:
    for line in lines[index + 1 : index + 3]:
        url_match = URL_RE.search(line)
        if url_match:
            return url_match.group(0)
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: search_public_skills.py <query>", file=sys.stderr)
        return 2
    result = search_skills(argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
