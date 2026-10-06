"""Fixtures for tests that need the real docker compose services.

All of these use the separate `circuit_test` database, never your dev data.
If the services are not running, every test in this folder is skipped.
"""

import os
import socket

import pytest
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from app.config import get_settings
from app.db.session import create_engine

SERVICES = {"postgres": 5432, "neo4j": 7687, "redis": 6379}


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pytest_collection_modifyitems(config, items):
    missing = [name for name, port in SERVICES.items() if not _port_open(port)]
    if not missing:
        return
    skip = pytest.mark.skip(reason=f"services not running: {', '.join(missing)} (run `docker compose up -d`)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def test_db_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    return make_url(get_settings().database_url).set(database="circuit_test").render_as_string(hide_password=False)


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False  # keep pytest's log capture working
    return cfg


def reset_schema(url: str) -> None:
    """Drop everything in the test database (including extensions) for a true from-scratch run."""
    engine = create_sync_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(scope="session")
def migrated_db(test_db_url: str) -> str:
    """A test database built from scratch by running every migration once per session."""
    reset_schema(test_db_url)
    command.upgrade(alembic_config(test_db_url), "head")
    return test_db_url


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine(migrated_db: str) -> AsyncEngine:
    """Async engine on the test database; all data is wiped after each test."""
    eng = create_engine(migrated_db)
    yield eng
    async with eng.begin() as conn:
        tables = await conn.run_sync(
            lambda c: [t for t in c.dialect.get_table_names(c) if t not in ("alembic_version", "spatial_ref_sys")]
        )
        if tables:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await eng.dispose()
