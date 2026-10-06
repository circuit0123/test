"""Application settings, read from environment variables (and a local .env file).

pydantic-settings maps each field to an env var of the same name, case-insensitive:
`database_url` <- DATABASE_URL. Types are validated at startup, so a typo such as
CITY_CENTER_LAT=abc fails fast instead of breaking later.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MatchingSettings(BaseModel):
    """Matching engine knobs. Override in .env as MATCHING__<NAME>, e.g. MATCHING__W_TIMING=0.2."""

    # score = w1*complementarity + w2*affinity + w3*trust_path + w4*timing - w5*load_penalty
    w_complementarity: float = 0.5
    w_affinity: float = 0.15
    w_trust_path: float = 0.15
    w_timing: float = 0.1
    w_load_penalty: float = 0.2

    # Need->offer fit = capability_weight * [same capability] + (1 - capability_weight) * similarity,
    # where similarity is cosine rescaled from [sim_floor, sim_ceiling] to [0, 1]. The embedding
    # model rates even unrelated texts ~0.55-0.6, so raw cosine would barely separate pairs.
    capability_weight: float = 0.6
    sim_floor: float = 0.55
    sim_ceiling: float = 0.85
    # Complementarity = (1 - mutual_weight) * stronger direction + mutual_weight * weaker direction.
    mutual_weight: float = 0.25

    min_complementarity: float = 0.3  # below this a pair is not a match at all
    results_per_member: int = 20
    bridge_share: float = 0.25  # share of each member's results reserved for bridge matches
    diversity_penalty: float = 0.05  # per already-picked result about the same capability
    role_cap_share: float = 0.5  # at most this share of results from one role
    max_appearances_per_candidate: int = 40  # stops one popular person flooding every list
    bridge_neighbours_per_need: int = 10  # nearest offers by embedding, per need
    # Feedback: never suggest people you already have a pending/accepted intro with,
    # and hide people who declined you, or whom you dismissed, for this many days.
    hide_declined_days: int = 30
    hide_dismissed_days: int = 30

    cache_ttl_hours: float = 12
    job_interval_hours: float = 4


class IntroSettings(BaseModel):
    """Intro request limits. Override in .env as INTROS__<NAME>."""

    max_pending_outgoing: int = 5  # open requests a member may have waiting at once
    max_requests_per_week: int = 10  # requests a member may send in any 7 days
    expire_after_days: int = 14  # unanswered requests expire
    decline_cooldown_days: int = 30  # after a decline, wait this long to ask the same person again
    connection_strength: float = 0.5  # strength of the connection an accepted intro creates


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_nested_delimiter="__"
    )

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

    # Geo search. Students always get a radius ("distance is a hard filter"):
    # the default if they don't ask, never more than the max.
    student_default_radius_km: float = 10.0
    student_max_radius_km: float = 25.0
    nearby_max_radius_km: float = 200.0

    matching: MatchingSettings = MatchingSettings()
    intros: IntroSettings = IntroSettings()
    # Background jobs (APScheduler) run inside the API process. Off in tests.
    scheduler_enabled: bool = True
    match_job_on_startup: bool = False  # also compute matches right after startup

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
