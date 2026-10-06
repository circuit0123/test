"""Declarative base that every ORM model (Phase 1) inherits from."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
