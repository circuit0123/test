"""Long-lived connections shared by the whole app, opened at startup, closed at shutdown."""

from dataclasses import dataclass

from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.redis import create_redis
from app.db.session import create_engine, create_sessionmaker
from app.graph.client import create_neo4j_driver


@dataclass
class Resources:
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    neo4j: AsyncDriver

    @classmethod
    def from_settings(cls, settings: Settings) -> "Resources":
        engine = create_engine(settings.database_url)
        return cls(
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            redis=create_redis(settings.redis_url),
            neo4j=create_neo4j_driver(
                settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password.get_secret_value()
            ),
        )

    async def close(self) -> None:
        await self.neo4j.close()
        await self.redis.aclose()
        await self.engine.dispose()
