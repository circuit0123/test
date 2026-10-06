from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_MATCHES, CurrentMember, get_resources, require_admin, require_level
from app.db.session import get_session
from app.resources import Resources
from app.schemas.matches import MatchesResponse, RecomputeResult
from app.services import matching

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get(
    "",
    response_model=MatchesResponse,
    summary="My matches",
    description=(
        "Level 2+. Your ranked matches, each with a one-line reason and the score components. Served from the "
        "Redis cache, falling back to stored results, or computed now if you have none yet. Results are "
        "refreshed for everyone every few hours; pass `refresh=true` to recompute yours immediately "
        "(e.g. after editing your needs or offers)."
    ),
)
async def my_matches(
    limit: int = Query(20, ge=1, le=100),
    refresh: bool = Query(False, description="Recompute your matches now instead of using stored results."),
    me: CurrentMember = Depends(require_level(LEVEL_MATCHES)),
    session: AsyncSession = Depends(get_session),
    resources: Resources = Depends(get_resources),
) -> MatchesResponse:
    settings = resources.settings.matching
    if refresh:
        computed_at = datetime.now(UTC)
        rows = await matching.compute_for_member(session, me.id, settings, now=computed_at)
        await matching.store(session, me.id, rows, computed_at)
        await matching.cache(resources.redis, me.id, rows, computed_at, settings.cache_ttl_hours)
    response = await matching.get_matches(session, resources.redis, me.id, settings, limit)
    if refresh:
        response.served_from = "computed"
    return response


@router.post(
    "/recompute",
    response_model=RecomputeResult,
    summary="Recompute all matches (admin)",
    description="partner_admin only. Runs the background match job now for every member and waits for it.",
)
async def recompute(
    _: CurrentMember = Depends(require_admin),
    resources: Resources = Depends(get_resources),
) -> RecomputeResult:
    return await matching.recompute_all(resources.sessionmaker, resources.redis, resources.settings.matching)
