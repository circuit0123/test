from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_BROWSE, CurrentMember, require_level
from app.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.geo import BboxResponse, NearbyResponse
from app.services import geo

router = APIRouter(prefix="/geo", tags=["geo"])
browse = require_level(LEVEL_BROWSE)


@router.get(
    "/startups/nearby",
    response_model=NearbyResponse,
    summary="Startups near a point",
    description=(
        "Startups nearest first, each with `distance_km`. Uses `lat`/`lng`, or your saved location if omitted. "
        "Filters: `radius_km`, `hiring`, and `capability` (startups with a live need for that capability slug, "
        "e.g. a student's skill). For students the radius is a hard filter: it defaults to 10 km and is capped "
        "at 25 km (the response says if it was capped). Others may omit it to search everywhere."
    ),
)
async def nearby(
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_km: float | None = Query(None, gt=0, le=200, description="Search radius in km."),
    hiring: bool | None = Query(None, description="Only hiring (true) or not hiring (false) startups."),
    capability: str | None = Query(None, description="Capability slug, e.g. frontend-development."),
    limit: int = Query(50, ge=1, le=200),
    me: CurrentMember = Depends(browse),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> NearbyResponse:
    return await geo.nearby(session, me, settings, lat=lat, lng=lng, radius_km=radius_km, hiring=hiring,
                            capability=capability, limit=limit)


@router.get(
    "/startups/bbox",
    response_model=BboxResponse,
    summary="Startups in a map viewport",
    description=(
        "Lightweight records (id, name, sector, hiring, lat, lng) for every startup inside the rectangle, for "
        "drawing map pins. If more than `limit` are in view, those nearest the centre are returned and "
        "`truncated` is true. A viewport crossing the 180° meridian must be sent as two requests."
    ),
)
async def bbox(
    min_lat: float = Query(ge=-90, le=90),
    min_lng: float = Query(ge=-180, le=180),
    max_lat: float = Query(ge=-90, le=90),
    max_lng: float = Query(ge=-180, le=180),
    hiring: bool | None = None,
    limit: int = Query(500, ge=1, le=2000),
    _: CurrentMember = Depends(browse),
    session: AsyncSession = Depends(get_session),
) -> BboxResponse:
    return await geo.in_bbox(session, min_lat=min_lat, min_lng=min_lng, max_lat=max_lat, max_lng=max_lng,
                             hiring=hiring, limit=limit)
