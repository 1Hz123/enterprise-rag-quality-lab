"""Runtime configuration.

All values can be overridden with ``RAG_LAB_``-prefixed environment variables
(for example ``RAG_LAB_TOP_K=8``) or with a local ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _cache_env(name: str) -> AliasChoices:
    """Accept both the documented exact key and the project's prefixed key."""

    return AliasChoices(name, f"RAG_LAB_{name}")


class Settings(BaseSettings):
    """Application settings with safe defaults for local development."""

    model_config = SettingsConfigDict(
        env_prefix="RAG_LAB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_name: str = "Enterprise RAG Quality Lab"
    app_version: str = "1.2.1"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    database_path: Path = PROJECT_ROOT / "data" / "rag_lab.db"
    sample_docs_dir: Path = PROJECT_ROOT / "data" / "sample_docs"
    eval_dataset_path: Path = PROJECT_ROOT / "eval" / "dataset.jsonl"
    eval_results_dir: Path = PROJECT_ROOT / "eval" / "results"

    auto_ingest_sample_docs: bool = True
    chunk_size: int = Field(default=760, ge=200, le=4000)
    chunk_overlap: int = Field(default=110, ge=0, le=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    min_retrieval_score: float = Field(default=0.22, ge=0.0, le=1.0)
    min_query_coverage: float = Field(default=0.30, ge=0.0, le=1.0)
    max_question_chars: int = Field(default=2000, ge=10, le=10000)
    max_document_bytes: int = Field(default=2_000_000, ge=1024, le=20_000_000)

    embedding_provider: Literal["hashing"] = "hashing"
    embedding_dimension: int = Field(default=384, ge=64, le=4096)

    generator_provider: Literal["extractive", "openai"] = "extractive"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = Field(default=45.0, ge=1.0, le=180.0)

    max_context_chars: int = Field(default=9000, ge=1000, le=100_000)
    max_answer_chars: int = Field(default=1200, ge=100, le=10000)
    experience_reuse_enabled: bool = True
    experience_min_quality: float = Field(default=0.75, ge=0.0, le=1.0)

    # Cache is opt-in: false must work with no Redis available.
    cache_enabled: bool = Field(
        default=False,
        validation_alias=_cache_env("CACHE_ENABLED"),
    )
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        validation_alias=_cache_env("REDIS_URL"),
    )
    cache_key_prefix: str = Field(
        default="raglab",
        min_length=1,
        max_length=80,
        validation_alias=_cache_env("CACHE_KEY_PREFIX"),
    )
    cache_env: str = Field(
        default="local",
        min_length=1,
        max_length=40,
        validation_alias=_cache_env("CACHE_ENV"),
    )
    # Current corpus is global. If ACL/tenant filtering is added, set a
    # per-deployment/per-request scope so private results never share keys.
    cache_scope: str = Field(
        default="public",
        min_length=1,
        max_length=120,
        validation_alias=_cache_env("CACHE_SCOPE"),
    )
    embedding_ttl: int = Field(
        default=604800,
        ge=3600,
        le=2592000,
        validation_alias=_cache_env("EMBEDDING_TTL"),
    )
    retrieval_ttl: int = Field(
        default=1800,
        ge=60,
        le=7200,
        validation_alias=_cache_env("RETRIEVAL_TTL"),
    )
    answer_ttl: int = Field(
        default=900,
        ge=60,
        le=7200,
        validation_alias=_cache_env("ANSWER_TTL"),
    )
    cache_ttl_jitter: float = Field(
        default=0.10,
        ge=0.0,
        le=0.50,
        validation_alias=_cache_env("CACHE_TTL_JITTER"),
    )
    redis_connect_timeout: float = Field(
        default=0.50,
        gt=0.0,
        le=1.0,
        validation_alias=_cache_env("REDIS_CONNECT_TIMEOUT"),
    )
    redis_socket_timeout: float = Field(
        default=0.10,
        gt=0.0,
        le=1.0,
        validation_alias=_cache_env("REDIS_SOCKET_TIMEOUT"),
    )
    redis_max_connections: int = Field(
        default=20,
        ge=1,
        le=200,
        validation_alias=_cache_env("REDIS_MAX_CONNECTIONS"),
    )
    redis_pool_wait_timeout: float = Field(
        default=0.10,
        gt=0.0,
        le=1.0,
        validation_alias=_cache_env("REDIS_POOL_WAIT_TIMEOUT"),
    )
    redis_health_check_interval: int = Field(
        default=30,
        ge=0,
        le=300,
        validation_alias=_cache_env("REDIS_HEALTH_CHECK_INTERVAL"),
    )
    cache_fail_open_cooldown: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        validation_alias=_cache_env("CACHE_FAIL_OPEN_COOLDOWN"),
    )
    cache_error_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        validation_alias=_cache_env("CACHE_ERROR_THRESHOLD"),
    )
    cache_singleflight_timeout: float = Field(
        default=1.0,
        ge=0.05,
        le=10.0,
        validation_alias=_cache_env("CACHE_SINGLEFLIGHT_TIMEOUT"),
    )
    negative_cache_enabled: bool = Field(
        default=False,
        validation_alias=_cache_env("NEGATIVE_CACHE_ENABLED"),
    )
    retrieval_negative_ttl: int = Field(
        default=60,
        ge=5,
        le=600,
        validation_alias=_cache_env("RETRIEVAL_NEGATIVE_TTL"),
    )
    # Reserved optional layer. It is never mixed into exact-cache metrics.
    semantic_cache_enabled: bool = Field(
        default=False,
        validation_alias=_cache_env("SEMANTIC_CACHE_ENABLED"),
    )

    @model_validator(mode="after")
    def _validate_ranges(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if self.chunk_overlap > self.chunk_size // 2:
            raise ValueError("chunk_overlap must not exceed half of chunk_size")
        if self.generator_provider == "openai" and not self.openai_api_key:
            raise ValueError("RAG_LAB_OPENAI_API_KEY is required when generator_provider=openai")
        return self

    def resolve_paths(self) -> Settings:
        """Resolve relative paths against the repository root.

        This keeps the application reproducible when it is started from a
        directory other than the repository root.
        """

        path_fields = (
            "database_path",
            "sample_docs_dir",
            "eval_dataset_path",
            "eval_results_dir",
        )
        for field_name in path_fields:
            value = getattr(self, field_name)
            if not value.is_absolute():
                setattr(self, field_name, (PROJECT_ROOT / value).resolve())
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings().resolve_paths()


def build_settings(**overrides: object) -> Settings:
    """Build fresh settings, primarily for tests and scripts."""

    return Settings(_env_file=None, **overrides).resolve_paths()
