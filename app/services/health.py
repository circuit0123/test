"""Dependency health checks: is each backing service reachable and usable?

Each check runs with a timeout and all run concurrently, so /health/deps answers
within ~timeout seconds even if a service hangs. Error details go to the logs;
the API response only carries the exception type, so it never leaks hostnames
or credentials.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas.health import DependencyStatus

log = structlog.get_logger("circuit.health")

# A check returns optional extra details, or raises if the service is unhealthy.
Check = Callable[[], Awaitable[dict[str, Any] | None]]

REQUIRED_PG_EXTENSIONS = ("postgis", "vector")


def postgres_check(engine: AsyncEngine) -> Check:
    async def check() -> dict[str, Any]:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT extname, extversion FROM pg_extension WHERE extname = ANY(:names)"),
                {"names": list(REQUIRED_PG_EXTENSIONS)},
            )
            extensions = {name: version for name, version in rows}
        missing = [e for e in REQUIRED_PG_EXTENSIONS if e not in extensions]
        if missing:
            raise RuntimeError(f"missing Postgres extensions: {', '.join(missing)}")
        return {"extensions": extensions}

    return check


def neo4j_check(driver: AsyncDriver) -> Check:
    async def check() -> None:
        await driver.execute_query("RETURN 1")

    return check


def redis_check(redis: Redis) -> Check:
    async def check() -> None:
        await redis.ping()

    return check


async def run_check(name: str, check: Check, timeout_s: float) -> DependencyStatus:
    start = time.perf_counter()
    try:
        details = await asyncio.wait_for(check(), timeout=timeout_s)
        ok, error = True, None
    except Exception as exc:  # any failure means "unhealthy", never a 500
        details = None
        ok = False
        error = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
        log.warning("dependency_check_failed", dependency=name, error=error, detail=str(exc))
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return DependencyStatus(ok=ok, latency_ms=latency_ms, error=error, details=details)


async def run_checks(checks: dict[str, Check], timeout_s: float) -> dict[str, DependencyStatus]:
    names = list(checks)
    results = await asyncio.gather(*(run_check(n, checks[n], timeout_s) for n in names))
    return dict(zip(names, results, strict=True))
