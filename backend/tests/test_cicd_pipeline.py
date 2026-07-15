from pathlib import Path

import yaml


def test_pipeline_accepts_push_pull_request_and_manual_events() -> None:
    config_path = Path(__file__).resolve().parents[2] / ".woodpecker.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert set(config["when"]["event"]) == {"push", "pull_request", "manual"}
