import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NeedText = Field(min_length=10, max_length=1000, description="What is needed or offered, in plain words.")
CapabilitySlug = Field(description="A capability slug from GET /capabilities.", examples=["growth-marketing"])


class NeedCreate(BaseModel):
    capability: str = CapabilitySlug
    text: str = NeedText
    expires_at: datetime | None = Field(None, description="Optional: the need stops matching after this time.")


class NeedUpdate(BaseModel):
    capability: str | None = None
    text: str | None = Field(None, min_length=10, max_length=1000)
    active: bool | None = None
    expires_at: datetime | None = None


class NeedOut(BaseModel):
    id: uuid.UUID
    owner_type: Literal["member", "startup"]
    owner_id: uuid.UUID
    capability: str
    text: str
    active: bool
    expires_at: datetime | None


class OfferCreate(BaseModel):
    capability: str = CapabilitySlug
    text: str = NeedText


class OfferUpdate(BaseModel):
    capability: str | None = None
    text: str | None = Field(None, min_length=10, max_length=1000)
    active: bool | None = None


class OfferOut(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    capability: str
    text: str
    active: bool
