"""FastAPI application: settings, logging, middleware, routers, startup/shutdown."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import auth, geo, health, matches, members, needs, reference, startups
from app.config import get_settings
from app.logging import RequestLoggingMiddleware, configure_logging
from app.resources import Resources
from app.services.errors import ServiceError

log = structlog.get_logger("circuit")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runs once at startup (before `yield`) and once at shutdown (after it).
    settings = get_settings()
    app.state.resources = Resources.from_settings(settings)
    scheduler = None
    if settings.scheduler_enabled:
        from app.jobs.scheduler import create_scheduler

        scheduler = create_scheduler(app.state.resources, settings)
        scheduler.start()
    log.info("startup", env=settings.env, scheduler=settings.scheduler_enabled)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
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

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(health.router)
    app.include_router(auth.router)
    if settings.dev_auth:  # config validation already refused this in production
        app.include_router(auth.dev_router)
    app.include_router(reference.router)
    app.include_router(members.router)
    app.include_router(needs.router)
    app.include_router(startups.router)
    app.include_router(geo.router)
    app.include_router(matches.router)
    return app


app = create_app()
