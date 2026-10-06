"""Product events: an append-only record of what members saw and did.

This is product data (for the dashboard, analytics and, later, learning better
matches), not debugging logs. Rows are never updated or deleted; a database
trigger enforces that.

Each event is added to the *same* database session as the action it describes,
so they commit together: there is never an "intro accepted" event for an intro
that failed to save, or the other way round.

Payloads hold ids, statuses, scores and the *names* of changed fields, never
free text such as bios or reasons.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event
from app.schemas.common import Page, Pagination
from app.schemas.events import EventOut

# Every event type the server records. Kept in one place so the dashboard and
# analysts have a single list to look at.
SERVER_EVENT_TYPES = {
    # recommendations
    "recommendation_shown",
    "matches_refreshed", "matches_recomputed",
    # intros
    "intro_requested", "intro_accepted", "intro_declined", "intro_cancelled", "intro_expired", "intro_rated",
    # profile and content
    "member_created", "member_deleted", "profile_updated", "traits_updated", "tokens_revoked",
    "verification_changed", "auth_account_linked",
    "need_created", "need_updated", "need_deleted", "offer_created", "offer_updated", "offer_deleted",
    "startup_created", "startup_updated", "startup_deleted",
    "claim_created", "claim_approved", "claim_rejected",
}

# Events the frontend may send for things only it can see (POST /events).
CLIENT_EVENT_TYPES = {"match_clicked", "match_dismissed", "profile_viewed", "startup_viewed"}


def record(
    session: AsyncSession, type: str, *, actor_id: uuid.UUID | None, target_type: str | None = None,
    target_id: uuid.UUID | str | int | None = None, payload: dict[str, Any] | None = None,
) -> None:
    """Queue one event in the caller's transaction (it is written on the caller's commit)."""
    if type not in SERVER_EVENT_TYPES | CLIENT_EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}: add it to SERVER_EVENT_TYPES")
    session.add(Event(actor_id=actor_id, type=type, target_type=target_type,
                      target_id=None if target_id is None else str(target_id), payload=payload or {}))


async def record_many(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """Insert many events in one statement (e.g. one per recommendation shown)."""
    if not rows:
        return
    for row in rows:
        if row["type"] not in SERVER_EVENT_TYPES:
            raise ValueError(f"unknown event type {row['type']!r}")
    await session.execute(insert(Event), [
        {"actor_id": r.get("actor_id"), "type": r["type"], "target_type": r.get("target_type"),
         "target_id": None if r.get("target_id") is None else str(r["target_id"]), "payload": r.get("payload", {})}
        for r in rows
    ])


async def list_events(
    session: AsyncSession, page: Pagination, *, type: str | None = None, actor_id: uuid.UUID | None = None,
    target_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
) -> Page[EventOut]:
    """Newest first, for admins and debugging."""
    stmt = select(Event)
    if type:
        stmt = stmt.where(Event.type == type)
    if actor_id:
        stmt = stmt.where(Event.actor_id == actor_id)
    if target_id:
        stmt = stmt.where(Event.target_id == target_id)
    if since:
        stmt = stmt.where(Event.created_at >= since)
    if until:
        stmt = stmt.where(Event.created_at < until)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.scalars(stmt.order_by(Event.id.desc()).limit(page.limit).offset(page.offset))
    items = [EventOut(id=e.id, actor_id=e.actor_id, type=e.type, target_type=e.target_type, target_id=e.target_id,
                      payload=e.payload, created_at=e.created_at) for e in rows]
    return Page(items=items, total=total or 0, limit=page.limit, offset=page.offset)
