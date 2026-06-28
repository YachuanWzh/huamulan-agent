from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(
        default="postgresql://langchain_user:Deartyl0115@192.168.5.7:5432/langchain_db?sslmode=disable",
        alias="DATABASE_URL",
    )
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4.1-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.2, alias="LLM_TEMPERATURE")
    skills_dir: str = Field(default="backend/src/personal_assistant/skills", alias="SKILLS_DIR")
    cors_origins: list[str] = Field(default=["http://localhost:5173"])

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
