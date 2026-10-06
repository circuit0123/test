import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ClientEventType = Literal["match_clicked", "match_dismissed", "profile_viewed", "startup_viewed"]


class ClientEvent(BaseModel):
    """Something only the frontend can see, e.g. a match card being clicked or dismissed."""

    type: ClientEventType
    target_type: Literal["member", "startup"]
    target_id: uuid.UUID
    payload: dict[str, Any] = Field(default_factory=dict, description="Small extra context, e.g. {'rank': 3}.")

    @field_validator("payload")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > 2000:
            raise ValueError("payload must be under 2000 characters of JSON")
        return value


class EventOut(BaseModel):
    id: int
    actor_id: uuid.UUID | None
    type: str
    target_type: str | None
    target_id: str | None
    payload: dict[str, Any]
    created_at: datetime
