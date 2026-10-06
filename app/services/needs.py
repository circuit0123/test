"""Needs (what a member or startup is looking for) and offers (what a member gives).

Every create or text change recomputes the embedding, so matching (Phase 4)
always compares up-to-date meaning.
"""

import asyncio
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth import CurrentMember
from app.db.models import Capability, Need, Offer
from app.embeddings.base import EmbeddingProvider
from app.schemas.needs import NeedCreate, NeedOut, NeedUpdate, OfferCreate, OfferOut, OfferUpdate
from app.services.errors import Forbidden, Invalid, NotFound
from app.services.permissions import can_edit_startup
from app.services.reference import capability_id


async def embed_text(embedder: EmbeddingProvider, text: str) -> list[float]:
    # Embedding is CPU-heavy; a worker thread keeps the server responsive meanwhile.
    (vector,) = await asyncio.to_thread(embedder.embed, [text])
    return vector


def _need_out(need: Need, slug: str) -> NeedOut:
    return NeedOut(id=need.id, owner_type=need.owner_type, owner_id=need.owner_id, capability=slug,
                   text=need.text, active=need.active, expires_at=need.expires_at)


def _offer_out(offer: Offer, slug: str) -> OfferOut:
    return OfferOut(id=offer.id, member_id=offer.member_id, capability=slug, text=offer.text, active=offer.active)


def _need_is_live():
    return Need.active & or_(Need.expires_at.is_(None), Need.expires_at > func.now())


async def list_needs(
    session: AsyncSession, owner_type: str, owner_id: uuid.UUID, *, only_live: bool
) -> list[NeedOut]:
    stmt = (
        select(Need, Capability.slug).join(Capability)
        .where(Need.owner_type == owner_type, Need.owner_id == owner_id)
        .order_by(Capability.slug, Need.text)
    )
    if only_live:
        stmt = stmt.where(_need_is_live())
    return [_need_out(n, slug) for n, slug in (await session.execute(stmt)).all()]


async def list_offers(session: AsyncSession, member_id: uuid.UUID, *, only_active: bool) -> list[OfferOut]:
    stmt = (
        select(Offer, Capability.slug).join(Capability)
        .where(Offer.member_id == member_id)
        .order_by(Capability.slug, Offer.text)
    )
    if only_active:
        stmt = stmt.where(Offer.active)
    return [_offer_out(o, slug) for o, slug in (await session.execute(stmt)).all()]


async def create_need(
    session: AsyncSession, embedder: EmbeddingProvider, owner_type: str, owner_id: uuid.UUID, data: NeedCreate
) -> NeedOut:
    need = Need(
        owner_type=owner_type, owner_id=owner_id, capability_id=await capability_id(session, data.capability),
        text=data.text, expires_at=data.expires_at, embedding=await embed_text(embedder, data.text),
        embedding_model=embedder.model_id,
    )
    session.add(need)
    await session.commit()
    return _need_out(need, data.capability)


async def create_offer(
    session: AsyncSession, embedder: EmbeddingProvider, member_id: uuid.UUID, data: OfferCreate
) -> OfferOut:
    offer = Offer(
        member_id=member_id, capability_id=await capability_id(session, data.capability),
        text=data.text, embedding=await embed_text(embedder, data.text), embedding_model=embedder.model_id,
    )
    session.add(offer)
    await session.commit()
    return _offer_out(offer, data.capability)


async def _editable_need(session: AsyncSession, me: CurrentMember, need_id: uuid.UUID) -> Need:
    need = await session.get(Need, need_id)
    if need is None:
        raise NotFound("need not found")
    if need.owner_type == "member":
        allowed = need.owner_id == me.id or me.is_admin
    else:
        allowed = await can_edit_startup(session, me, need.owner_id)
    if not allowed:
        raise Forbidden("you can only change your own needs (or your startup's)")
    return need


async def _editable_offer(session: AsyncSession, me: CurrentMember, offer_id: uuid.UUID) -> Offer:
    offer = await session.get(Offer, offer_id)
    if offer is None:
        raise NotFound("offer not found")
    if offer.member_id != me.id and not me.is_admin:
        raise Forbidden("you can only change your own offers")
    return offer


async def _apply_common(session, embedder, row: Need | Offer, changes: dict) -> str:
    """Apply capability/text/active changes shared by needs and offers; return the slug."""
    for key in ("text", "active", "capability"):
        if key in changes and changes[key] is None:
            raise Invalid(f"{key} cannot be null")
    if "capability" in changes:
        row.capability_id = await capability_id(session, changes["capability"])
    if "text" in changes and changes["text"] != row.text:
        row.text = changes["text"]
        row.embedding = await embed_text(embedder, row.text)
        row.embedding_model = embedder.model_id
    if "active" in changes:
        row.active = changes["active"]
    return await session.scalar(select(Capability.slug).where(Capability.id == row.capability_id))


async def update_need(
    session: AsyncSession, embedder: EmbeddingProvider, me: CurrentMember, need_id: uuid.UUID, data: NeedUpdate
) -> NeedOut:
    need = await _editable_need(session, me, need_id)
    changes = data.model_dump(exclude_unset=True)
    slug = await _apply_common(session, embedder, need, changes)
    if "expires_at" in changes:
        need.expires_at = changes["expires_at"]
    await session.commit()
    return _need_out(need, slug)


async def update_offer(
    session: AsyncSession, embedder: EmbeddingProvider, me: CurrentMember, offer_id: uuid.UUID, data: OfferUpdate
) -> OfferOut:
    offer = await _editable_offer(session, me, offer_id)
    slug = await _apply_common(session, embedder, offer, data.model_dump(exclude_unset=True))
    await session.commit()
    return _offer_out(offer, slug)


async def delete_need(session: AsyncSession, me: CurrentMember, need_id: uuid.UUID) -> None:
    await session.delete(await _editable_need(session, me, need_id))
    await session.commit()


async def delete_offer(session: AsyncSession, me: CurrentMember, offer_id: uuid.UUID) -> None:
    await session.delete(await _editable_offer(session, me, offer_id))
    await session.commit()
