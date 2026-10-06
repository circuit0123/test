"""Member profiles: read, list, update, traits, verification, token revocation."""

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.geo import from_point, to_point
from app.db.models import Capability, Member, MemberTrait, Offer, Startup, StartupMember, Trait
from app.schemas.common import Page, Pagination
from app.schemas.members import (
    MemberCreate,
    MemberMe,
    MemberProfile,
    MemberStartup,
    MemberSummary,
    MemberUpdate,
)
from app.services.errors import Conflict, Invalid, NotFound
from app.services.needs import list_needs, list_offers
from app.services.reference import trait_ids

# Columns that may not be set to null through PATCH.
_NOT_NULL = {"display_name", "open_intro_slots", "circuits_opt_in", "open_to_cross_sector"}


def _summary(m: Member) -> MemberSummary:
    return MemberSummary(id=m.id, role=m.role, display_name=m.display_name, city=m.city,
                         verification_level=m.verification_level)


async def _get(session: AsyncSession, member_id: uuid.UUID) -> Member:
    member = await session.get(Member, member_id)
    if member is None:
        raise NotFound("member not found")
    return member


async def _traits(session: AsyncSession, member_id: uuid.UUID) -> list[str]:
    stmt = select(Trait.slug).join(MemberTrait).where(MemberTrait.member_id == member_id).order_by(Trait.slug)
    return list(await session.scalars(stmt))


async def _startups(session: AsyncSession, member_id: uuid.UUID) -> list[MemberStartup]:
    stmt = (
        select(Startup.id, Startup.name, StartupMember.title).join(StartupMember)
        .where(StartupMember.member_id == member_id).order_by(Startup.name)
    )
    return [MemberStartup(startup_id=i, name=n, title=t) for i, n, t in (await session.execute(stmt)).all()]


async def get_profile(session: AsyncSession, member_id: uuid.UUID) -> MemberProfile:
    """Public view: active needs/offers only, city but never coordinates."""
    m = await _get(session, member_id)
    return MemberProfile(
        **_summary(m).model_dump(), bio=m.bio, traits=await _traits(session, m.id),
        offers=await list_offers(session, m.id, only_active=True),
        needs=await list_needs(session, "member", m.id, only_live=True),
        startups=await _startups(session, m.id),
    )


async def get_me(session: AsyncSession, member_id: uuid.UUID) -> MemberMe:
    m = await _get(session, member_id)
    lat, lng = from_point(m.location) or (None, None)
    return MemberMe(
        **_summary(m).model_dump(), bio=m.bio, traits=await _traits(session, m.id),
        offers=await list_offers(session, m.id, only_active=False),
        needs=await list_needs(session, "member", m.id, only_live=False),
        startups=await _startups(session, m.id),
        lat=lat, lng=lng, open_intro_slots=m.open_intro_slots, circuits_opt_in=m.circuits_opt_in,
        open_to_cross_sector=m.open_to_cross_sector, created_at=m.created_at,
    )


async def list_members(
    session: AsyncSession, page: Pagination, *, role: str | None = None, city: str | None = None,
    capability: str | None = None, trait: str | None = None,
) -> Page[MemberSummary]:
    stmt = select(Member)
    if role:
        stmt = stmt.where(Member.role == role)
    if city:
        stmt = stmt.where(func.lower(Member.city) == city.lower())
    if capability:
        offers_it = (
            select(Offer.member_id).join(Capability)
            .where(Capability.slug == capability, Offer.active, Offer.member_id == Member.id)
        )
        stmt = stmt.where(offers_it.exists())
    if trait:
        has_it = (
            select(MemberTrait.member_id).join(Trait)
            .where(Trait.slug == trait, MemberTrait.member_id == Member.id)
        )
        stmt = stmt.where(has_it.exists())
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.scalars(stmt.order_by(Member.display_name, Member.id).limit(page.limit).offset(page.offset))
    return Page(items=[_summary(m) for m in rows], total=total or 0, limit=page.limit, offset=page.offset)


async def update_me(session: AsyncSession, member_id: uuid.UUID, data: MemberUpdate) -> MemberMe:
    m = await _get(session, member_id)
    changes = data.model_dump(exclude_unset=True)
    for key in _NOT_NULL & changes.keys():
        if changes[key] is None:
            raise Invalid(f"{key} cannot be null")
    if "lat" in changes or "lng" in changes:
        m.location = to_point(changes.pop("lat", None), changes.pop("lng", None))
    for key, value in changes.items():
        setattr(m, key, value)
    await session.commit()
    await session.refresh(m)  # reload what the database stored (location, updated_at)
    return await get_me(session, member_id)


async def set_traits(session: AsyncSession, member_id: uuid.UUID, slugs: list[str]) -> list[str]:
    ids = await trait_ids(session, slugs)
    await session.execute(delete(MemberTrait).where(MemberTrait.member_id == member_id))
    session.add_all(MemberTrait(member_id=member_id, trait_id=t) for t in ids)
    await session.commit()
    return await _traits(session, member_id)


async def create_member(session: AsyncSession, data: MemberCreate) -> MemberMe:
    m = Member(
        role=data.role, display_name=data.display_name, bio=data.bio, city=data.city,
        location=to_point(data.lat, data.lng), verification_level=data.verification_level,
        auth_subject=data.auth_subject,
    )
    session.add(m)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("that identity-provider account is already linked to another member") from exc
    await session.refresh(m)
    return await get_me(session, m.id)


async def delete_member(session: AsyncSession, member_id: uuid.UUID) -> None:
    await session.delete(await _get(session, member_id))
    await session.commit()


async def set_verification(session: AsyncSession, member_id: uuid.UUID, level: int) -> MemberSummary:
    """Change a member's level. Their old tokens carry the old level, so revoke them."""
    m = await _get(session, member_id)
    if m.verification_level != level:
        m.verification_level = level
        m.token_version += 1
    await session.commit()
    return _summary(m)


async def set_auth_subject(session: AsyncSession, member_id: uuid.UUID, subject: str | None) -> None:
    """Link (or unlink) an identity-provider account. Old tokens are revoked either way."""
    m = await _get(session, member_id)
    if m.auth_subject != subject:
        m.auth_subject = subject
        m.token_version += 1
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("that identity-provider account is already linked to another member") from exc


async def revoke_tokens(session: AsyncSession, member_id: uuid.UUID) -> None:
    """'Log out everywhere': every token issued so far stops working."""
    await session.execute(
        update(Member).where(Member.id == member_id).values(token_version=Member.token_version + 1)
    )
    await session.commit()
