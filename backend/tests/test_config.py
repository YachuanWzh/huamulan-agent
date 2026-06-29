from pathlib import Path

from personal_assistant.config import Settings


def test_default_env_file_is_backend_env_file() -> None:
    env_file = Path(Settings.model_config["env_file"])

    assert env_file.is_absolute()
    assert env_file == Path(__file__).resolve().parents[1] / ".env"
