import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_BROWSE, CurrentMember, get_resources, require_level
from app.db.session import get_session
from app.resources import Resources
from app.schemas.needs import NeedOut, NeedUpdate, OfferOut, OfferUpdate
from app.services import needs

router = APIRouter(tags=["needs & offers"])
browse = require_level(LEVEL_BROWSE)


@router.patch("/needs/{need_id}", response_model=NeedOut, summary="Update a need",
              description="Change a need's text, capability, active flag or expiry. Owner, startup team or admin.")
async def update_need(need_id: uuid.UUID, body: NeedUpdate, me: CurrentMember = Depends(browse),
                      session: AsyncSession = Depends(get_session),
                      resources: Resources = Depends(get_resources)) -> NeedOut:
    return await needs.update_need(session, resources.embedder, me, need_id, body)


@router.delete("/needs/{need_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a need",
               description="Permanently deletes a need. To pause it instead, PATCH active=false.")
async def delete_need(need_id: uuid.UUID, me: CurrentMember = Depends(browse),
                      session: AsyncSession = Depends(get_session)) -> Response:
    await needs.delete_need(session, me, need_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/offers/{offer_id}", response_model=OfferOut, summary="Update an offer",
              description="Change an offer's text, capability or active flag. Owner or admin.")
async def update_offer(offer_id: uuid.UUID, body: OfferUpdate, me: CurrentMember = Depends(browse),
                       session: AsyncSession = Depends(get_session),
                       resources: Resources = Depends(get_resources)) -> OfferOut:
    return await needs.update_offer(session, resources.embedder, me, offer_id, body)


@router.delete("/offers/{offer_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an offer",
               description="Permanently deletes an offer. To pause it instead, PATCH active=false.")
async def delete_offer(offer_id: uuid.UUID, me: CurrentMember = Depends(browse),
                       session: AsyncSession = Depends(get_session)) -> Response:
    await needs.delete_offer(session, me, offer_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
