"""Matching against a small hand-built world where the right answers are known."""

import uuid

import pytest
from redis.asyncio import Redis
from sqlalchemy import insert, select

from app.config import MatchingSettings
from app.db import models as m
from app.embeddings.fake import FakeEmbeddingProvider
from app.jobs.scheduler import create_scheduler
from app.matching.candidates import connection_paths
from app.services import matching
from tests.integration.conftest import TEST_REDIS_URL

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
EMB = FakeEmbeddingProvider()


class World:
    def __init__(self, factory, engine):
        self.f, self.engine = factory, engine
        self.ids: dict[str, uuid.UUID] = {}

    async def cap(self, slug):
        async with self.engine.connect() as conn:
            return await conn.scalar(select(m.Capability.id).where(m.Capability.slug == slug))

    async def trait(self, mid, slug):
        async with self.engine.begin() as conn:
            tid = await conn.scalar(select(m.Trait.id).where(m.Trait.slug == slug))
            await conn.execute(insert(m.MemberTrait).values(member_id=mid, trait_id=tid))

    async def person(self, name, role="mentor", city="Testville", cross=True, traits=(), level=2):
        mid = await self.f.member(role, level=level, city=city, open_to_cross_sector=cross, display_name=name)
        for t in traits:
            await self.trait(mid, t)
        self.ids[name] = mid
        return mid

    async def need(self, owner, slug, text, owner_type="member"):
        async with self.engine.begin() as conn:
            await conn.execute(insert(m.Need).values(
                id=uuid.uuid4(), owner_type=owner_type, owner_id=owner, capability_id=await self.cap(slug),
                text=text, embedding=EMB.embed([text])[0], expires_at=None))

    async def offer(self, member, slug, text):
        async with self.engine.begin() as conn:
            await conn.execute(insert(m.Offer).values(
                id=uuid.uuid4(), member_id=member, capability_id=await self.cap(slug), text=text,
                embedding=EMB.embed([text])[0]))

    async def connect(self, a, b, strength):
        lo, hi = sorted([a, b])
        async with self.engine.begin() as conn:
            await conn.execute(insert(m.Connection).values(member_a=lo, member_b=hi, strength=strength, source="mutual"))


@pytest.fixture
async def world(factory, engine):
    w = World(factory, engine)
    a = await w.person("A", role="founder", traits=["technical"])
    await w.need(a, "seo", "Need help with SEO for our website")
    b = await w.person("B", traits=["technical"])                       # local: same city + shared trait
    await w.offer(b, "seo", "I help with SEO for websites")
    c = await w.person("C", city="Farcity")                             # bridge: far away, open to cross-sector
    await w.offer(c, "seo", "SEO audits and website search ranking")
    d = await w.person("D", city="Farcity", cross=False)                # not open to cross-sector: never found
    await w.offer(d, "seo", "SEO for websites")
    e = await w.person("E", traits=["technical"])                       # local but nothing to offer A
    await w.offer(e, "legal-incorporation", "Company registration paperwork")
    f = await w.person("F", role="partner_admin", traits=["technical"], level=4)  # admins are never suggested
    await w.offer(f, "seo", "SEO for websites")
    g = await w.person("G", role="student", city="Farcity", cross=False)
    h = await w.person("H", cross=False)                                # local only via a mutual connection
    await w.offer(h, "seo", "Website SEO and search help")
    await w.connect(a, g, 0.9)
    await w.connect(g, h, 0.8)
    return w


async def test_connection_paths_walk_two_hops(engine, world):
    async with engine.connect() as conn:
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(bind=conn) as session:
            paths = await connection_paths(session, world.ids["A"])
    assert paths[world.ids["G"]].hops == 1 and paths[world.ids["G"]].strength == pytest.approx(0.9)
    assert paths[world.ids["H"]].hops == 2 and paths[world.ids["H"]].strength == pytest.approx(0.72)


async def compute(api_settings, engine, member_id):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(engine)() as session:
        return await matching.compute_for_member(session, member_id, api_settings.matching)


