"""The seed loader against a real Postgres (test database), with fake embeddings."""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.db import models as m
from app.embeddings.fake import FakeEmbeddingProvider
from app.ingestion.seed_loader import DEFAULT_DATA_DIR, SeedBundle, load

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


class CountingEmbedder(FakeEmbeddingProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return super().embed(texts)


async def _counts(engine) -> dict[str, int]:
    async with engine.connect() as conn:
        out = {}
        for model in (m.Capability, m.Trait, m.Startup, m.Member, m.Need, m.Offer, m.Connection,
                      m.MemberTrait, m.StartupTrait, m.StartupMember):
            out[model.__tablename__] = await conn.scalar(select(func.count()).select_from(model))
        return out


@pytest.fixture(scope="module")
def bundle() -> SeedBundle:
    return SeedBundle.from_dirs(DEFAULT_DATA_DIR)


async def test_loads_everything_with_embeddings(engine, bundle):
    summary = await load(engine, bundle, FakeEmbeddingProvider())
    counts = await _counts(engine)
    assert counts["startups"] == len(bundle.startups)
    assert counts["members"] == len(bundle.members)
    assert counts["needs"] == summary["needs"] and counts["offers"] == summary["offers"]
    assert counts["connections"] == len(bundle.connections)
    async with engine.connect() as conn:
        missing = await conn.scalar(select(func.count()).select_from(m.Need).where(m.Need.embedding.is_(None)))
        assert missing == 0
        dims = await conn.scalar(text("SELECT vector_dims(embedding) FROM offers LIMIT 1"))
        assert dims == 384


async def test_is_idempotent_and_skips_unchanged_embeddings(engine, bundle):
    first = CountingEmbedder()
    await load(engine, bundle, first)
    before = await _counts(engine)
    second = CountingEmbedder()
    await load(engine, bundle, second)
    assert await _counts(engine) == before
    assert first.calls > 0 and second.calls == 0


async def test_changed_text_is_re_embedded(engine, bundle):
    await load(engine, bundle, FakeEmbeddingProvider())
    student = next(mb for mb in bundle.members if mb.role == "student")
    student.needs[0].text = "Completely new need text about patents"
    embedder = CountingEmbedder()
    await load(engine, bundle, embedder)
    assert embedder.calls == 1
    async with engine.connect() as conn:
        stored = await conn.scalar(select(m.Need.text).where(m.Need.id == student.needs[0].id))
    assert stored == "Completely new need text about patents"


async def test_crawler_update_keeps_claim_and_updates_crawler_fields(engine, bundle):
    await load(engine, bundle, FakeEmbeddingProvider())
    claimed = next(p for p in bundle.profiles if p.team)
    crawled = next(st for st in bundle.startups if st.website_domain == claimed.website_domain)
    # Simulate a later crawl of just this startup with new info.
    crawled_again = crawled.model_copy(update={"hiring": not crawled.hiring, "source": "crawler"})
    await load(engine, SeedBundle(bundle.capabilities, bundle.traits, [crawled_again]), FakeEmbeddingProvider())
    async with engine.connect() as conn:
        row = (await conn.execute(
            select(m.Startup.hiring, m.Startup.claimed_by_member_id, m.Startup.source)
            .where(m.Startup.website_domain == crawled.website_domain)
        )).one()
    assert row.hiring == (not crawled.hiring)
    assert row.claimed_by_member_id == claimed.team[0].member_id
    assert row.source == "seed"  # provenance is kept from the first insert


async def test_spatial_and_vector_queries_work(engine, bundle):
    await load(engine, bundle, FakeEmbeddingProvider())
    cases = json.loads((Path(DEFAULT_DATA_DIR) / "bridge_cases.json").read_text())
    async with engine.connect() as conn:
        # PostGIS: startups within 5 km of the centre, nearest first, distance in metres.
        rows = (await conn.execute(text("""
            SELECT name, ST_Distance(location, ST_MakePoint(77.5946, 12.9716)::geography) AS metres
            FROM startups
            WHERE ST_DWithin(location, ST_MakePoint(77.5946, 12.9716)::geography, 5000)
            ORDER BY metres LIMIT 5
        """))).all()
        assert rows and all(r.metres <= 5000 for r in rows)
        assert [r.metres for r in rows] == sorted(r.metres for r in rows)

        # pgvector: offers with the same capability, ranked by cosine distance to the bridge need.
        case = cases[0]
        ranked = (await conn.execute(text("""
            SELECT o.id::text FROM offers o, needs n
            WHERE n.id = :need AND o.capability_id = n.capability_id
            ORDER BY o.embedding <=> n.embedding LIMIT 3
        """), {"need": case["need_id"]})).scalars().all()
        assert case["offer_id"] in ranked
