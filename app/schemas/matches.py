import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.members import MemberSummary


class MatchOut(BaseModel):
    member: MemberSummary
    rank: int
    score: float
    source: Literal["local", "bridge"] = Field(description="'bridge' = found by cross-sector search only.")
    reason: str
    components: dict[str, Any] = Field(description="The score's ingredients, for transparency.")


class MatchesResponse(BaseModel):
    computed_at: datetime | None
    served_from: Literal["cache", "database", "computed"] = Field(
        description="Where these results came from: Redis cache, stored results, or computed just now."
    )
    items: list[MatchOut]


class RecomputeResult(BaseModel):
    members: int
    results: int
    bridge_results: int
    seconds: float


class MatchRow(BaseModel):
    """What we cache in Redis and store in match_results for one result."""

    candidate_member_id: uuid.UUID
    rank: int
    score: float
    source: Literal["local", "bridge"]
    reason: str
    components: dict[str, Any]
