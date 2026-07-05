from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, computed_field, field_validator
from pydantic_settings import NoDecode
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
    checkpoint_ttl_seconds: int = Field(default=604800, alias="CHECKPOINT_TTL_SECONDS")
    checkpoint_pg_cleanup_enabled: bool = Field(
        default=True,
        alias="CHECKPOINT_PG_CLEANUP_ENABLED",
    )
    checkpoint_pg_cleanup_interval_seconds: int = Field(
        default=3600,
        alias="CHECKPOINT_PG_CLEANUP_INTERVAL_SECONDS",
    )
    checkpoint_redis_lru_enabled: bool = Field(
        default=True,
        alias="CHECKPOINT_REDIS_LRU_ENABLED",
    )
    checkpoint_redis_maxmemory_policy: str = Field(
        default="allkeys-lru",
        alias="CHECKPOINT_REDIS_MAXMEMORY_POLICY",
    )
    checkpoint_skip_nodes: Annotated[list[str], NoDecode] = Field(
        default=["route_skills", "compact_context"],
        alias="CHECKPOINT_SKIP_NODES",
    )
    skill_routing_semantic_enabled: bool = Field(
        default=False,
        alias="SKILL_ROUTING_SEMANTIC_ENABLED",
    )
    skill_routing_embedding_model: str = Field(
        default="bge-m3",
        alias="SKILL_ROUTING_EMBEDDING_MODEL",
    )
    skill_routing_ollama_base_url: str = Field(
        default="http://localhost:11434",
        alias="SKILL_ROUTING_OLLAMA_BASE_URL",
    )
    skill_routing_vector_store: str = Field(
        default="memory",
        alias="SKILL_ROUTING_VECTOR_STORE",
    )
    skill_routing_qdrant_url: str | None = Field(
        default=None,
        alias="SKILL_ROUTING_QDRANT_URL",
    )
    skill_routing_qdrant_api_key: str | None = Field(
        default=None,
        alias="SKILL_ROUTING_QDRANT_API_KEY",
    )
    skill_routing_qdrant_collection: str = Field(
        default="skill_routes",
        alias="SKILL_ROUTING_QDRANT_COLLECTION",
    )
    skill_routing_similarity_threshold: float = Field(
        default=0.72,
        alias="SKILL_ROUTING_SIMILARITY_THRESHOLD",
    )
    skill_routing_top_k: int = Field(default=3, alias="SKILL_ROUTING_TOP_K")
    skill_routing_rerank_enabled: bool = Field(
        default=False,
        alias="SKILL_ROUTING_RERANK_ENABLED",
    )
    skill_routing_rerank_model: str = Field(
        default="qllama/bge-reranker-v2-m3",
        alias="SKILL_ROUTING_RERANK_MODEL",
    )
    skill_routing_rerank_threshold: float = Field(
        default=0.72,
        alias="SKILL_ROUTING_RERANK_THRESHOLD",
    )
    skill_routing_rerank_top_k: int = Field(
        default=3,
        alias="SKILL_ROUTING_RERANK_TOP_K",
    )
    skill_routing_llm_retry_count: int = Field(
        default=1,
        alias="SKILL_ROUTING_LLM_RETRY_COUNT",
    )
    skill_routing_llm_model: str | None = Field(
        default=None,
        alias="SKILL_ROUTING_LLM_MODEL",
    )
    user_vector_retrieval_enabled: bool = Field(
        default=False,
        alias="USER_VECTOR_RETRIEVAL_ENABLED",
    )
    user_vector_qdrant_url: str | None = Field(
        default=None,
        alias="USER_VECTOR_QDRANT_URL",
    )
    user_vector_qdrant_api_key: str | None = Field(
        default=None,
        alias="USER_VECTOR_QDRANT_API_KEY",
    )
    user_vector_qdrant_collection: str = Field(
        default="user_memory",
        alias="USER_VECTOR_QDRANT_COLLECTION",
    )
    user_vector_top_k: int = Field(default=5, alias="USER_VECTOR_TOP_K")
    # ── Multi-agent Hybrid Intent Routing ──────────────────────────────
    multi_agent_intent_regex_threshold: float = Field(
        default=0.80,
        alias="MULTI_AGENT_INTENT_REGEX_THRESHOLD",
    )
    multi_agent_intent_semantic_enabled: bool = Field(
        default=True,
        alias="MULTI_AGENT_INTENT_SEMANTIC_ENABLED",
    )
    multi_agent_intent_semantic_threshold: float = Field(
        default=0.75,
        alias="MULTI_AGENT_INTENT_SEMANTIC_THRESHOLD",
    )
    multi_agent_intent_llm_enabled: bool = Field(
        default=True,
        alias="MULTI_AGENT_INTENT_LLM_ENABLED",
    )
    multi_agent_intent_llm_threshold: float = Field(
        default=0.60,
        alias="MULTI_AGENT_INTENT_LLM_THRESHOLD",
    )
    multi_agent_intent_llm_model: str | None = Field(
        default=None,
        alias="MULTI_AGENT_INTENT_LLM_MODEL",
    )
    evaluation_judge_enabled: bool = Field(
        default=True,
        alias="EVALUATION_JUDGE_ENABLED",
    )
    evaluation_judge_model: str = Field(
        default="deepseek-v4-pro",
        alias="EVALUATION_JUDGE_MODEL",
    )
    # ── OTEL Demo telemetry query endpoints ────────────────────────────
    otel_jaeger_api_url: str = Field(
        default="",
        alias="OTEL_JAEGER_API_URL",
    )
    otel_prometheus_proxy_url: str = Field(
        default="",
        alias="OTEL_PROMETHEUS_PROXY_URL",
    )
    # ── OTEL Push: Kafka Consumer ──────────────────────────────────
    otel_kafka_brokers: str = Field(
        default="localhost:9092",
        alias="OTEL_KAFKA_BROKERS",
    )
    otel_kafka_topic_spans: str = Field(
        default="otel-spans",
        alias="OTEL_KAFKA_TOPIC_SPANS",
    )
    otel_kafka_topic_metrics: str = Field(
        default="otel-metrics",
        alias="OTEL_KAFKA_TOPIC_METRICS",
    )
    otel_kafka_topic_logs: str = Field(
        default="otel-logs",
        alias="OTEL_KAFKA_TOPIC_LOGS",
    )
    otel_kafka_consumer_group: str = Field(
        default="langgraph-claw",
        alias="OTEL_KAFKA_CONSUMER_GROUP",
    )
    evaluation_judge_base_url: str | None = Field(
        default=None,
        alias="EVALUATION_JUDGE_BASE_URL",
    )
    evaluation_judge_api_key: str | None = Field(
        default=None,
        alias="EVALUATION_JUDGE_API_KEY",
    )
    prompt_guard_llm_enabled: bool = Field(
        default=True,
        alias="PROMPT_GUARD_LLM_ENABLED",
    )
    prompt_guard_llm_model: str = Field(
        default="deepseek-v4-flash",
        alias="PROMPT_GUARD_LLM_MODEL",
    )
    prompt_guard_llm_confidence_threshold: float = Field(
        default=0.8,
        alias="PROMPT_GUARD_LLM_CONFIDENCE_THRESHOLD",
    )

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        return value

    @field_validator("checkpoint_skip_nodes", mode="before")
    @classmethod
    def parse_checkpoint_skip_nodes(cls, value) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("skill_routing_vector_store")
    @classmethod
    def validate_skill_routing_vector_store(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"memory", "qdrant"}:
            raise ValueError("SKILL_ROUTING_VECTOR_STORE must be memory or qdrant")
        return normalized

    @field_validator("evaluation_judge_model")
    @classmethod
    def validate_evaluation_judge_model(cls, value: str) -> str:
        if "flash" in value.lower():
            raise ValueError("EVALUATION_JUDGE_MODEL must use a Pro model, not Flash")
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
