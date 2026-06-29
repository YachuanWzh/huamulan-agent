from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_SKILLS_DIR = str(Path(__file__).resolve().parent / "skills")
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    database_url: str = Field(
        default="postgresql://langchain_user:Deartyl0115@192.168.5.7:5432/langchain_db?sslmode=disable",
        alias="DATABASE_URL",
    )
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4.1-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    skills_dir: str = Field(default=DEFAULT_SKILLS_DIR, alias="SKILLS_DIR")
    assistant_workspace_dir: str = Field(
        default_factory=lambda: str(Path.cwd()),
        alias="ASSISTANT_WORKSPACE_DIR",
    )
    cors_origins: list[str] = Field(default=["http://localhost:5173"])

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
