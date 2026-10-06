from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.reference import CapabilityOut, TraitOut
from app.services import reference

router = APIRouter(tags=["reference"])


@router.get(
    "/capabilities",
    response_model=list[CapabilityOut],
    summary="List capabilities",
    description="The fixed list of capabilities that needs and offers refer to (public, rarely changes).",
)
async def capabilities(session: AsyncSession = Depends(get_session)) -> list[CapabilityOut]:
    return await reference.list_capabilities(session)


@router.get(
    "/traits",
    response_model=list[TraitOut],
    summary="List traits",
    description="The fixed list of descriptive traits for members and startups (public, rarely changes).",
)
async def traits(session: AsyncSession = Depends(get_session)) -> list[TraitOut]:
    return await reference.list_traits(session)
