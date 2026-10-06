"""Startup profiles and the claiming flow.

Claiming: a member who works at a startup that was added by the crawler or seed
asks to own its profile (POST /startups/{id}/claims). A partner_admin checks the
evidence and approves or rejects. Approval makes the member the claimer and adds
them to the team; any other pending claims for that startup are rejected.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.geo import from_point, to_point
from app.db.models import Member, Startup, StartupClaim, StartupMember, StartupTrait, Trait
from app.embeddings.base import EmbeddingProvider
from app.schemas.common import Page, Pagination
from app.schemas.needs import NeedCreate, NeedOut
from app.schemas.startups import (
    ClaimCreate,
    ClaimOut,
    StartupCreate,
    StartupDetail,
    StartupSummary,
    StartupUpdate,
    TeamMemberOut,
)
from app.services.auth import CurrentMember
from app.services.errors import Conflict, Forbidden, Invalid, NotFound
from app.services.needs import create_need, list_needs
from app.services.permissions import can_edit_startup

_NOT_NULL = {"name", "hiring", "open_roles", "tech_stack"}


def _summary(s: Startup) -> StartupSummary:
    lat, lng = from_point(s.location) or (None, None)
    return StartupSummary(id=s.id, name=s.name, website_domain=s.website_domain, sector=s.sector, stage=s.stage,
                          hiring=s.hiring, lat=lat, lng=lng, claimed=s.claimed_by_member_id is not None)


async def _get(session: AsyncSession, startup_id: uuid.UUID, *, for_update: bool = False) -> Startup:
    stmt = select(Startup).where(Startup.id == startup_id)
    if for_update:
        stmt = stmt.with_for_update()  # lock the row until commit (no two approvals at once)
    startup = await session.scalar(stmt)
    if startup is None:
        raise NotFound("startup not found")
    return startup


async def list_startups(
    session: AsyncSession, page: Pagination, *, sector: str | None = None, hiring: bool | None = None,
    claimed: bool | None = None, q: str | None = None,
) -> Page[StartupSummary]:
    stmt = select(Startup)
    if sector:
        stmt = stmt.where(Startup.sector == sector)
    if hiring is not None:
        stmt = stmt.where(Startup.hiring == hiring)
    if claimed is not None:
        stmt = stmt.where(Startup.claimed_by_member_id.isnot(None) if claimed else Startup.claimed_by_member_id.is_(None))
    if q:
        stmt = stmt.where(Startup.name.ilike(f"%{q}%"))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.scalars(stmt.order_by(Startup.name, Startup.id).limit(page.limit).offset(page.offset))
    return Page(items=[_summary(s) for s in rows], total=total or 0, limit=page.limit, offset=page.offset)


async def get_detail(session: AsyncSession, startup_id: uuid.UUID) -> StartupDetail:
    s = await _get(session, startup_id)
    traits = await session.scalars(
        select(Trait.slug).join(StartupTrait).where(StartupTrait.startup_id == s.id).order_by(Trait.slug)
    )
    team = (await session.execute(
        select(Member.id, Member.display_name, StartupMember.title).join(StartupMember)
        .where(StartupMember.startup_id == s.id).order_by(Member.display_name)
    )).all()
    return StartupDetail(
        **_summary(s).model_dump(), description=s.description, address=s.address, open_roles=s.open_roles,
        tech_stack=s.tech_stack, source=s.source, claimed_by_member_id=s.claimed_by_member_id,
        traits=list(traits), team=[TeamMemberOut(member_id=i, display_name=n, title=t) for i, n, t in team],
        needs=await list_needs(session, "startup", s.id, only_live=True),
        created_at=s.created_at, updated_at=s.updated_at,
    )


async def create_startup(session: AsyncSession, me: CurrentMember, data: StartupCreate) -> StartupDetail:
    """A member adds a startup the crawler hasn't found; they own it from the start."""
    existing = await session.scalar(select(Startup.id).where(Startup.website_domain == data.website_domain))
    if existing:
        raise Conflict(f"a startup with domain {data.website_domain} already exists ({existing}); claim it instead")
    s = Startup(
        name=data.name, website_domain=data.website_domain, description=data.description, sector=data.sector,
        stage=data.stage, address=data.address, location=to_point(data.lat, data.lng), hiring=data.hiring,
        open_roles=data.open_roles, tech_stack=data.tech_stack, source="user", claimed_by_member_id=me.id,
    )
    session.add(s)
    try:
        await session.flush()  # assigns the row now, so the team link below can reference it
    except IntegrityError as exc:  # someone created the same domain a moment ago
        await session.rollback()
        raise Conflict(f"a startup with domain {data.website_domain} already exists") from exc
    session.add(StartupMember(startup_id=s.id, member_id=me.id, title=data.title))
    await session.commit()
    await session.refresh(s)  # reload what the database stored (location, timestamps)
    return await get_detail(session, s.id)


