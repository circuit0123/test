"""Application settings, read from environment variables (and a local .env file).

pydantic-settings maps each field to an env var of the same name, case-insensitive:
`database_url` <- DATABASE_URL. Types are validated at startup, so a typo such as
CITY_CENTER_LAT=abc fails fast instead of breaking later.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Postgres (source of truth). psycopg 3 async driver.
    database_url: str = "postgresql+psycopg://circuit:circuit@localhost:5432/circuit"

    # Neo4j (derived graph copy, rebuildable from Postgres).
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("circuit-dev-password")

    # Redis (cache, disposable).
    redis_url: str = "redis://localhost:6379/0"

    # Seconds each dependency health check may take before it counts as failed.
    health_check_timeout_s: float = 2.0

    # Centre point for generated seed data.
    city_center_lat: float = 12.9716
    city_center_lng: float = 77.5946
    city_name: str = "Bengaluru"

    # Embeddings: "fastembed" (real local model) or "fake" (deterministic, for tests).
    embedding_provider: Literal["fastembed", "fake"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str | None = None  # default: fastembed's own cache folder

    # Local-only test JWT issuer (built in Phase 2). Never allowed in production.
    dev_auth: bool = False

    @model_validator(mode="after")
    def _refuse_dev_auth_in_production(self) -> "Settings":
        if self.dev_auth and self.env == "production":
            raise ValueError("DEV_AUTH=true is not allowed when ENV=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