async def test_candidates_sources_and_exclusions(api_settings, engine, world):
    rows = await compute(api_settings, engine, world.ids["A"])
    by_id = {r.candidate_member_id: r for r in rows}
    names = {n for n, i in world.ids.items() if i in by_id}
    assert names == {"B", "C", "H"}
    assert by_id[world.ids["B"]].source == "local"
    assert by_id[world.ids["H"]].source == "local" and by_id[world.ids["H"]].components["hops"] == 2
    assert by_id[world.ids["C"]].source == "bridge"
    assert by_id[world.ids["C"]].reason.startswith("Cross-sector match. Can help you with seo")
    assert "mutual connection" in by_id[world.ids["H"]].reason
    # The local, trait-sharing helper outranks the stranger.
    assert by_id[world.ids["B"]].score > by_id[world.ids["C"]].score
    assert [r.rank for r in rows] == list(range(1, len(rows) + 1))


async def test_founder_is_matched_on_their_startups_needs(api_settings, engine, factory, world):
    k = await world.person("K", role="founder", traits=["b2b"])
    sid = await factory.startup("kco.example")
    async with engine.begin() as conn:
        await conn.execute(insert(m.StartupMember).values(startup_id=sid, member_id=k, title="CEO"))
    await world.need(sid, "fundraising", "Raising our seed round", owner_type="startup")
    investor = await world.person("L", role="investor", traits=["b2b"])
    await world.offer(investor, "fundraising", "I help founders raise seed rounds")
    rows = await compute(api_settings, engine, k)
    assert rows[0].candidate_member_id == investor
    assert rows[0].components["capability_they_help_with"] == "fundraising"


async def test_weights_come_from_config(api_settings, engine, world):
    base = {r.candidate_member_id: r.score for r in await compute(api_settings, engine, world.ids["A"])}
    heavier = api_settings.model_copy(update={"matching": MatchingSettings(w_complementarity=1.0)})
    boosted = {r.candidate_member_id: r.score for r in await compute(heavier, engine, world.ids["A"])}
    assert all(boosted[i] > base[i] for i in base)


async def test_matches_api_cache_database_and_compute(api, factory, world):
    h = await factory.headers(world.ids["A"])
    first = (await api.get("/matches", headers=h)).json()
    assert first["served_from"] == "computed"
    assert {i["member"]["display_name"] for i in first["items"]} == {"B", "C", "H"}
    assert (await api.get("/matches", headers=h)).json()["served_from"] == "cache"

    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete(matching.cache_key(world.ids["A"]))  # cache lost (e.g. Redis restarted)
    await redis.aclose()
    again = (await api.get("/matches", headers=h)).json()
    assert again["served_from"] == "database" and again["items"] == first["items"]
    assert (await api.get("/matches", headers=h)).json()["served_from"] == "cache"  # re-cached

    refreshed = (await api.get("/matches", headers=h, params={"refresh": "true", "limit": 1})).json()
    assert refreshed["served_from"] == "computed" and len(refreshed["items"]) == 1


async def test_matches_need_level_2(api, factory, world):
    low = await factory.headers(await factory.member("student", level=1))
    assert (await api.get("/matches", headers=low)).status_code == 403


async def test_deleted_candidates_are_not_shown(api, factory, world, engine):
    h = await factory.headers(world.ids["A"])
    await api.get("/matches", headers=h)
    async with engine.begin() as conn:
        await conn.execute(m.Member.__table__.delete().where(m.Member.id == world.ids["C"]))
    names = {i["member"]["display_name"] for i in (await api.get("/matches", headers=h)).json()["items"]}
    assert "C" not in names


async def test_admin_recompute_runs_for_everyone(api, factory, world):
    admin = await factory.headers(world.ids["F"])  # F is a partner_admin
    assert (await api.post("/matches/recompute", headers=await factory.headers(world.ids["A"]))).status_code == 403
    result = (await api.post("/matches/recompute", headers=admin)).json()
    assert result["members"] == len(world.ids) - 1  # everyone except the partner_admin
    assert result["results"] >= 3 and result["bridge_results"] >= 1
    assert (await api.get("/matches", headers=await factory.headers(world.ids["A"]))).json()["served_from"] == "cache"


def test_scheduler_registers_the_match_job(api_settings):
    scheduler = create_scheduler(resources=None, settings=api_settings)  # type: ignore[arg-type]
    job = scheduler.get_job("compute_all_matches")
    assert job.trigger.interval.total_seconds() == api_settings.matching.job_interval_hours * 3600
    assert job.max_instances == 1
