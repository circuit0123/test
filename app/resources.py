"""Long-lived connections shared by the whole app, opened at startup, closed at shutdown."""

from dataclasses import dataclass, field
from functools import cached_property

import jwt
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.redis import create_redis
from app.db.session import create_engine, create_sessionmaker
from app.embeddings.base import EmbeddingProvider
from app.embeddings.factory import get_embedding_provider
from app.graph.client import create_neo4j_driver
from app.services.auth import create_jwks_client


@dataclass
class Resources:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    neo4j: AsyncDriver
    jwks_client: jwt.PyJWKClient | None = field(default=None)

    @cached_property
    def embedder(self) -> EmbeddingProvider:
        # Created on first use: loading the real model takes a moment, and many
        # requests (and most tests) never need it.
        return get_embedding_provider(self.settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Resources":
        engine = create_engine(settings.database_url)
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            redis=create_redis(settings.redis_url),
            neo4j=create_neo4j_driver(
                settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password.get_secret_value()
            ),
            jwks_client=create_jwks_client(settings),
        )

    async def close(self) -> None:
        await self.neo4j.close()
        await self.redis.aclose()
        await self.engine.dispose()
