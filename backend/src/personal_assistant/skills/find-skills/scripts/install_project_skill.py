#!/usr/bin/env python3
"""Install a GitHub skill into this project's skills directory."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


PACKAGE_RE = re.compile(r"^(?P<owner>[^/\s]+)/(?P<repo>[^@\s]+)@(?P<skill>[^@\s/]+)$")
CLONE_TIMEOUT_SECONDS = 120
DOWNLOAD_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class PackageSpec:
    owner: str
    repo: str
    skill: str

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"


def parse_package_spec(raw: str) -> PackageSpec:
    spec = raw.strip()
    match = PACKAGE_RE.match(spec)
    if not match:
        raise ValueError(
            "Expected package spec in owner/repo@skill-name form, "
            f"got {raw!r}."
        )
    return PackageSpec(
        owner=match.group("owner"),
        repo=match.group("repo"),
        skill=match.group("skill"),
    )


def copy_skill_from_repo(repo_dir: Path, skill_name: str, target_dir: Path) -> Path:
    source = find_skill_dir(repo_dir, skill_name)
    destination = target_dir.resolve() / skill_name
    if destination.exists():
        raise FileExistsError(f"Destination skill already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return destination


def find_skill_dir(repo_dir: Path, skill_name: str) -> Path:
    candidates = [
        path
        for path in repo_dir.rglob("SKILL.md")
        if path.parent.name == skill_name or skill_md_declares_name(path, skill_name)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"Could not find a skill directory named {skill_name!r} containing SKILL.md."
        )
    candidates.sort(key=lambda path: len(path.parts))
    return candidates[0].parent


def skill_md_declares_name(path: Path, skill_name: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    parts = text.split("---", 2)
    if len(parts) < 3:
        return False
    for line in parts[1].splitlines():
        if not line.strip().startswith("name:"):
            continue
        declared = line.split(":", 1)[1].strip().strip("\"'")
        return declared == skill_name
    return False


def clone_repo(package: PackageSpec, destination: Path) -> None:
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        package.clone_url,
        str(destination),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "GitHub clone failed. Check Git network/proxy access to github.com:443. "
            f"git output: {stderr}"
        )


def download_repo_archive(package: PackageSpec, destination: Path) -> None:
    errors: list[str] = []
    for branch in ("main", "master"):
        url = f"https://codeload.github.com/{package.owner}/{package.repo}/zip/refs/heads/{branch}"
        try:
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
                archive_path = Path(temp_file.name)
            try:
                urllib.request.urlretrieve(url, archive_path)
                extract_repo_archive(archive_path, destination)
                return
            finally:
                archive_path.unlink(missing_ok=True)
        except (urllib.error.URLError, OSError, zipfile.BadZipFile) as exc:
            errors.append(f"{branch}: {exc}")
    raise RuntimeError(
        "GitHub archive download failed for main/master branches. "
        + " | ".join(errors)
    )


def extract_repo_archive(archive_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="project-skill-zip-") as temp:
        temp_dir = Path(temp)
        with zipfile.ZipFile(archive_path) as zip_file:
            zip_file.extractall(temp_dir)
        roots = [path for path in temp_dir.iterdir() if path.is_dir()]
        if not roots:
            raise FileNotFoundError("Archive did not contain a repository root directory.")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(roots[0], destination)


def fetch_repo(package: PackageSpec, destination: Path) -> None:
    archive_error = None
    try:
        download_repo_archive(package, destination)
        return
    except Exception as exc:
        archive_error = exc
    try:
        clone_repo(package, destination)
    except Exception as clone_error:
        raise RuntimeError(
            "Could not download skill repository by GitHub archive or git clone. "
            f"Archive error: {archive_error}. Clone error: {clone_error}"
        ) from clone_error


def install_project_skill(
    package_spec: str,
    target_dir: str,
    *,
    fetch_repo: Callable[[PackageSpec, Path], None] = fetch_repo,
) -> Path:
    package = parse_package_spec(package_spec)
    target = Path(target_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="project-skill-") as temp:
        repo_dir = Path(temp) / package.repo
        fetch_repo(package, repo_dir)
        return copy_skill_from_repo(repo_dir, package.skill, target)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "Usage: install_project_skill.py <owner/repo@skill-name> <target-dir>",
            file=sys.stderr,
        )
        return 2
    try:
        installed = install_project_skill(argv[1], argv[2])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Installed project skill to {installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
