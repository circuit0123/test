import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.members import MemberSummary

IntroStatus = Literal["pending", "accepted", "declined", "expired", "cancelled"]


class IntroCreate(BaseModel):
    to_member: uuid.UUID
    reason: str = Field(min_length=10, max_length=1000, description="Why you'd like to meet; shown to them.")
    via_member: uuid.UUID | None = Field(
        None, description="Optional warm intro: a mutual connection of you both (see Phase 6 warm paths)."
    )


class IntroResponse(BaseModel):
    note: str | None = Field(None, max_length=1000, description="Optional message back to the requester.")


class IntroRating(BaseModel):
    rating: int = Field(ge=1, le=5, description="How useful the intro turned out to be, 1-5.")


class IntroOut(BaseModel):
    id: uuid.UUID
    from_member: MemberSummary
    to_member: MemberSummary
    via_member: MemberSummary | None
    status: IntroStatus
    reason: str | None
    response_note: str | None
    match_source: Literal["local", "bridge"] | None = Field(
        description="If this request came from a recommendation: which kind."
    )
    created_at: datetime
    responded_at: datetime | None
    expires_at: datetime | None = Field(description="When a pending request expires unanswered.")
    my_rating: int | None = Field(description="Your outcome rating, once accepted.")


class IntroLimits(BaseModel):
    """Your current capacity, so the app can explain why a button is disabled."""

    pending_outgoing: int
    max_pending_outgoing: int
    sent_last_7_days: int
    max_per_week: int
    pending_incoming: int
    open_intro_slots: int
