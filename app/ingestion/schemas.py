"""File formats the seed loader accepts.

`CrawlerStartup` is the contract with the future web crawler: the crawler must emit
exactly these fields. Everything a crawler could NOT know (a startup's stage, its
needs, its team) lives in `StartupProfile`, which in the real product comes from
founders claiming and editing their profile.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import CONNECTION_SOURCES, MEMBER_ROLES

MemberRole = Literal[MEMBER_ROLES]  # type: ignore[valid-type]
ConnectionSource = Literal[CONNECTION_SOURCES]  # type: ignore[valid-type]


class _Strict(BaseModel):
    # Reject unknown fields, so a typo in a seed file fails loudly.
    model_config = ConfigDict(extra="forbid")


class CrawlerStartup(_Strict):
    name: str
    website_domain: str = Field(pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")
    description: str | None = None
    sector: str | None = None
    address: str | None = None
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    hiring: bool = False
    open_roles: list[str] = []
    tech_stack: list[str] = []
    source: Literal["seed", "crawler"] = "crawler"


class SeedNeed(_Strict):
    id: uuid.UUID  # fixed id so reloading the same file updates instead of duplicating
    capability: str  # capability slug
    text: str


class SeedOffer(SeedNeed):
    pass


class TeamMember(_Strict):
    member_id: uuid.UUID
    title: str


class StartupProfile(_Strict):
    website_domain: str
    stage: str | None = None
    traits: list[str] = []
    needs: list[SeedNeed] = []
    team: list[TeamMember] = []


class SeedMember(_Strict):
    id: uuid.UUID
    role: MemberRole
    display_name: str
    bio: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    city: str | None = None
    verification_level: int = Field(1, ge=1, le=4)
    open_intro_slots: int = Field(3, ge=0)
    circuits_opt_in: bool = False
    open_to_cross_sector: bool = True
    traits: list[str] = []
    needs: list[SeedNeed] = []
    offers: list[SeedOffer] = []


class SeedConnection(_Strict):
    a: uuid.UUID
    b: uuid.UUID
    strength: float = Field(ge=0, le=1)
    source: ConnectionSource


class Capability(_Strict):
    slug: str
    name: str
    description: str | None = None


class Trait(_Strict):
    slug: str
    name: str
