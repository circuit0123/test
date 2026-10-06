import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.members import _LatLngPair
from app.schemas.needs import NeedOut

_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def normalise_domain(value: str) -> str:
    """'https://www.Acme.io/about' -> 'acme.io' (same rule the crawler will use)."""
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split("?")[0].split("#")[0]
    v = v.removeprefix("www.")
    if not _DOMAIN.match(v):
        raise ValueError("not a valid domain")
    return v


class StartupCreate(_LatLngPair):
    name: str = Field(min_length=1, max_length=200)
    website_domain: str = Field(examples=["acme.io"])
    description: str | None = Field(None, max_length=5000)
    sector: str | None = Field(None, max_length=50)
    stage: str | None = Field(None, max_length=30)
    address: str | None = Field(None, max_length=500)
    hiring: bool = False
    open_roles: list[str] = []
    tech_stack: list[str] = []
    title: str = Field("Founder", max_length=120, description="Your title on the team.")

    @field_validator("website_domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        return normalise_domain(value)


class StartupUpdate(_LatLngPair):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    sector: str | None = Field(None, max_length=50)
    stage: str | None = Field(None, max_length=30)
    address: str | None = Field(None, max_length=500)
    hiring: bool | None = None
    open_roles: list[str] | None = None
    tech_stack: list[str] | None = None


class StartupSummary(BaseModel):
    id: uuid.UUID
    name: str
    website_domain: str
    sector: str | None
    stage: str | None
    hiring: bool
    lat: float | None
    lng: float | None
    claimed: bool


class TeamMemberOut(BaseModel):
    member_id: uuid.UUID
    display_name: str
    title: str | None


class StartupDetail(StartupSummary):
    description: str | None
    address: str | None
    open_roles: list[str]
    tech_stack: list[str]
    source: Literal["seed", "crawler", "user"]
    claimed_by_member_id: uuid.UUID | None
    traits: list[str]
    team: list[TeamMemberOut]
    needs: list[NeedOut]
    created_at: datetime
    updated_at: datetime


class ClaimCreate(BaseModel):
    title: str | None = Field(None, max_length=120, description="Your title at the startup.")
    evidence: str = Field(
        min_length=10, max_length=2000,
        description="How a reviewer can confirm you work there (e.g. company email, LinkedIn, registration number).",
    )


class ClaimDecision(BaseModel):
    note: str | None = Field(None, max_length=2000)


class ClaimOut(BaseModel):
    id: uuid.UUID
    startup_id: uuid.UUID
    startup_name: str
    member_id: uuid.UUID
    title: str | None
    evidence: str | None
    status: Literal["pending", "approved", "rejected"]
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None
