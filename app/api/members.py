import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LEVEL_BROWSE, CurrentMember, get_resources, require_admin, require_level
from app.db.session import get_session
from app.resources import Resources
from app.schemas.common import Page, Pagination, pagination
from app.schemas.members import (
    MemberCreate,
    MemberMe,
    MemberProfile,
    MemberSummary,
    MemberUpdate,
    Role,
    TraitsUpdate,
    VerificationUpdate,
)
from app.schemas.needs import NeedCreate, NeedOut, OfferCreate, OfferOut
from app.schemas.startups import ClaimOut
from app.services import members, needs, startups

router = APIRouter(prefix="/members", tags=["members"])
browse = require_level(LEVEL_BROWSE)

# Note: the /me routes are declared before /{member_id} so "me" is not parsed as an id.


@router.get("/me", response_model=MemberMe, summary="My profile",
            description="Your full profile, including private settings, coordinates and inactive needs/offers.")
async def get_me(me: CurrentMember = Depends(browse), session: AsyncSession = Depends(get_session)) -> MemberMe:
    return await members.get_me(session, me.id)


@router.patch("/me", response_model=MemberMe, summary="Update my profile",
              description="Change any subset of your profile fields. Send lat and lng together.")
async def update_me(body: MemberUpdate, me: CurrentMember = Depends(browse),
                    session: AsyncSession = Depends(get_session)) -> MemberMe:
    return await members.update_me(session, me.id, body)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="Delete my account",
               description="Permanently deletes your member record and everything that belongs to it.")
async def delete_me(me: CurrentMember = Depends(browse), session: AsyncSession = Depends(get_session)) -> Response:
    await members.delete_member(session, me.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/me/traits", response_model=list[str], summary="Set my traits",
            description="Replaces your traits with the given slugs (see GET /traits).")
async def set_traits(body: TraitsUpdate, me: CurrentMember = Depends(browse),
                     session: AsyncSession = Depends(get_session)) -> list[str]:
    return await members.set_traits(session, me.id, body.traits)


@router.post("/me/revoke-tokens", status_code=status.HTTP_204_NO_CONTENT, summary="Log out everywhere",
             description="Invalidates every token issued to you so far, on all devices.")
async def revoke_tokens(me: CurrentMember = Depends(browse), session: AsyncSession = Depends(get_session)) -> Response:
    await members.revoke_tokens(session, me.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/needs", response_model=list[NeedOut], summary="My needs",
            description="All your personal needs, including inactive and expired ones.")
async def my_needs(me: CurrentMember = Depends(browse), session: AsyncSession = Depends(get_session)) -> list[NeedOut]:
    return await needs.list_needs(session, "member", me.id, only_live=False)


@router.post("/me/needs", response_model=NeedOut, status_code=status.HTTP_201_CREATED, summary="Add a need",
             description="Adds something you are looking for. Its embedding is computed for matching.")
async def add_need(body: NeedCreate, me: CurrentMember = Depends(browse),
                   session: AsyncSession = Depends(get_session),
                   resources: Resources = Depends(get_resources)) -> NeedOut:
    return await needs.create_need(session, resources.embedder, "member", me.id, body)


@router.get("/me/offers", response_model=list[OfferOut], summary="My offers",
            description="All your offers, including inactive ones.")
async def my_offers(me: CurrentMember = Depends(browse),
                    session: AsyncSession = Depends(get_session)) -> list[OfferOut]:
    return await needs.list_offers(session, me.id, only_active=False)


@router.post("/me/offers", response_model=OfferOut, status_code=status.HTTP_201_CREATED, summary="Add an offer",
             description="Adds something you can help others with. Its embedding is computed for matching.")
async def add_offer(body: OfferCreate, me: CurrentMember = Depends(browse),
                    session: AsyncSession = Depends(get_session),
                    resources: Resources = Depends(get_resources)) -> OfferOut:
    return await needs.create_offer(session, resources.embedder, me.id, body)


@router.get("/me/claims", response_model=Page[ClaimOut], summary="My startup claims",
            description="The startup claims you have made and their status.")
async def my_claims(me: CurrentMember = Depends(browse), page: Pagination = Depends(pagination),
                    session: AsyncSession = Depends(get_session)) -> Page[ClaimOut]:
    return await startups.list_claims(session, page, member_id=me.id)


@router.get("", response_model=Page[MemberSummary], summary="List members",
            description="Browse members, optionally filtered by role, city, an offered capability or a trait.")
async def list_members(
    role: Role | None = None,
    city: str | None = None,
    capability: str | None = Query(None, description="Only members actively offering this capability slug."),
    trait: str | None = Query(None, description="Only members with this trait slug."),
    page: Pagination = Depends(pagination),
    _: CurrentMember = Depends(browse),
    session: AsyncSession = Depends(get_session),
) -> Page[MemberSummary]:
    return await members.list_members(session, page, role=role, city=city, capability=capability, trait=trait)


@router.post("", response_model=MemberMe, status_code=status.HTTP_201_CREATED, summary="Create a member (admin)",
             description="partner_admin only. Creates a member record, e.g. when onboarding a cohort.")
async def create_member(body: MemberCreate, _: CurrentMember = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)) -> MemberMe:
    return await members.create_member(session, body)


@router.get("/{member_id}", response_model=MemberProfile, summary="Member profile",
            description="A member's public profile: active needs and offers, traits, startups. Never coordinates.")
async def get_member(member_id: uuid.UUID, _: CurrentMember = Depends(browse),
                     session: AsyncSession = Depends(get_session)) -> MemberProfile:
    return await members.get_profile(session, member_id)


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a member (admin)",
               description="partner_admin only. Permanently deletes a member.")
async def delete_member(member_id: uuid.UUID, _: CurrentMember = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)) -> Response:
    await members.delete_member(session, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{member_id}/verification", response_model=MemberSummary, summary="Set verification level (admin)",
            description="partner_admin only. Changes a member's level and revokes their existing tokens.")
async def set_verification(member_id: uuid.UUID, body: VerificationUpdate,
                           _: CurrentMember = Depends(require_admin),
                           session: AsyncSession = Depends(get_session)) -> MemberSummary:
    return await members.set_verification(session, member_id, body.verification_level)
