from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentMember, get_current_member
from app.config import Settings, get_settings
from app.db.models import Member
from app.db.session import get_session
from app.schemas.auth import DevTokenRequest, Me, TokenResponse
from app.schemas.members import MemberSummary
from app.services.auth import issue_dev_token
from app.services.errors import NotFound

router = APIRouter(prefix="/auth", tags=["auth"])

# Mounted by main.py only when DEV_AUTH=true (which is refused in production).
dev_router = APIRouter(prefix="/auth/dev", tags=["auth (dev only)"])


@router.get(
    "/me",
    response_model=Me,
    summary="Who am I?",
    description="Returns the authenticated member's id, role and current verification level.",
)
async def me(member: CurrentMember = Depends(get_current_member)) -> Me:
    return Me(id=member.id, role=member.role, verification_level=member.verification_level)


@dev_router.post(
    "/token",
    response_model=TokenResponse,
    summary="Issue a local test token",
    description=(
        "DEV_AUTH only. Issues a signed JWT for an existing member, chosen by `member_id`, or by `role` "
        "(picks the most-verified member with that role). Paste the token into the Authorize button on /docs."
    ),
)
async def dev_token(
    body: DevTokenRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    stmt = select(Member)
    if body.member_id:
        stmt = stmt.where(Member.id == body.member_id)
    else:
        stmt = stmt.where(Member.role == body.role).order_by(Member.verification_level.desc(), Member.id).limit(1)
    member = await session.scalar(stmt)
    if member is None:
        raise NotFound("no such member (did you run the seed loader?)")
    token, expires_at = issue_dev_token(
        member_id=member.id, role=member.role, verification_level=member.verification_level,
        token_version=member.token_version, settings=settings,
    )
    return TokenResponse(
        access_token=token, expires_at=expires_at,
        member=MemberSummary(id=member.id, role=member.role, display_name=member.display_name,
                             city=member.city, verification_level=member.verification_level),
    )
