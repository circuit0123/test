"""Candidate generation: who is worth scoring for a member?

Two sources, merged:
a) Local funnel: same city, then narrowed to people who share a trait or are
   within 2 hops in the connection graph (a recursive SQL query walks the graph).
b) Bridge search: the whole pool, ignoring sector and place. People whose offers
   meet my needs (or whose needs my offers meet), found by exact capability match
   plus nearest neighbours by embedding (pgvector HNSW index). Only between
   members who are both open_to_cross_sector.

A candidate found by both is "local"; found only by bridge search, "bridge".
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Capability, Member, MemberTrait, Need, Offer, Startup, StartupMember, Trait
from app.matching.scoring import MemberProfile, NeedItem, OfferItem

# Members in these roles are never suggested as matches.
EXCLUDED_ROLES = ("partner_admin",)

# Walk the undirected connection graph out to 2 hops, keeping the fewest hops and
# the strongest path (product of strengths) to each member reached.
_PATHS_SQL = text("""
WITH RECURSIVE edges AS (
    SELECT member_a AS src, member_b AS dst, strength FROM connections
    UNION ALL
    SELECT member_b, member_a, strength FROM connections
),
walk(member_id, depth, strength) AS (
    SELECT dst, 1, strength FROM edges WHERE src = :me
    UNION ALL
    SELECT e.dst, w.depth + 1, w.strength * e.strength
    FROM walk w JOIN edges e ON e.src = w.member_id
    WHERE w.depth < 2 AND e.dst <> :me
)
SELECT member_id, min(depth) AS hops, max(strength) AS strength
FROM walk GROUP BY member_id
""")


@dataclass(frozen=True)
class Path:
    hops: int
    strength: float


@dataclass
class Candidates:
    local: set[uuid.UUID]
    bridge: set[uuid.UUID]  # excludes anyone already in `local`
    paths: dict[uuid.UUID, Path]


async def connection_paths(session: AsyncSession, member_id: uuid.UUID) -> dict[uuid.UUID, Path]:
    rows = await session.execute(_PATHS_SQL, {"me": member_id})
    return {mid: Path(hops=h, strength=s) for mid, h, s in rows}


async def local_candidates(
    session: AsyncSession, member_id: uuid.UUID, city: str | None, paths: dict[uuid.UUID, Path]
) -> set[uuid.UUID]:
    shares_trait = (
        select(MemberTrait.member_id).where(
            MemberTrait.member_id == Member.id,
            MemberTrait.trait_id.in_(select(MemberTrait.trait_id).where(MemberTrait.member_id == member_id)),
        ).exists()
    )
    stmt = select(Member.id).where(Member.id != member_id, Member.role.notin_(EXCLUDED_ROLES))
    if city:
        stmt = stmt.where(func.lower(Member.city) == city.lower())
    connected = list(paths)
    stmt = stmt.where(or_(shares_trait, Member.id.in_(connected)) if connected else shares_trait)
    return set(await session.scalars(stmt))


async def bridge_candidates(
    session: AsyncSession, me: MemberProfile, neighbours_per_need: int
) -> set[uuid.UUID]:
    if not me.open_to_cross_sector:
        return set()
    my_id = uuid.UUID(me.id)
    found: set[uuid.UUID] = set()

    # 1) My needs -> other members' offers: same capability...
    my_need_caps = {n.capability for n in me.needs}
    if my_need_caps:
        stmt = (select(Offer.member_id).join(Capability)
                .where(Capability.slug.in_(my_need_caps), Offer.active, Offer.member_id != my_id))
        found |= set(await session.scalars(stmt))
    # ...and nearest offers by meaning, via the HNSW index (catches different wording/capability).
    for need in me.needs:
        if need.vector is None:
            continue
        stmt = (select(Offer.member_id).where(Offer.active, Offer.member_id != my_id, Offer.embedding.isnot(None))
                .order_by(Offer.embedding.cosine_distance(list(need.vector))).limit(neighbours_per_need))
        found |= set(await session.scalars(stmt))

    # 2) My offers -> other people's needs (member needs, or needs of a startup they work at).
    my_offer_caps = {o.capability for o in me.offers}
    if my_offer_caps:
        owners = (select(Need.owner_type, Need.owner_id).join(Capability)
                  .where(Capability.slug.in_(my_offer_caps), _live_need()))
        found |= await _need_owners_to_members(session, (await session.execute(owners)).all())
    for offer in me.offers:
        if offer.vector is None:
            continue
        stmt = (select(Need.owner_type, Need.owner_id).where(_live_need(), Need.embedding.isnot(None))
                .order_by(Need.embedding.cosine_distance(list(offer.vector))).limit(neighbours_per_need))
        found |= await _need_owners_to_members(session, (await session.execute(stmt)).all())

    found.discard(my_id)
    if not found:
        return set()
    # Both sides must be open to cross-sector matches, and never suggest excluded roles.
    stmt = select(Member.id).where(Member.id.in_(found), Member.open_to_cross_sector,
                                   Member.role.notin_(EXCLUDED_ROLES))
    return set(await session.scalars(stmt))


async def find_candidates(session: AsyncSession, me: MemberProfile, neighbours_per_need: int) -> Candidates:
    my_id = uuid.UUID(me.id)
    paths = await connection_paths(session, my_id)
    local = await local_candidates(session, my_id, me.city, paths)
    bridge = await bridge_candidates(session, me, neighbours_per_need) - local
    return Candidates(local=local, bridge=bridge, paths=paths)


# ---------------------------------------------------------------- profiles

def _live_need():
    return Need.active & or_(Need.expires_at.is_(None), Need.expires_at > func.now())


async def _need_owners_to_members(session: AsyncSession, owners) -> set[uuid.UUID]:
    member_ids = {oid for otype, oid in owners if otype == "member"}
    startup_ids = {oid for otype, oid in owners if otype == "startup"}
    if startup_ids:
        team = select(StartupMember.member_id).where(StartupMember.startup_id.in_(startup_ids))
        member_ids |= set(await session.scalars(team))
    return member_ids


def _vec(value) -> np.ndarray | None:
    return None if value is None else np.asarray(value, dtype=float)


async def load_profiles(session: AsyncSession, member_ids: set[uuid.UUID]) -> dict[uuid.UUID, MemberProfile]:
    """Build scoring profiles in a handful of batched queries.

    A member's needs include their startup's needs: a founder is matched on what
    the company is looking for. Founders of hiring startups are also treated as
    offering internships, which is what most students need.
    """
    if not member_ids:
        return {}
    ids = list(member_ids)
    members = (await session.execute(
        select(Member.id, Member.role, Member.city, Member.open_intro_slots, Member.open_to_cross_sector)
        .where(Member.id.in_(ids))
    )).all()

    traits: dict[uuid.UUID, set[str]] = defaultdict(set)
    for mid, slug in await session.execute(
        select(MemberTrait.member_id, Trait.slug).join(Trait).where(MemberTrait.member_id.in_(ids))
    ):
        traits[mid].add(slug)

    team: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)  # startup -> members
    hiring: dict[uuid.UUID, list[str]] = defaultdict(list)  # member -> hiring startup ids
    for sid, mid, is_hiring in await session.execute(
        select(StartupMember.startup_id, StartupMember.member_id, Startup.hiring)
        .join(Startup, Startup.id == StartupMember.startup_id).where(StartupMember.member_id.in_(ids))
    ):
        team[sid].append(mid)
        if is_hiring:
            hiring[mid].append(str(sid))

    needs: dict[uuid.UUID, list[NeedItem]] = defaultdict(list)
    need_rows = await session.execute(
        select(Need.id, Need.owner_type, Need.owner_id, Capability.slug, Need.embedding, Need.expires_at)
        .join(Capability).where(_live_need(), or_(
            (Need.owner_type == "member") & Need.owner_id.in_(ids),
            (Need.owner_type == "startup") & Need.owner_id.in_(list(team)),
        ))
    )
    for nid, otype, oid, slug, emb, expires in need_rows:
        item = NeedItem(id=str(nid), capability=slug, vector=_vec(emb), expires_at=expires)
        for mid in ([oid] if otype == "member" else team[oid]):
            needs[mid].append(item)

    offers: dict[uuid.UUID, list[OfferItem]] = defaultdict(list)
    for oid, mid, slug, emb in await session.execute(
        select(Offer.id, Offer.member_id, Capability.slug, Offer.embedding).join(Capability)
        .where(Offer.active, Offer.member_id.in_(ids))
    ):
        offers[mid].append(OfferItem(id=str(oid), capability=slug, vector=_vec(emb)))
    for mid, startup_ids in hiring.items():
        offers[mid].append(OfferItem(id=f"startup:{startup_ids[0]}:hiring", capability="internships", vector=None))

    pending = dict((await session.execute(
        text("SELECT to_member, count(*) FROM intro_requests WHERE status = 'pending' "
             "AND to_member = ANY(:ids) GROUP BY to_member"), {"ids": ids}
    )).all())

    return {
        mid: MemberProfile(
            id=str(mid), role=role, city=city, traits=frozenset(traits[mid]), needs=tuple(needs[mid]),
            offers=tuple(offers[mid]), open_intro_slots=slots, pending_incoming=pending.get(mid, 0),
            open_to_cross_sector=cross,
        )
        for mid, role, city, slots, cross in members
    }
