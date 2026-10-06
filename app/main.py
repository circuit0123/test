"""FastAPI application: settings, logging, middleware, routers, startup/shutdown."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api import health
from app.config import get_settings
from app.logging import RequestLoggingMiddleware, configure_logging
from app.resources import Resources

log = structlog.get_logger("circuit")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runs once at startup (before `yield`) and once at shutdown (after it).
    settings = get_settings()
    app.state.resources = Resources.from_settings(settings)
    log.info("startup", env=settings.env)
    try:
        yield
    finally:
        await app.state.resources.close()
        log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()  # validates env at import time: bad config = no start
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Circuit API",
        version="0.1.0",
        description="Backend for Circuit: startup discovery map and founder/mentor/investor matching.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(health.router)
    return app


app = create_app()
