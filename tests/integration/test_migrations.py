"""Migrations apply from scratch, match the models, and roll back cleanly."""

import pytest
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.db.base import Base
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration


def _tables(url: str) -> set[str]:
    engine = create_sync_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"spatial_ref_sys"}
    finally:
        engine.dispose()


def test_upgrade_from_scratch_creates_every_model_table(migrated_db):
    assert _tables(migrated_db) == set(Base.metadata.tables) | {"alembic_version"}


def test_models_and_migrations_are_in_sync(migrated_db):
    # Fails if someone changes a model without writing a migration.
    command.check(alembic_config(migrated_db))


def test_expected_indexes_exist(migrated_db):
    engine = create_sync_engine(migrated_db)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"))
        indexes = dict(rows.all())
    engine.dispose()
    assert "USING gist (location)" in indexes["ix_members_location"]
    assert "USING gist (location)" in indexes["ix_startups_location"]
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_needs_embedding_hnsw"]
    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["ix_offers_embedding_hnsw"]


def test_downgrade_then_upgrade_round_trips(migrated_db):
    cfg = alembic_config(migrated_db)
    command.downgrade(cfg, "base")
    assert _tables(migrated_db) == {"alembic_version"}
    command.upgrade(cfg, "head")
    assert _tables(migrated_db) == set(Base.metadata.tables) | {"alembic_version"}


@pytest.mark.anyio
async def test_events_are_append_only(engine):
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO events (type, payload) VALUES ('test', '{}')"))
    for sql in ("UPDATE events SET type = 'changed'", "DELETE FROM events"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(sql))


@pytest.mark.anyio
async def test_connections_must_be_stored_in_order(engine):
    lo, hi = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
    async with engine.begin() as conn:
        for mid in (lo, hi):
            await conn.execute(text("INSERT INTO members (id, role, display_name) VALUES (:id, 'student', 'x')"), {"id": mid})
    with pytest.raises(IntegrityError, match="ck_connections_ordered"):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO connections (member_a, member_b, source) VALUES (:a, :b, 'mutual')"), {"a": hi, "b": lo}
            )