async def update_startup(
    session: AsyncSession, me: CurrentMember, startup_id: uuid.UUID, data: StartupUpdate
) -> StartupDetail:
    s = await _get(session, startup_id)
    if not await can_edit_startup(session, me, startup_id):
        raise Forbidden("only the startup's team can edit it (claim it first)")
    changes = data.model_dump(exclude_unset=True)
    for key in _NOT_NULL & changes.keys():
        if changes[key] is None:
            raise Invalid(f"{key} cannot be null")
    if "lat" in changes or "lng" in changes:
        s.location = to_point(changes.pop("lat", None), changes.pop("lng", None))
    for key, value in changes.items():
        setattr(s, key, value)
    await session.commit()
    await session.refresh(s)
    return await get_detail(session, startup_id)


async def delete_startup(session: AsyncSession, me: CurrentMember, startup_id: uuid.UUID) -> None:
    s = await _get(session, startup_id)
    if not (me.is_admin or s.claimed_by_member_id == me.id):
        raise Forbidden("only the claimer or a partner_admin can delete a startup")
    await session.delete(s)
    await session.commit()


async def add_need(
    session: AsyncSession, embedder: EmbeddingProvider, me: CurrentMember, startup_id: uuid.UUID, data: NeedCreate
) -> NeedOut:
    await _get(session, startup_id)
    if not await can_edit_startup(session, me, startup_id):
        raise Forbidden("only the startup's team can add its needs")
    return await create_need(session, embedder, "startup", startup_id, data)


# ---------- claiming ----------

def _claim_out(c: StartupClaim, startup_name: str) -> ClaimOut:
    return ClaimOut(id=c.id, startup_id=c.startup_id, startup_name=startup_name, member_id=c.member_id,
                    title=c.title, evidence=c.evidence, status=c.status, decision_note=c.decision_note,
                    created_at=c.created_at, decided_at=c.decided_at)


async def create_claim(session: AsyncSession, me: CurrentMember, startup_id: uuid.UUID, data: ClaimCreate) -> ClaimOut:
    s = await _get(session, startup_id)
    if s.claimed_by_member_id is not None:
        raise Conflict("this startup has already been claimed")
    claim = StartupClaim(startup_id=s.id, member_id=me.id, title=data.title, evidence=data.evidence)
    session.add(claim)
    try:
        await session.commit()
    except IntegrityError as exc:  # the partial unique index: one pending claim per member
        await session.rollback()
        raise Conflict("you already have a pending claim for this startup") from exc
    await session.refresh(claim)  # load created_at/status set by the database
    return _claim_out(claim, s.name)


async def list_claims(
    session: AsyncSession, page: Pagination, *, status: str | None = None, member_id: uuid.UUID | None = None
) -> Page[ClaimOut]:
    stmt = select(StartupClaim, Startup.name).join(Startup)
    if status:
        stmt = stmt.where(StartupClaim.status == status)
    if member_id:
        stmt = stmt.where(StartupClaim.member_id == member_id)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(
        stmt.order_by(StartupClaim.created_at, StartupClaim.id).limit(page.limit).offset(page.offset)
    )).all()
    return Page(items=[_claim_out(c, n) for c, n in rows], total=total or 0, limit=page.limit, offset=page.offset)


async def _pending_claim(session: AsyncSession, claim_id: uuid.UUID) -> StartupClaim:
    claim = await session.get(StartupClaim, claim_id, with_for_update=True)
    if claim is None:
        raise NotFound("claim not found")
    if claim.status != "pending":
        raise Conflict(f"claim is already {claim.status}")
    return claim


async def decide_claim(
    session: AsyncSession, admin: CurrentMember, claim_id: uuid.UUID, *, approve: bool, note: str | None
) -> ClaimOut:
    claim = await _pending_claim(session, claim_id)
    startup = await _get(session, claim.startup_id, for_update=True)
    now = datetime.now(UTC)
    if approve:
        if startup.claimed_by_member_id is not None:
            raise Conflict("this startup has already been claimed")
        startup.claimed_by_member_id = claim.member_id
        await session.merge(StartupMember(startup_id=startup.id, member_id=claim.member_id, title=claim.title))
        # Anyone else waiting on this startup loses: there is now an owner.
        await session.execute(
            update(StartupClaim)
            .where(StartupClaim.startup_id == startup.id, StartupClaim.status == "pending",
                   StartupClaim.id != claim.id)
            .values(status="rejected", decided_by=admin.id, decided_at=now,
                    decision_note="another claim for this startup was approved")
        )
    claim.status = "approved" if approve else "rejected"
    claim.decided_by, claim.decided_at, claim.decision_note = admin.id, now, note
    await session.commit()
    return _claim_out(claim, startup.name)
