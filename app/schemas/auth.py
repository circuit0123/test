import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.members import MemberSummary, Role


class DevTokenRequest(BaseModel):
    member_id: uuid.UUID | None = Field(None, description="Issue a token for this member.")
    role: Role | None = Field(None, description="Or: pick the most-verified seeded member with this role.")

    @model_validator(mode="after")
    def _one_of(self):
        if (self.member_id is None) == (self.role is None):
            raise ValueError("give exactly one of member_id or role")
        return self


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    member: MemberSummary


class Me(BaseModel):
    id: uuid.UUID
    role: Role
    verification_level: int
