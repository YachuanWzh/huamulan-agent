from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_cache_defaults_to_enabled_without_redis_url() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )

    assert settings.cache_enabled is True
    assert settings.redis_url is None
    assert settings.cache_default_ttl_seconds == 10
    assert settings.cache_log_ttl_seconds == 5
    assert settings.cache_memory_ttl_seconds == 60


def test_stream_prompt_guard_llm_defaults_to_disabled_for_ttft() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )

    assert settings.prompt_guard_llm_enabled is True
    assert settings.prompt_guard_llm_stream_enabled is False


def test_checkpoint_storage_defaults() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )

    assert settings.checkpoint_ttl_seconds == 604800
    assert settings.checkpoint_pg_cleanup_enabled is True
    assert settings.checkpoint_pg_cleanup_interval_seconds == 3600
    assert settings.checkpoint_redis_lru_enabled is True
    assert settings.checkpoint_redis_maxmemory_policy == "allkeys-lru"
    assert settings.checkpoint_skip_nodes == ["route_skills", "compact_context"]


def test_checkpoint_storage_overrides_from_env() -> None:
    with patch.dict(
        "os.environ",
        {
            "CHECKPOINT_TTL_SECONDS": "3600",
            "CHECKPOINT_PG_CLEANUP_ENABLED": "false",
            "CHECKPOINT_PG_CLEANUP_INTERVAL_SECONDS": "120",
            "CHECKPOINT_REDIS_LRU_ENABLED": "false",
            "CHECKPOINT_REDIS_MAXMEMORY_POLICY": "volatile-lru",
            "CHECKPOINT_SKIP_NODES": "route_skills, compact_context ,custom_node",
        },
        clear=True,
    ):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )

    assert settings.checkpoint_ttl_seconds == 3600
    assert settings.checkpoint_pg_cleanup_enabled is False
    assert settings.checkpoint_pg_cleanup_interval_seconds == 120
    assert settings.checkpoint_redis_lru_enabled is False
    assert settings.checkpoint_redis_maxmemory_policy == "volatile-lru"
    assert settings.checkpoint_skip_nodes == [
        "route_skills",
        "compact_context",
        "custom_node",
    ]


def test_skill_routing_semantic_defaults_to_disabled() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )

    assert settings.skill_routing_semantic_enabled is False
    assert settings.skill_routing_embedding_model == "bge-m3"
    assert settings.skill_routing_ollama_base_url == "http://localhost:11434"
    assert settings.skill_routing_vector_store == "memory"
    assert settings.skill_routing_qdrant_url is None
    assert settings.skill_routing_qdrant_api_key is None
    assert settings.skill_routing_qdrant_collection == "skill_routes"
    assert settings.skill_routing_similarity_threshold == 0.72
    assert settings.skill_routing_top_k == 3
    assert settings.skill_routing_rerank_enabled is False
    assert settings.skill_routing_rerank_model == "qllama/bge-reranker-v2-m3"
    assert settings.skill_routing_rerank_threshold == 0.72
    assert settings.skill_routing_rerank_top_k == 3
    assert settings.skill_routing_llm_retry_count == 1
    assert settings.skill_routing_llm_model is None


def test_skill_routing_semantic_can_be_enabled_from_env() -> None:
    with patch.dict(
        "os.environ",
        {
            "SKILL_ROUTING_SEMANTIC_ENABLED": "true",
            "SKILL_ROUTING_EMBEDDING_MODEL": "custom-bge",
            "SKILL_ROUTING_OLLAMA_BASE_URL": "http://ollama.local:11434",
            "SKILL_ROUTING_VECTOR_STORE": "qdrant",
            "SKILL_ROUTING_QDRANT_URL": "http://qdrant.example.test:6333",
            "SKILL_ROUTING_QDRANT_API_KEY": "qdrant-key",
            "SKILL_ROUTING_QDRANT_COLLECTION": "assistant_skill_routes",
            "SKILL_ROUTING_SIMILARITY_THRESHOLD": "0.81",
            "SKILL_ROUTING_TOP_K": "5",
            "SKILL_ROUTING_RERANK_ENABLED": "true",
            "SKILL_ROUTING_RERANK_MODEL": "custom-reranker",
            "SKILL_ROUTING_RERANK_THRESHOLD": "0.88",
            "SKILL_ROUTING_RERANK_TOP_K": "4",
            "SKILL_ROUTING_LLM_RETRY_COUNT": "2",
            "SKILL_ROUTING_LLM_MODEL": "deepseek-v4-flash",
        },
        clear=False,
    ):
        settings = Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            _env_file=None,
        )

    assert settings.skill_routing_semantic_enabled is True
    assert settings.skill_routing_embedding_model == "custom-bge"
    assert settings.skill_routing_ollama_base_url == "http://ollama.local:11434"
    assert settings.skill_routing_vector_store == "qdrant"
    assert settings.skill_routing_qdrant_url == "http://qdrant.example.test:6333"
    assert settings.skill_routing_qdrant_api_key == "qdrant-key"
    assert settings.skill_routing_qdrant_collection == "assistant_skill_routes"
    assert settings.skill_routing_similarity_threshold == 0.81
    assert settings.skill_routing_top_k == 5
    assert settings.skill_routing_rerank_enabled is True
    assert settings.skill_routing_rerank_model == "custom-reranker"
    assert settings.skill_routing_rerank_threshold == 0.88
    assert settings.skill_routing_rerank_top_k == 4
    assert settings.skill_routing_llm_retry_count == 2
    assert settings.skill_routing_llm_model == "deepseek-v4-flash"


