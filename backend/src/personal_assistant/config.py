from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SKILLS_DIR = str(Path(__file__).resolve().parent / "skills")
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    database_url: str = Field(
        default=...,
        alias="DATABASE_URL",
    )
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    llm_model: str = Field(default=..., alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    skills_dir: str = Field(default=DEFAULT_SKILLS_DIR, alias="SKILLS_DIR")
    assistant_workspace_dir: str = Field(
        default_factory=lambda: str(Path.cwd()),
        alias="ASSISTANT_WORKSPACE_DIR",
    )
    long_term_memory_dir: str | None = Field(default=None, alias="LONG_TERM_MEMORY_DIR")
    transcript_dir: str | None = Field(default=None, alias="TRANSCRIPT_DIR")
    context_compaction_message_count: int = Field(
        default=20,
        alias="CONTEXT_COMPACTION_MESSAGE_COUNT",
    )
    context_compaction_token_threshold: int = Field(
        default=1_000_000,
        alias="CONTEXT_COMPACTION_TOKEN_THRESHOLD",
    )
    cors_origins: list[str] = Field(
        default=["http://localhost:5173"], alias="CORS_ORIGINS"
    )
    langfuse_public_key: str | None = Field(
        default=None, alias="LANGFUSE_PUBLIC_KEY"
    )
    langfuse_secret_key: str | None = Field(
        default=None, alias="LANGFUSE_SECRET_KEY"
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com", alias="LANGFUSE_HOST"
    )
    cache_enabled: bool = Field(default=True, alias="CACHE_ENABLED")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    cache_default_ttl_seconds: int = Field(default=10, alias="CACHE_DEFAULT_TTL_SECONDS")
    cache_log_ttl_seconds: int = Field(default=5, alias="CACHE_LOG_TTL_SECONDS")
    cache_memory_ttl_seconds: int = Field(default=60, alias="CACHE_MEMORY_TTL_SECONDS")

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        return value

    @computed_field
    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
