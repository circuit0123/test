import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_BROWSE, LEVEL_INTROS, CurrentMember, get_resources, require_level
from app.db.session import get_session
from app.resources import Resources
from app.schemas.common import Page, Pagination, pagination
from app.schemas.intros import IntroCreate, IntroLimits, IntroOut, IntroRating, IntroResponse, IntroStatus
from app.services import intros

router = APIRouter(prefix="/intros", tags=["intros"])
browse = require_level(LEVEL_BROWSE)


@router.post("", response_model=IntroOut, status_code=status.HTTP_201_CREATED, summary="Request an intro",
             description=(
                 "Level 3+. Ask to be introduced to a member, optionally via a mutual connection. "
                 "409 if you already asked, were already introduced, were declined recently, or their inbox is "
                 "full; 429 if you've hit your own limits (see GET /intros/limits)."
             ))
async def request_intro(body: IntroCreate, me: CurrentMember = Depends(require_level(LEVEL_INTROS)),
                        session: AsyncSession = Depends(get_session),
                        resources: Resources = Depends(get_resources)) -> IntroOut:
    return await intros.request_intro(session, me, body, resources.settings.intros)


@router.get("/limits", response_model=IntroLimits, summary="My intro limits",
            description="How many requests you have waiting and can still send, and how full your inbox is.")
async def my_limits(me: CurrentMember = Depends(browse), session: AsyncSession = Depends(get_session),
                    resources: Resources = Depends(get_resources)) -> IntroLimits:
    return await intros.limits(session, me, resources.settings.intros)


async def _list(direction: str, intro_status: str | None, me: CurrentMember, page: Pagination,
                session: AsyncSession, resources: Resources) -> Page[IntroOut]:
    return await intros.list_intros(session, me, page, resources.settings.intros,
                                    direction=direction, status=intro_status)


@router.get("/incoming", response_model=Page[IntroOut], summary="Requests to me",
            description="Intro requests other members sent you, newest first.")
async def incoming(intro_status: IntroStatus | None = Query(None, alias="status"),
                   me: CurrentMember = Depends(browse), page: Pagination = Depends(pagination),
                   session: AsyncSession = Depends(get_session),
                   resources: Resources = Depends(get_resources)) -> Page[IntroOut]:
    return await _list("incoming", intro_status, me, page, session, resources)


@router.get("/outgoing", response_model=Page[IntroOut], summary="Requests I sent",
            description="Intro requests you sent, newest first.")
async def outgoing(intro_status: IntroStatus | None = Query(None, alias="status"),
                   me: CurrentMember = Depends(browse), page: Pagination = Depends(pagination),
                   session: AsyncSession = Depends(get_session),
                   resources: Resources = Depends(get_resources)) -> Page[IntroOut]:
    return await _list("outgoing", intro_status, me, page, session, resources)


@router.get("/{intro_id}", response_model=IntroOut, summary="One intro request",
            description="Visible to the two people involved, the mutual connection, and admins.")
async def get_intro(intro_id: uuid.UUID, me: CurrentMember = Depends(browse),
                    session: AsyncSession = Depends(get_session),
                    resources: Resources = Depends(get_resources)) -> IntroOut:
    return await intros.get(session, me, intro_id, resources.settings.intros)



@router.post("/{intro_id}/accept", response_model=IntroOut, summary="Accept a request",
             description="The person asked accepts. You become connected, which strengthens future matches.")
async def accept(intro_id: uuid.UUID, body: IntroResponse | None = None, me: CurrentMember = Depends(browse),
                 session: AsyncSession = Depends(get_session),
                 resources: Resources = Depends(get_resources)) -> IntroOut:
    return await intros.respond(session, me, intro_id, accept=True, note=body.note if body else None,
                                settings=resources.settings.intros)


@router.post("/{intro_id}/decline", response_model=IntroOut, summary="Decline a request",
             description="The person asked declines, with an optional note. They won't be suggested to the "
                         "requester for a while.")
async def decline(intro_id: uuid.UUID, body: IntroResponse | None = None, me: CurrentMember = Depends(browse),
                  session: AsyncSession = Depends(get_session),
                  resources: Resources = Depends(get_resources)) -> IntroOut:
    return await intros.respond(session, me, intro_id, accept=False, note=body.note if body else None,
                                settings=resources.settings.intros)


@router.post("/{intro_id}/cancel", response_model=IntroOut, summary="Cancel my request",
             description="The requester withdraws a pending request, freeing a slot for both of you.")
async def cancel(intro_id: uuid.UUID, me: CurrentMember = Depends(browse),
                 session: AsyncSession = Depends(get_session),
                 resources: Resources = Depends(get_resources)) -> IntroOut:
    return await intros.cancel(session, me, intro_id, resources.settings.intros)


@router.post("/{intro_id}/rating", response_model=IntroOut, summary="Rate an intro",
             description="After an accepted intro, either person rates how useful it was (1-5). Can be changed.")
async def rate(intro_id: uuid.UUID, body: IntroRating, me: CurrentMember = Depends(browse),
               session: AsyncSession = Depends(get_session),
               resources: Resources = Depends(get_resources)) -> IntroOut:
    return await intros.rate(session, me, intro_id, body.rating, resources.settings.intros)