def test_env_example_documents_skill_routing_services() -> None:
    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )

    assert "SKILL_ROUTING_SEMANTIC_ENABLED" in env_example
    assert "SKILL_ROUTING_OLLAMA_BASE_URL" in env_example
    assert "SKILL_ROUTING_EMBEDDING_MODEL" in env_example
    assert "SKILL_ROUTING_VECTOR_STORE" in env_example
    assert "SKILL_ROUTING_QDRANT_URL" in env_example
    assert "SKILL_ROUTING_QDRANT_COLLECTION" in env_example
    assert "SKILL_ROUTING_RERANK_ENABLED" in env_example
    assert "SKILL_ROUTING_RERANK_MODEL" in env_example
    assert "SKILL_ROUTING_RERANK_THRESHOLD" in env_example
    assert "SKILL_ROUTING_RERANK_TOP_K" in env_example
    assert "SKILL_ROUTING_LLM_MODEL" in env_example


def test_redis_url_accepts_redis_scheme() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        REDIS_URL="redis://redis.example.local:6379/0",
        _env_file=None,
    )

    assert settings.redis_url == "redis://redis.example.local:6379/0"


def test_redis_url_rejects_http_scheme() -> None:
    with pytest.raises(ValueError, match="REDIS_URL must use redis:// or rediss://"):
        Settings(
            DATABASE_URL="postgresql://localhost/test",
            LLM_MODEL="test-model",
            REDIS_URL="http://redis.example.local:6379",
            _env_file=None,
        )


def test_multi_agent_intent_settings_defaults() -> None:
    """MULTI_AGENT_INTENT_* 配置项默认值"""
    settings = Settings(
        DATABASE_URL="postgresql://localhost/test",
        LLM_MODEL="test-model",
        _env_file=None,
    )
    assert settings.multi_agent_intent_regex_threshold == 0.80
    assert settings.multi_agent_intent_semantic_enabled is True
    assert settings.multi_agent_intent_semantic_threshold == 0.75
    assert settings.multi_agent_intent_llm_enabled is True
    assert settings.multi_agent_intent_llm_threshold == 0.60
    assert settings.multi_agent_intent_llm_model is None


def test_multi_agent_intent_settings_from_env(monkeypatch) -> None:
    """环境变量重写 MULTI_AGENT_INTENT_*"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("MULTI_AGENT_INTENT_REGEX_THRESHOLD", "0.85")
    monkeypatch.setenv("MULTI_AGENT_INTENT_SEMANTIC_ENABLED", "false")
    monkeypatch.setenv("MULTI_AGENT_INTENT_SEMANTIC_THRESHOLD", "0.70")
    monkeypatch.setenv("MULTI_AGENT_INTENT_LLM_ENABLED", "false")
    monkeypatch.setenv("MULTI_AGENT_INTENT_LLM_THRESHOLD", "0.50")
    monkeypatch.setenv("MULTI_AGENT_INTENT_LLM_MODEL", "deepseek-v4-flash")

    settings = Settings(_env_file=None)
    assert settings.multi_agent_intent_regex_threshold == 0.85
    assert settings.multi_agent_intent_semantic_enabled is False
    assert settings.multi_agent_intent_semantic_threshold == 0.70
    assert settings.multi_agent_intent_llm_enabled is False
    assert settings.multi_agent_intent_llm_threshold == 0.50
    assert settings.multi_agent_intent_llm_model == "deepseek-v4-flash"
