import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_BROWSE, LEVEL_MATCHES, CurrentMember, get_resources, require_admin, require_level
from app.db.session import get_session
from app.resources import Resources
from app.schemas.common import Page, Pagination, pagination
from app.schemas.needs import NeedCreate, NeedOut
from app.schemas.startups import (
    ClaimCreate,
    ClaimDecision,
    ClaimOut,
    StartupCreate,
    StartupDetail,
    StartupSummary,
    StartupUpdate,
)
from app.services import startups

router = APIRouter(prefix="/startups", tags=["startups"])
browse = require_level(LEVEL_BROWSE)
# Creating or claiming a startup needs a verified member (level 2).
verified = require_level(LEVEL_MATCHES)


@router.get("", response_model=Page[StartupSummary], summary="List startups",
            description="Browse startups, filtered by sector, hiring, claimed status or a name search.")
async def list_startups(
    sector: str | None = None,
    hiring: bool | None = None,
    claimed: bool | None = None,
    q: str | None = Query(None, max_length=100, description="Case-insensitive name search."),
    page: Pagination = Depends(pagination),
    _: CurrentMember = Depends(browse),
    session: AsyncSession = Depends(get_session),
) -> Page[StartupSummary]:
    return await startups.list_startups(session, page, sector=sector, hiring=hiring, claimed=claimed, q=q)


@router.post("", response_model=StartupDetail, status_code=status.HTTP_201_CREATED, summary="Add a startup",
             description="Level 2+. Adds a startup that isn't listed yet; you become its owner. "
                         "409 if the domain already exists (claim it instead).")
async def create_startup(body: StartupCreate, me: CurrentMember = Depends(verified),
                         session: AsyncSession = Depends(get_session)) -> StartupDetail:
    return await startups.create_startup(session, me, body)


# --- claim review (declared before /{startup_id} so "claims" is not parsed as an id) ---

@router.get("/claims", response_model=Page[ClaimOut], summary="List claims (admin)",
            description="partner_admin only. Claims to review, oldest first.")
async def list_claims(
    claim_status: Literal["pending", "approved", "rejected"] | None = Query("pending", alias="status"),
    page: Pagination = Depends(pagination),
    _: CurrentMember = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Page[ClaimOut]:
    return await startups.list_claims(session, page, status=claim_status)


@router.post("/claims/{claim_id}/approve", response_model=ClaimOut, summary="Approve a claim (admin)",
             description="partner_admin only. Makes the claimant the owner and a team member; "
                         "other pending claims for the startup are rejected.")
async def approve_claim(claim_id: uuid.UUID, body: ClaimDecision | None = None,
                        admin: CurrentMember = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)) -> ClaimOut:
    return await startups.decide_claim(session, admin, claim_id, approve=True, note=body.note if body else None)


@router.post("/claims/{claim_id}/reject", response_model=ClaimOut, summary="Reject a claim (admin)",
             description="partner_admin only. Rejects a pending claim, with an optional note to the claimant.")
async def reject_claim(claim_id: uuid.UUID, body: ClaimDecision | None = None,
                       admin: CurrentMember = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)) -> ClaimOut:
    return await startups.decide_claim(session, admin, claim_id, approve=False, note=body.note if body else None)


@router.get("/{startup_id}", response_model=StartupDetail, summary="Startup profile",
            description="Full startup profile with team, traits and active needs.")
async def get_startup(startup_id: uuid.UUID, _: CurrentMember = Depends(browse),
                      session: AsyncSession = Depends(get_session)) -> StartupDetail:
    return await startups.get_detail(session, startup_id)


@router.patch("/{startup_id}", response_model=StartupDetail, summary="Update a startup",
              description="Team members, the claimer or a partner_admin. The domain cannot be changed.")
async def update_startup(startup_id: uuid.UUID, body: StartupUpdate, me: CurrentMember = Depends(browse),
                         session: AsyncSession = Depends(get_session)) -> StartupDetail:
    return await startups.update_startup(session, me, startup_id, body)


@router.delete("/{startup_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a startup",
               description="The claimer or a partner_admin.")
async def delete_startup(startup_id: uuid.UUID, me: CurrentMember = Depends(browse),
                         session: AsyncSession = Depends(get_session)) -> Response:
    await startups.delete_startup(session, me, startup_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{startup_id}/needs", response_model=NeedOut, status_code=status.HTTP_201_CREATED,
             summary="Add a startup need", description="Team members only. Adds something the startup is looking for.")
async def add_startup_need(startup_id: uuid.UUID, body: NeedCreate, me: CurrentMember = Depends(browse),
                           session: AsyncSession = Depends(get_session),
                           resources: Resources = Depends(get_resources)) -> NeedOut:
    return await startups.add_need(session, resources.embedder, me, startup_id, body)


@router.post("/{startup_id}/claims", response_model=ClaimOut, status_code=status.HTTP_201_CREATED,
             summary="Claim a startup", description="Level 2+. Ask to become the owner of an unclaimed startup "
                                                    "profile. A partner_admin reviews your evidence.")
async def claim_startup(startup_id: uuid.UUID, body: ClaimCreate, me: CurrentMember = Depends(verified),
                        session: AsyncSession = Depends(get_session)) -> ClaimOut:
    return await startups.create_claim(session, me, startup_id, body)
