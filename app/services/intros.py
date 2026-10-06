"""Intro requests: ask to be introduced, accept/decline, cancel, rate the outcome.

Capacity rules (all configurable under INTROS__*):
- the recipient's `open_intro_slots` caps how many requests can wait for them
  at once ("inbox full"), so nobody is flooded;
- a requester may have at most `max_pending_outgoing` requests waiting, and send
  at most `max_requests_per_week`;
- one pending request per pair; no repeat after an accepted intro; after a
  decline, wait `decline_cooldown_days` before asking the same person again;
- unanswered requests expire after `expire_after_days` (an hourly job).

Race safety: two requests arriving at the same moment could both see "1 slot
left". We lock both members' rows (SELECT ... FOR UPDATE) while checking and
inserting, so such requests run one after the other. Locks are always taken in
id order, so two transactions can never wait on each other forever (deadlock).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import IntroSettings
from app.db.models import Connection, IntroRequest, MatchResult, Member
from app.schemas.common import Page, Pagination
from app.schemas.intros import IntroCreate, IntroLimits, IntroOut
from app.schemas.members import MemberSummary
from app.services import events
from app.services.auth import CurrentMember
from app.services.errors import Conflict, Forbidden, Invalid, NotFound, TooMany


def _now() -> datetime:
    return datetime.now(UTC)


def _summary(m: Member) -> MemberSummary:
    return MemberSummary(id=m.id, role=m.role, display_name=m.display_name, city=m.city,
                         verification_level=m.verification_level)


async def _out(session: AsyncSession, intro: IntroRequest, viewer: uuid.UUID, settings: IntroSettings) -> IntroOut:
    ids = {intro.from_member, intro.to_member} | ({intro.via_member} if intro.via_member else set())
    people = {m.id: m for m in await session.scalars(select(Member).where(Member.id.in_(ids)))}
    via = people.get(intro.via_member) if intro.via_member else None
    return IntroOut(
        id=intro.id, from_member=_summary(people[intro.from_member]), to_member=_summary(people[intro.to_member]),
        via_member=_summary(via) if via else None, status=intro.status, reason=intro.reason,
        response_note=intro.response_note, match_source=intro.match_source, created_at=intro.created_at,
        responded_at=intro.responded_at,
        expires_at=intro.created_at + timedelta(days=settings.expire_after_days) if intro.status == "pending" else None,
        my_rating=intro.from_rating if viewer == intro.from_member else
        intro.to_rating if viewer == intro.to_member else None,
    )


def _pending_cutoff(settings: IntroSettings) -> datetime:
    # Requests older than this count as expired even before the hourly job marks them.
    return _now() - timedelta(days=settings.expire_after_days)


async def _counts(session: AsyncSession, member_id: uuid.UUID, settings: IntroSettings) -> tuple[int, int, int]:
    """(pending outgoing, sent in the last 7 days, pending incoming) for a member."""
    live = and_(IntroRequest.status == "pending", IntroRequest.created_at > _pending_cutoff(settings))
    out_pending = await session.scalar(
        select(func.count()).where(IntroRequest.from_member == member_id, live))
    week = await session.scalar(select(func.count()).where(
        IntroRequest.from_member == member_id, IntroRequest.created_at > _now() - timedelta(days=7),
        IntroRequest.status != "cancelled"))
    in_pending = await session.scalar(
        select(func.count()).where(IntroRequest.to_member == member_id, live))
    return out_pending or 0, week or 0, in_pending or 0


async def limits(session: AsyncSession, me: CurrentMember, settings: IntroSettings) -> IntroLimits:
    out_pending, week, in_pending = await _counts(session, me.id, settings)
    slots = await session.scalar(select(Member.open_intro_slots).where(Member.id == me.id))
    return IntroLimits(pending_outgoing=out_pending, max_pending_outgoing=settings.max_pending_outgoing,
                       sent_last_7_days=week, max_per_week=settings.max_requests_per_week,
                       pending_incoming=in_pending, open_intro_slots=slots or 0)


async def _connected(session: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    lo, hi = sorted([a, b])
    return await session.scalar(
        select(func.count()).where(Connection.member_a == lo, Connection.member_b == hi)) > 0


async def request_intro(
    session: AsyncSession, me: CurrentMember, data: IntroCreate, settings: IntroSettings
) -> IntroOut:
    if data.to_member == me.id:
        raise Invalid("you can't request an intro to yourself")
    # Lock both members (in id order) for the duration of the checks and insert.
    locked = list(await session.scalars(
        select(Member).where(Member.id.in_([me.id, data.to_member])).order_by(Member.id).with_for_update()))
    target = next((m for m in locked if m.id == data.to_member), None)
    if target is None:
        raise NotFound("member not found")

    if data.via_member is not None:
        if data.via_member in (me.id, data.to_member):
            raise Invalid("via_member must be a third person")
        if not (await _connected(session, me.id, data.via_member)
                and await _connected(session, data.via_member, data.to_member)):
            raise Invalid("via_member must be connected to both of you")

    between = or_(and_(IntroRequest.from_member == me.id, IntroRequest.to_member == data.to_member),
                  and_(IntroRequest.from_member == data.to_member, IntroRequest.to_member == me.id))
    history = list(await session.scalars(select(IntroRequest).where(between)))
    cutoff = _pending_cutoff(settings)
    for past in history:
        if past.status == "accepted":
            raise Conflict("you have already been introduced")
        if past.status == "pending" and past.created_at > cutoff:
            if past.from_member == me.id:
                raise Conflict("you already have a pending request to this member")
            raise Conflict("they have already asked to meet you: accept their request instead")
        if past.status == "pending":  # past its deadline but not yet marked by the job
            past.status = "expired"
            events.record(session, "intro_expired", actor_id=None, target_type="intro", target_id=past.id)
        if (past.status == "declined" and past.from_member == me.id and past.responded_at
                and past.responded_at > _now() - timedelta(days=settings.decline_cooldown_days)):
            raise Conflict(f"they declined recently; you can ask again after {settings.decline_cooldown_days} days")

    out_pending, week, _ = await _counts(session, me.id, settings)
    if out_pending >= settings.max_pending_outgoing:
        raise TooMany(f"you have {out_pending} requests waiting (max {settings.max_pending_outgoing}); "
                      "wait for answers or cancel one")
    if week >= settings.max_requests_per_week:
        raise TooMany(f"you can send at most {settings.max_requests_per_week} requests per 7 days")
    _, _, their_pending = await _counts(session, target.id, settings)
    if target.open_intro_slots <= 0:
        raise Conflict("this member isn't accepting intro requests right now")
    if their_pending >= target.open_intro_slots:
        raise Conflict("this member's intro inbox is full; try again later")

    match = await session.scalar(select(MatchResult).where(
        MatchResult.member_id == me.id, MatchResult.candidate_member_id == target.id))
    intro = IntroRequest(
        id=uuid.uuid4(), from_member=me.id, to_member=target.id, via_member=data.via_member, reason=data.reason,
        match_source=match.source if match else None, match_score=match.score if match else None,
    )
    session.add(intro)
    events.record(session, "intro_requested", actor_id=me.id, target_type="member", target_id=target.id,
                  payload={"intro_id": str(intro.id), "match_source": intro.match_source,
                           "match_score": intro.match_score, "warm": data.via_member is not None})
    await session.commit()
    await session.refresh(intro)
    return await _out(session, intro, me.id, settings)


async def _locked_intro(session: AsyncSession, intro_id: uuid.UUID) -> IntroRequest:
    intro = await session.scalar(select(IntroRequest).where(IntroRequest.id == intro_id).with_for_update())
    if intro is None:
        raise NotFound("intro request not found")
    return intro


async def _require_pending(session: AsyncSession, intro: IntroRequest, settings: IntroSettings) -> None:
    if intro.status == "pending" and intro.created_at <= _pending_cutoff(settings):
        # Expired in time but the job hasn't marked it yet: do it now.
        intro.status = "expired"
        events.record(session, "intro_expired", actor_id=None, target_type="intro", target_id=intro.id)
        await session.commit()
    if intro.status != "pending":
        raise Conflict(f"this request is {intro.status}")


async def respond(
    session: AsyncSession, me: CurrentMember, intro_id: uuid.UUID, *, accept: bool, note: str | None,
    settings: IntroSettings,
) -> IntroOut:
    intro = await _locked_intro(session, intro_id)
    if intro.to_member != me.id:
        raise Forbidden("only the person asked can accept or decline")
    await _require_pending(session, intro, settings)
    intro.status = "accepted" if accept else "declined"
    intro.responded_at = _now()
    intro.response_note = note
    if accept:
        # An accepted intro becomes a connection (or strengthens an existing one),
        # which feeds back into trust paths for future matches.
        lo, hi = sorted([intro.from_member, intro.to_member])
        stmt = insert(Connection).values(member_a=lo, member_b=hi, strength=settings.connection_strength,
                                         source="intro")
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["member_a", "member_b"],
            set_={"strength": func.greatest(Connection.strength, stmt.excluded.strength)}))
    events.record(session, f"intro_{intro.status}", actor_id=me.id, target_type="intro", target_id=intro.id,
                  payload={"from_member": str(intro.from_member), "match_source": intro.match_source,
                           "hours_to_respond": round((intro.responded_at - intro.created_at).total_seconds() / 3600, 1)})
    await session.commit()
    return await _out(session, intro, me.id, settings)


async def cancel(session: AsyncSession, me: CurrentMember, intro_id: uuid.UUID, settings: IntroSettings) -> IntroOut:
    intro = await _locked_intro(session, intro_id)
    if intro.from_member != me.id:
        raise Forbidden("only the requester can cancel")
    await _require_pending(session, intro, settings)
    intro.status = "cancelled"
    events.record(session, "intro_cancelled", actor_id=me.id, target_type="intro", target_id=intro.id)
    await session.commit()
    return await _out(session, intro, me.id, settings)


async def rate(
    session: AsyncSession, me: CurrentMember, intro_id: uuid.UUID, rating: int, settings: IntroSettings
) -> IntroOut:
    intro = await _locked_intro(session, intro_id)
    if me.id not in (intro.from_member, intro.to_member):
        raise Forbidden("only the two people introduced can rate it")
    if intro.status != "accepted":
        raise Conflict("you can only rate an accepted intro")
    side = "from" if me.id == intro.from_member else "to"
    setattr(intro, f"{side}_rating", rating)
    events.record(session, "intro_rated", actor_id=me.id, target_type="intro", target_id=intro.id,
                  payload={"rating": rating, "side": side, "match_source": intro.match_source})
    await session.commit()
    return await _out(session, intro, me.id, settings)


async def get(session: AsyncSession, me: CurrentMember, intro_id: uuid.UUID, settings: IntroSettings) -> IntroOut:
    intro = await session.get(IntroRequest, intro_id)
    if intro is None:
        raise NotFound("intro request not found")
    if not (me.is_admin or me.id in (intro.from_member, intro.to_member, intro.via_member)):
        raise NotFound("intro request not found")  # don't reveal that it exists
    return await _out(session, intro, me.id, settings)


async def list_intros(
    session: AsyncSession, me: CurrentMember, page: Pagination, settings: IntroSettings, *,
    direction: str, status: str | None,
) -> Page[IntroOut]:
    column = IntroRequest.to_member if direction == "incoming" else IntroRequest.from_member
    stmt = select(IntroRequest).where(column == me.id)
    if status:
        stmt = stmt.where(IntroRequest.status == status)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.scalars(stmt.order_by(IntroRequest.created_at.desc(), IntroRequest.id)
                                 .limit(page.limit).offset(page.offset))
    items = [await _out(session, r, me.id, settings) for r in rows]
    return Page(items=items, total=total or 0, limit=page.limit, offset=page.offset)


async def expire_stale(session: AsyncSession, settings: IntroSettings) -> int:
    """Mark unanswered requests past their deadline as expired (run by the scheduler)."""
    expired = (await session.execute(
        update(IntroRequest)
        .where(IntroRequest.status == "pending", IntroRequest.created_at <= _pending_cutoff(settings))
        .values(status="expired").returning(IntroRequest.id)
    )).scalars().all()
    for intro_id in expired:
        events.record(session, "intro_expired", actor_id=None, target_type="intro", target_id=intro_id)
    await session.commit()
    return len(expired)
