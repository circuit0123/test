"""Fixtures for tests that need the real docker compose services.

All of these use the separate `circuit_test` database, never your dev data.
If the services are not running, every test in this folder is skipped.
"""

import json
import os
import socket
import uuid

import httpx
import pytest
from redis.asyncio import Redis
from alembic.config import Config
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import insert, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from app.config import get_settings
from app.db import models as m
from app.db.geo import to_point
from app.db.session import create_engine
from app.ingestion import schemas as seed_schemas
from app.ingestion.seed_loader import REFERENCE_DIR, SeedBundle, load_reference
from app.main import create_app
from app.services.auth import issue_dev_token
from tests.jwt_helpers import AUDIENCE, ISSUER, FakeIdP

SERVICES = {"postgres": 5432, "neo4j": 7687, "redis": 6379}


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


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


# ---------------------------------------------------------------- API fixtures

DEV_SECRET = "test-dev-secret-that-is-long-enough-123"
TEST_REDIS_URL = "redis://localhost:6379/15"  # a separate Redis database, wiped around each test


@pytest.fixture(scope="session")
def idp() -> FakeIdP:
    server = FakeIdP()
    yield server
    server.close()


@pytest.fixture
def api_settings(migrated_db, idp, monkeypatch):
    """Settings for the API under test: test database, dev auth, fake JWKS, fake embeddings."""
    env = {
        "ENV": "test", "DATABASE_URL": migrated_db, "DEV_AUTH": "true", "DEV_AUTH_SECRET": DEV_SECRET,
        "EMBEDDING_PROVIDER": "fake", "JWKS_URL": idp.jwks_url, "JWT_ISSUER": ISSUER, "JWT_AUDIENCE": AUDIENCE,
        "REDIS_URL": TEST_REDIS_URL, "SCHEDULER_ENABLED": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def api(engine, api_settings):
    """An HTTP client talking to the app in-process (no real network)."""
    app = create_app()
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.flushdb()
    async with app.router.lifespan_context(app):  # run startup/shutdown like a real server
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    await redis.flushdb()
    await redis.aclose()


class Factory:
    """Tiny helpers to put rows in the test database and get auth headers."""

    def __init__(self, engine, settings) -> None:
        self.engine, self.settings = engine, settings

    async def reference(self) -> None:
        async with self.engine.begin() as conn:
            await load_reference(conn, _reference_bundle())

    async def member(self, role: str = "founder", level: int = 2, **fields) -> uuid.UUID:
        values = {"id": uuid.uuid4(), "role": role, "display_name": f"Test {role}", "verification_level": level,
                  "city": "Testville", **fields}
        if "lat" in values:
            values["location"] = to_point(values.pop("lat"), values.pop("lng"))
        async with self.engine.begin() as conn:
            await conn.execute(insert(m.Member).values(**values))
        return values["id"]

    async def startup(self, domain: str = "acme.example", **fields) -> uuid.UUID:
        values = {"id": uuid.uuid4(), "name": domain.split(".")[0].title(), "website_domain": domain,
                  "sector": "saas", "location": to_point(12.97, 77.59), **fields}
        async with self.engine.begin() as conn:
            await conn.execute(insert(m.Startup).values(**values))
        return values["id"]

    async def headers(self, member_id: uuid.UUID) -> dict[str, str]:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(m.Member.role, m.Member.verification_level, m.Member.token_version).where(m.Member.id == member_id)
            )).one()
        token, _ = issue_dev_token(member_id=member_id, role=row.role, verification_level=row.verification_level,
                                   token_version=row.token_version, settings=self.settings)
        return {"Authorization": f"Bearer {token}"}


def _reference_bundle() -> SeedBundle:
    caps = [seed_schemas.Capability(**c) for c in json.loads((REFERENCE_DIR / "capabilities.json").read_text())]
    traits = [seed_schemas.Trait(**t) for t in json.loads((REFERENCE_DIR / "traits.json").read_text())]
    return SeedBundle(capabilities=caps, traits=traits, startups=[])


@pytest.fixture
async def factory(engine, api_settings) -> Factory:
    f = Factory(engine, api_settings)
    await f.reference()
    return f
