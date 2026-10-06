import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.needs import NeedOut, OfferOut

Role = Literal["student", "founder", "mentor", "investor", "partner_admin"]


class _LatLngPair(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def _both_or_neither(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be given together")
        return self


class MemberSummary(BaseModel):
    id: uuid.UUID
    role: Role
    display_name: str
    city: str | None
    verification_level: int


class MemberStartup(BaseModel):
    startup_id: uuid.UUID
    name: str
    title: str | None


class MemberProfile(MemberSummary):
    """What other members can see. Exact location is never shared, only the city."""

    bio: str | None
    traits: list[str]
    offers: list[OfferOut]
    needs: list[NeedOut]
    startups: list[MemberStartup]


class MemberMe(MemberProfile):
    """Your own profile, including private settings and inactive needs/offers."""

    lat: float | None
    lng: float | None
    open_intro_slots: int
    circuits_opt_in: bool
    open_to_cross_sector: bool
    created_at: datetime


class MemberUpdate(_LatLngPair):
    display_name: str | None = Field(None, min_length=1, max_length=120)
    bio: str | None = Field(None, max_length=2000)
    city: str | None = Field(None, max_length=100)
    open_intro_slots: int | None = Field(None, ge=0, le=20)
    circuits_opt_in: bool | None = None
    open_to_cross_sector: bool | None = None


class MemberCreate(_LatLngPair):
    role: Role
    display_name: str = Field(min_length=1, max_length=120)
    bio: str | None = Field(None, max_length=2000)
    city: str | None = Field(None, max_length=100)
    verification_level: int = Field(1, ge=1, le=4)


class TraitsUpdate(BaseModel):
    traits: list[str] = Field(description="Trait slugs from GET /traits. Replaces your current traits.")


class VerificationUpdate(BaseModel):
    verification_level: int = Field(ge=1, le=4)
