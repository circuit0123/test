from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One page of a list. `total` counts every match, not just this page."""

    items: list[T]
    total: int
    limit: int
    offset: int


class Pagination(BaseModel):
    limit: int
    offset: int


def pagination(
    limit: int = Query(20, ge=1, le=100, description="Page size (max 100)."),
    offset: int = Query(0, ge=0, description="Number of items to skip."),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)
