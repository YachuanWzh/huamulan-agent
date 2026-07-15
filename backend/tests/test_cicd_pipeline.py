import subprocess
from pathlib import Path

import yaml


def test_pipeline_accepts_push_pull_request_and_manual_events() -> None:
    config_path = Path(__file__).resolve().parents[2] / ".woodpecker.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert set(config["when"]["event"]) == {"push", "pull_request", "manual"}


def test_repository_does_not_track_local_worktrees_as_gitlinks() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "ls-files", "--stage"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    worktree_gitlinks = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("160000 ") and "\t.claude/worktrees/" in line
    ]

    assert worktree_gitlinks == []
