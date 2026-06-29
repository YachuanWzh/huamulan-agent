from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from personal_assistant.config import Settings


def test_default_env_file_is_backend_env_file() -> None:
    env_file = Path(Settings.model_config["env_file"])

    assert env_file.is_absolute()
    assert env_file == Path(__file__).resolve().parents[1] / ".env"


def test_langfuse_disabled_by_default() -> None:
    """Langfuse is opt-in — disabled when keys are missing."""
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )
        assert settings.langfuse_enabled is False
        assert settings.langfuse_public_key is None
        assert settings.langfuse_secret_key is None
        assert settings.langfuse_host == "https://cloud.langfuse.com"


def test_langfuse_enabled_when_keys_are_set() -> None:
    """Langfuse is enabled when both credentials are provided."""
    with patch.dict("os.environ", {
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
    }, clear=False):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )
        assert settings.langfuse_enabled is True


def test_langfuse_custom_host() -> None:
    """Custom Langfuse host is respected."""
    with patch.dict("os.environ", {
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
        "LANGFUSE_HOST": "https://selfhosted.example.com",
    }, clear=False):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )
        assert settings.langfuse_host == "https://selfhosted.example.com"


def test_langfuse_disabled_when_only_public_key_is_set() -> None:
    """Langfuse stays disabled when only the public key is configured."""
    with patch.dict("os.environ", {
        "LANGFUSE_PUBLIC_KEY": "pk-test",
    }, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )
        assert settings.langfuse_enabled is False


def test_langfuse_disabled_when_only_secret_key_is_set() -> None:
    """Langfuse stays disabled when only the secret key is configured."""
    with patch.dict("os.environ", {
        "LANGFUSE_SECRET_KEY": "sk-test",
    }, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )
        assert settings.langfuse_enabled is False
