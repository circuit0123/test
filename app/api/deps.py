"""Reusable FastAPI dependencies: who is calling, and are they allowed?

A dependency is a function FastAPI runs before your endpoint; its return value is
passed in as an argument. Example:

    @router.get("/matches")
    async def matches(me: CurrentMember = Depends(require_level(2))): ...

If the caller has no valid token -> 401. Valid token but not allowed -> 403.
"""

from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Member
from app.db.session import get_session
from app.resources import Resources
from app.services.auth import AuthError, CurrentMember, verify_token

log = structlog.get_logger("circuit.auth")

# auto_error=False: we raise our own 401 with a consistent message.
# This also adds the "Authorize" button to the /docs page.
bearer = HTTPBearer(auto_error=False, description="JWT from the identity provider (or /auth/dev/token locally).")

# Proposed verification rules from the spec.
LEVEL_BROWSE = 1
LEVEL_MATCHES = 2
LEVEL_INTROS = 3


def get_resources(request: Request) -> Resources:
    return request.app.state.resources


def _unauthorized(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Not authenticated: {reason}",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_member(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    resources: Resources = Depends(get_resources),
) -> CurrentMember:
    if credentials is None:
        raise _unauthorized("missing bearer token")
    try:
        claims = await verify_token(credentials.credentials, settings, resources.jwks_client)
    except AuthError as exc:
        log.info("auth_rejected", reason=str(exc))  # never log the token itself
        raise _unauthorized(str(exc)) from exc

    row = (
        await session.execute(
            select(Member.id, Member.role, Member.verification_level, Member.token_version).where(
                Member.id == claims.sub
            )
        )
    ).one_or_none()
    if row is None:
        raise _unauthorized("unknown member")
    # token_version is bumped on logout-everywhere or a role/level change, which
    # instantly invalidates every token issued before.
    if row.token_version != claims.token_version:
        log.info("auth_rejected", reason="token_version mismatch", member_id=str(claims.sub))
        raise _unauthorized("token revoked")

    request.state.user_id = str(row.id)  # picked up by the request log line
    return CurrentMember(id=row.id, role=row.role, verification_level=row.verification_level)


Dependency = Callable[..., Coroutine[Any, Any, CurrentMember]]


def require_level(level: int) -> Dependency:
    """Allow only members whose verification level is at least `level`."""

    async def dependency(member: CurrentMember = Depends(get_current_member)) -> CurrentMember:
        if member.verification_level < level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires verification level {level} (you have {member.verification_level})",
            )
        return member

    return dependency


def require_role(*roles: str) -> Dependency:
    """Allow only members with one of the given roles."""

    async def dependency(member: CurrentMember = Depends(get_current_member)) -> CurrentMember:
        if member.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires role: {', '.join(roles)}")
        return member

    return dependency


require_admin = require_role("partner_admin")
