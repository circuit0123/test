"""Async SQLAlchemy engine and sessions.

The engine owns a pool of Postgres connections and is created once at startup.
A session is a short-lived unit of work: one per request, via `get_session`.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping: test a pooled connection before use, so a Postgres restart
    # doesn't surface as errors on the first few requests.
    return create_async_engine(database_url, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session and always closes it afterwards."""
    async with request.app.state.resources.sessionmaker() as session:
        yield session
