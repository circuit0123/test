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

    # Real auth: verify JWTs against the identity provider's public keys (JWKS).
    # Switching provider (Clerk, Supabase, ...) only changes these values.
    jwks_url: str | None = None
    jwt_issuer: str | None = None  # expected "iss" claim; checked when set
    jwt_audience: str | None = None  # expected "aud" claim; checked when set
    jwt_algorithms: list[str] = ["RS256"]
    jwks_cache_seconds: int = 300

    # Local-only test login: POST /auth/dev/token issues tokens for seeded members,
    # signed with DEV_AUTH_SECRET. Never allowed in production.
    dev_auth: bool = False
    dev_auth_secret: SecretStr | None = None
    dev_token_ttl_minutes: int = 12 * 60

    @model_validator(mode="after")
    def _check_auth_settings(self) -> "Settings":
        if self.dev_auth and self.env == "production":
            raise ValueError("DEV_AUTH=true is not allowed when ENV=production")
        if self.dev_auth:
            secret = self.dev_auth_secret.get_secret_value() if self.dev_auth_secret else ""
            if len(secret) < 32:
                raise ValueError("DEV_AUTH=true needs DEV_AUTH_SECRET of at least 32 characters")
        # Shared-secret algorithms on the JWKS path would let anyone who knows the
        # public key forge tokens ("algorithm confusion"), so only allow key pairs.
        if any(alg.upper().startswith("HS") or alg.lower() == "none" for alg in self.jwt_algorithms):
            raise ValueError("JWT_ALGORITHMS must be asymmetric (e.g. RS256, ES256)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
