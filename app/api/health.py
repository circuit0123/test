from fastapi import APIRouter, Depends, Request, Response, status

from app.config import Settings, get_settings
from app.schemas.health import DependencyHealth, Health
from app.services.health import Check, neo4j_check, postgres_check, redis_check, run_checks

router = APIRouter(prefix="/health", tags=["health"])


def get_dependency_checks(request: Request) -> dict[str, Check]:
    """One check per backing service. Tests override this dependency with fakes."""
    res = request.app.state.resources
    return {
        "postgres": postgres_check(res.engine),
        "neo4j": neo4j_check(res.neo4j),
        "redis": redis_check(res.redis),
    }


@router.get(
    "",
    response_model=Health,
    summary="Liveness check",
    description="Returns 200 if the API process is up. Does not touch any dependency.",
)
async def health() -> Health:
    return Health()


@router.get(
    "/deps",
    response_model=DependencyHealth,
    summary="Dependency check",
    description=(
        "Checks Postgres (including the PostGIS and pgvector extensions), Neo4j and Redis. "
        "Returns 200 when all are healthy, 503 if any is not."
    ),
    responses={503: {"model": DependencyHealth, "description": "One or more dependencies are unhealthy."}},
)
async def health_deps(
    response: Response,
    checks: dict[str, Check] = Depends(get_dependency_checks),
    settings: Settings = Depends(get_settings),
) -> DependencyHealth:
    results = await run_checks(checks, settings.health_check_timeout_s)
    all_ok = all(r.ok for r in results.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return DependencyHealth(status="ok" if all_ok else "degraded", dependencies=results)
