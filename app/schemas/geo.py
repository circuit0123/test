import uuid

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float
    lng: float


class NearbyStartup(BaseModel):
    id: uuid.UUID
    name: str
    website_domain: str
    sector: str | None
    stage: str | None
    address: str | None
    hiring: bool
    open_roles: list[str]
    lat: float
    lng: float
    distance_km: float = Field(description="Straight-line distance from the search centre, in km.")


class NearbyResponse(BaseModel):
    center: LatLng
    radius_km: float | None = Field(description="The radius actually applied (null = no limit).")
    radius_capped: bool = Field(description="True if your requested radius was reduced to the maximum allowed.")
    items: list[NearbyStartup]


class MapStartup(BaseModel):
    """Lightweight record for drawing a map pin."""

    id: uuid.UUID
    name: str
    sector: str | None
    hiring: bool
    lat: float
    lng: float


class BboxResponse(BaseModel):
    items: list[MapStartup]
    count: int
    truncated: bool = Field(description="True if more startups are in view than `limit`; zoom in to see all.")
