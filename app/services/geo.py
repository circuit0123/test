"""Geographic startup search with PostGIS.

Key PostGIS pieces used here:
- ST_DWithin(a, b, metres): "within this distance?" Uses the GIST index, so it
  skips far-away rows without measuring each one.
- a <-> b: distance operator for ORDER BY. With a GIST index, Postgres walks the
  index outward from the point ("K-nearest-neighbour"), so "nearest 50" is fast.
- ST_Distance(a, b): exact distance in metres (on the WGS84 spheroid for geography).
- a && box: "bounding boxes overlap", the index-friendly first cut for map viewports.
"""

from dataclasses import dataclass

from geoalchemy2 import Geography, Geometry
from sqlalchemy import cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.geo import from_point
from app.db.models import Capability, Member, Need, Startup
from app.schemas.geo import BboxResponse, LatLng, MapStartup, NearbyResponse, NearbyStartup
from app.services.auth import CurrentMember
from app.services.errors import Invalid


def _geog_point(lat: float, lng: float):
    # PostGIS wants longitude first.
    return cast(func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326), Geography(srid=4326))


@dataclass(frozen=True)
class RadiusDecision:
    radius_km: float | None
    capped: bool


def decide_radius(role: str, requested_km: float | None, settings: Settings) -> RadiusDecision:
    """Students always get a radius (hard filter); others may search without one."""
    if role == "student":
        if requested_km is None:
            return RadiusDecision(settings.student_default_radius_km, False)
        if requested_km > settings.student_max_radius_km:
            return RadiusDecision(settings.student_max_radius_km, True)
        return RadiusDecision(requested_km, False)
    return RadiusDecision(requested_km, False)


async def resolve_center(
    session: AsyncSession, me: CurrentMember, lat: float | None, lng: float | None
) -> tuple[float, float]:
    """Use the given point, or fall back to the caller's saved location."""
    if (lat is None) != (lng is None):
        raise Invalid("lat and lng must be given together")
    if lat is not None and lng is not None:
        return lat, lng
    saved = from_point(await session.scalar(select(Member.location).where(Member.id == me.id)))
    if saved is None:
        raise Invalid("give lat and lng, or save your location with PATCH /members/me")
    return saved


def _needs_capability(slug: str):
    """Startups with a live need for this capability (e.g. a student's skill)."""
    return (
        select(Need.id).join(Capability)
        .where(Need.owner_type == "startup", Need.owner_id == Startup.id, Capability.slug == slug, Need.active,
               or_(Need.expires_at.is_(None), Need.expires_at > func.now()))
        .exists()
    )


async def nearby(
    session: AsyncSession, me: CurrentMember, settings: Settings, *, lat: float | None, lng: float | None,
    radius_km: float | None, hiring: bool | None, capability: str | None, limit: int,
) -> NearbyResponse:
    lat, lng = await resolve_center(session, me, lat, lng)
    radius = decide_radius(me.role, radius_km, settings)
    point = _geog_point(lat, lng)
    distance_m = func.ST_Distance(Startup.location, point).label("distance_m")

    stmt = select(Startup, distance_m).where(Startup.location.isnot(None))
    if radius.radius_km is not None:
        stmt = stmt.where(func.ST_DWithin(Startup.location, point, radius.radius_km * 1000))
    if hiring is not None:
        stmt = stmt.where(Startup.hiring == hiring)
    if capability:
        stmt = stmt.where(_needs_capability(capability))
    stmt = stmt.order_by(Startup.location.op("<->")(point), Startup.id).limit(limit)

    items = []
    for s, metres in (await session.execute(stmt)).all():
        s_lat, s_lng = from_point(s.location)
        items.append(NearbyStartup(
            id=s.id, name=s.name, website_domain=s.website_domain, sector=s.sector, stage=s.stage,
            address=s.address, hiring=s.hiring, open_roles=s.open_roles, lat=s_lat, lng=s_lng,
            distance_km=round(metres / 1000, 3),
        ))
    return NearbyResponse(center=LatLng(lat=lat, lng=lng), radius_km=radius.radius_km,
                          radius_capped=radius.capped, items=items)


async def in_bbox(
    session: AsyncSession, *, min_lat: float, min_lng: float, max_lat: float, max_lng: float,
    hiring: bool | None, limit: int,
) -> BboxResponse:
    if min_lat >= max_lat:
        raise Invalid("min_lat must be less than max_lat")
    if min_lng >= max_lng:
        raise Invalid("min_lng must be less than max_lng (a box crossing the 180° meridian must be split in two)")

    envelope = cast(func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326), Geography(srid=4326))
    as_geom = cast(Startup.location, Geometry)
    stmt = (
        select(Startup.id, Startup.name, Startup.sector, Startup.hiring, Startup.location)
        # 1) && uses the GIST index to discard everything clearly outside the view...
        .where(Startup.location.op("&&")(envelope))
        # 2) ...then an exact lat/lng check, so the result matches the map rectangle exactly.
        .where(func.ST_Y(as_geom).between(min_lat, max_lat), func.ST_X(as_geom).between(min_lng, max_lng))
    )
    if hiring is not None:
        stmt = stmt.where(Startup.hiring == hiring)
    # If there are too many to show, keep the ones nearest the centre of the view.
    center = _geog_point((min_lat + max_lat) / 2, (min_lng + max_lng) / 2)
    stmt = stmt.order_by(Startup.location.op("<->")(center), Startup.id).limit(limit + 1)

    rows = (await session.execute(stmt)).all()
    truncated = len(rows) > limit
    items = []
    for sid, name, sector, hiring_flag, location in rows[:limit]:
        s_lat, s_lng = from_point(location)
        items.append(MapStartup(id=sid, name=name, sector=sector, hiring=hiring_flag, lat=s_lat, lng=s_lng))
    return BboxResponse(items=items, count=len(items), truncated=truncated)


