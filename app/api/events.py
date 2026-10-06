import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_BROWSE, CurrentMember, require_admin, require_level
from app.db.session import get_session
from app.schemas.common import Page, Pagination, pagination
from app.schemas.events import ClientEvent, EventOut
from app.services import events

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT, summary="Record a frontend event",
             description=(
                 "For things only the app sees: a match card clicked (`match_clicked`) or dismissed "
                 "(`match_dismissed`, which hides that person from your matches for a while), a profile or "
                 "startup viewed. The actor is always you."
             ))
async def record(body: ClientEvent, me: CurrentMember = Depends(require_level(LEVEL_BROWSE)),
                 session: AsyncSession = Depends(get_session)) -> Response:
    events.record(session, body.type, actor_id=me.id, target_type=body.target_type, target_id=body.target_id,
                  payload=body.payload)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=Page[EventOut], summary="List events (admin)",
            description="partner_admin only. The append-only product event log, newest first, with filters.")
async def list_events(
    type: str | None = None,
    actor_id: uuid.UUID | None = None,
    target_id: str | None = Query(None, max_length=64),
    since: datetime | None = Query(None, description="Only events at or after this time (ISO 8601)."),
    until: datetime | None = Query(None, description="Only events before this time (ISO 8601)."),
    page: Pagination = Depends(pagination),
    _: CurrentMember = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Page[EventOut]:
    return await events.list_events(session, page, type=type, actor_id=actor_id, target_id=target_id,
                                    since=since, until=until)
