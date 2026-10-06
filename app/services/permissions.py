"""Who may change what. Kept in one place so the rules are easy to review."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth import CurrentMember
from app.db.models import Startup, StartupMember


async def can_edit_startup(session: AsyncSession, me: CurrentMember, startup_id: uuid.UUID) -> bool:
    """The claimer, anyone on the team, or a partner_admin."""
    if me.is_admin:
        return True
    is_claimer = select(Startup.id).where(Startup.id == startup_id, Startup.claimed_by_member_id == me.id)
    on_team = select(StartupMember.member_id).where(
        StartupMember.startup_id == startup_id, StartupMember.member_id == me.id
    )
    return bool(await session.scalar(select(exists(is_claimer) | exists(on_team))))
