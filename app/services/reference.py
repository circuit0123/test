"""The fixed capability and trait vocabularies."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Capability, Trait
from app.schemas.reference import CapabilityOut, TraitOut
from app.services.errors import Invalid


async def list_capabilities(session: AsyncSession) -> list[CapabilityOut]:
    rows = await session.scalars(select(Capability).order_by(Capability.name))
    return [CapabilityOut(slug=c.slug, name=c.name, description=c.description) for c in rows]


async def list_traits(session: AsyncSession) -> list[TraitOut]:
    rows = await session.scalars(select(Trait).order_by(Trait.name))
    return [TraitOut(slug=t.slug, name=t.name) for t in rows]


async def capability_id(session: AsyncSession, slug: str) -> int:
    cap_id = await session.scalar(select(Capability.id).where(Capability.slug == slug))
    if cap_id is None:
        raise Invalid(f"unknown capability '{slug}' (see GET /capabilities)")
    return cap_id


async def trait_ids(session: AsyncSession, slugs: list[str]) -> list[int]:
    wanted = set(slugs)
    rows = (await session.execute(select(Trait.slug, Trait.id).where(Trait.slug.in_(wanted)))).all()
    found = {slug: tid for slug, tid in rows}
    unknown = sorted(wanted - found.keys())
    if unknown:
        raise Invalid(f"unknown traits: {', '.join(unknown)} (see GET /traits)")
    return list(found.values())
