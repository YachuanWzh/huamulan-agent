import subprocess
from pathlib import Path

import yaml


def _pipeline_config() -> dict:
    config_path = Path(__file__).resolve().parents[2] / ".woodpecker.yml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def test_pipeline_accepts_push_pull_request_and_manual_events() -> None:
    config = _pipeline_config()

    assert set(config["when"]["event"]) == {"push", "pull_request", "manual"}


def test_pipeline_uses_container_reachable_python_package_index() -> None:
    steps = _pipeline_config()["steps"]

    for step_name in ("lint-backend", "test-backend"):
        environment = steps[step_name]["environment"]
        assert environment["PIP_INDEX_URL"] == "https://mirrors.aliyun.com/pypi/simple/"
        assert environment["UV_DEFAULT_INDEX"] == "https://mirrors.aliyun.com/pypi/simple/"


def test_backend_tests_receive_required_offline_settings() -> None:
    environment = _pipeline_config()["steps"]["test-backend"]["environment"]
    required_settings = {
        "DATABASE_URL": "postgresql://test:test@127.0.0.1:5432/test",
        "LLM_MODEL": "test-model",
        "OTEL_JAEGER_API_URL": "http://jaeger.test/api",
        "OTEL_PROMETHEUS_PROXY_URL": "http://prometheus.test/api/v1",
    }

    assert {name: environment[name] for name in required_settings} == required_settings


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
