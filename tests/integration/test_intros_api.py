"""Intro requests: the happy path, every capacity rule, permissions, expiry, races."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text, update

from app.config import IntroSettings
from app.db import models as m
from app.services.intros import expire_stale

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
REASON = "I'd love your advice on our seed round"


async def ask(api, headers, to, **extra):
    return await api.post("/intros", headers=headers, json={"to_member": str(to), "reason": REASON, **extra})


async def event_types(engine) -> list[str]:
    async with engine.connect() as conn:
        return list((await conn.execute(text("SELECT type FROM events ORDER BY id"))).scalars())


async def test_request_accept_connect_and_rate(api, factory, engine):
    a, b = await factory.member("founder", level=3), await factory.member("mentor", level=2)
    ah, bh = await factory.headers(a), await factory.headers(b)

    r = await ask(api, ah, b)
    assert r.status_code == 201, r.text
    intro = r.json()
    assert intro["status"] == "pending" and intro["expires_at"] and intro["match_source"] is None
    assert (await api.get("/intros/incoming", headers=bh)).json()["items"][0]["id"] == intro["id"]
    assert (await api.get("/intros/outgoing", headers=ah)).json()["total"] == 1

    r = await api.post(f"/intros/{intro['id']}/accept", headers=bh, json={"note": "Happy to help"})
    assert r.json()["status"] == "accepted" and r.json()["response_note"] == "Happy to help"
    async with engine.connect() as conn:
        lo, hi = sorted([a, b])
        conn_row = (await conn.execute(select(m.Connection).where(m.Connection.member_a == lo,
                                                                  m.Connection.member_b == hi))).one()
    assert conn_row.source == "intro" and conn_row.strength == 0.5  # the intro created a connection

    assert (await api.post(f"/intros/{intro['id']}/rating", headers=ah, json={"rating": 5})).json()["my_rating"] == 5
    assert (await api.post(f"/intros/{intro['id']}/rating", headers=bh, json={"rating": 4})).json()["my_rating"] == 4
    assert (await api.post(f"/intros/{intro['id']}/rating", headers=ah, json={"rating": 6})).status_code == 422
    assert await event_types(engine) == ["intro_requested", "intro_accepted", "intro_rated", "intro_rated"]


async def test_requesting_needs_level_3(api, factory):
    low = await factory.headers(await factory.member("founder", level=2))
    r = await ask(api, low, await factory.member())
    assert r.status_code == 403 and "level 3" in r.json()["detail"]


async def test_one_request_per_pair_and_no_repeats(api, factory):
    a, b = await factory.member(level=3), await factory.member(level=3)
    ah, bh = await factory.headers(a), await factory.headers(b)
    intro = (await ask(api, ah, b)).json()
    assert "already have a pending" in (await ask(api, ah, b)).json()["detail"]
    assert "accept their request" in (await ask(api, bh, a)).json()["detail"]
    await api.post(f"/intros/{intro['id']}/accept", headers=bh)
    r = await ask(api, ah, b)
    assert r.status_code == 409 and "already been introduced" in r.json()["detail"]


async def test_decline_then_cooldown(api, factory, engine):
    a, b = await factory.member(level=3), await factory.member()
    ah, bh = await factory.headers(a), await factory.headers(b)
    intro = (await ask(api, ah, b)).json()
    assert (await api.post(f"/intros/{intro['id']}/decline", headers=bh)).json()["status"] == "declined"
    r = await ask(api, ah, b)
    assert r.status_code == 409 and "declined recently" in r.json()["detail"]
    async with engine.begin() as conn:  # pretend the decline was 31 days ago
        await conn.execute(update(m.IntroRequest).values(responded_at=datetime.now(UTC) - timedelta(days=31)))
    assert (await ask(api, ah, b)).status_code == 201


async def test_recipient_inbox_capacity(api, factory):
    busy = await factory.member(open_intro_slots=1)
    closed = await factory.member(open_intro_slots=0)
    first, second = await factory.member(level=3), await factory.member(level=3)
    assert (await ask(api, await factory.headers(first), busy)).status_code == 201
    r = await ask(api, await factory.headers(second), busy)
    assert r.status_code == 409 and "inbox is full" in r.json()["detail"]
    r = await ask(api, await factory.headers(second), closed)
    assert r.status_code == 409 and "isn't accepting" in r.json()["detail"]


async def test_requester_pending_and_weekly_limits(api, factory, engine):
    me = await factory.member(level=3)
    h = await factory.headers(me)
    targets = [await factory.member() for _ in range(6)]
    sent = [(await ask(api, h, t)).json() for t in targets[:5]]
    r = await ask(api, h, targets[5])
    assert r.status_code == 429 and "requests waiting" in r.json()["detail"]
    limits = (await api.get("/intros/limits", headers=h)).json()
    assert limits["pending_outgoing"] == 5 and limits["max_pending_outgoing"] == 5

    assert (await api.post(f"/intros/{sent[0]['id']}/cancel", headers=h)).json()["status"] == "cancelled"
    assert (await ask(api, h, targets[5])).status_code == 201  # cancelling freed a slot

    # Weekly limit: 10 non-cancelled requests in 7 days. Cancel one more so the
    # pending limit (5) isn't what stops us: 4 pending + 6 declined = 10 this week.
    await api.post(f"/intros/{sent[1]['id']}/cancel", headers=h)
    others = [await factory.member() for _ in range(6)]
    async with engine.begin() as conn:
        await conn.execute(insert(m.IntroRequest), [
            {"id": uuid.uuid4(), "from_member": me, "to_member": o, "status": "declined", "reason": "x"}
            for o in others])
    r = await ask(api, h, await factory.member())
    assert r.status_code == 429 and "per 7 days" in r.json()["detail"]


async def test_permissions(api, factory):
    a, b, outsider = await factory.member(level=3), await factory.member(), await factory.member()
    ah, bh, oh = await factory.headers(a), await factory.headers(b), await factory.headers(outsider)
    intro = (await ask(api, ah, b)).json()
    assert (await api.post(f"/intros/{intro['id']}/accept", headers=ah)).status_code == 403  # can't accept own
    assert (await api.post(f"/intros/{intro['id']}/cancel", headers=bh)).status_code == 403  # only requester
    assert (await api.get(f"/intros/{intro['id']}", headers=oh)).status_code == 404  # invisible to others
    assert (await api.post(f"/intros/{intro['id']}/rating", headers=ah, json={"rating": 3})).status_code == 409
    assert (await ask(api, ah, a)).status_code == 422
    assert (await ask(api, ah, uuid.uuid4())).status_code == 404


async def test_warm_intro_via_mutual(api, factory, engine):
    a, via, b, stranger = (await factory.member(level=3), await factory.member(),
                           await factory.member(), await factory.member())
    async with engine.begin() as conn:
        for x, y in [(a, via), (via, b)]:
            lo, hi = sorted([x, y])
            await conn.execute(insert(m.Connection).values(member_a=lo, member_b=hi, strength=0.8, source="mutual"))
    ah = await factory.headers(a)
    r = await ask(api, ah, b, via_member=str(stranger))
    assert r.status_code == 422 and "connected to both" in r.json()["detail"]
    r = await ask(api, ah, b, via_member=str(via))
    assert r.status_code == 201 and r.json()["via_member"]["id"] == str(via)
    assert (await api.get(f"/intros/{r.json()['id']}", headers=await factory.headers(via))).status_code == 200


async def test_match_source_is_recorded(api, factory, engine):
    a, b = await factory.member(level=3), await factory.member()
    async with engine.begin() as conn:
        await conn.execute(insert(m.MatchResult).values(member_id=a, candidate_member_id=b, score=0.42,
                                                        components={}, source="bridge"))
    intro = (await ask(api, await factory.headers(a), b)).json()
    assert intro["match_source"] == "bridge"


async def test_expiry(api, factory, engine):
    a, b, c = await factory.member(level=3), await factory.member(), await factory.member()
    old = datetime.now(UTC) - timedelta(days=15)
    stale_ids = [uuid.uuid4(), uuid.uuid4()]
    async with engine.begin() as conn:
        await conn.execute(insert(m.IntroRequest), [
            {"id": stale_ids[0], "from_member": a, "to_member": b, "reason": "x", "created_at": old},
            {"id": stale_ids[1], "from_member": a, "to_member": c, "reason": "x", "created_at": old}])
    # Answering a stale request marks it expired and refuses.
    r = await api.post(f"/intros/{stale_ids[0]}/accept", headers=await factory.headers(b))
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    # A new request to C works even before the job runs (the stale one is expired on the spot)...
    assert (await ask(api, await factory.headers(a), c)).status_code == 201
    # ...and the job finds nothing left to expire.
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(engine)() as session:
        assert await expire_stale(session, IntroSettings()) == 0
    assert (await event_types(engine)).count("intro_expired") == 2


async def test_expire_job_marks_stale_requests(engine, factory):
    a, b = await factory.member(level=3), await factory.member()
    async with engine.begin() as conn:
        await conn.execute(insert(m.IntroRequest).values(
            id=uuid.uuid4(), from_member=a, to_member=b, reason="x", created_at=datetime.now(UTC) - timedelta(days=20)))
    from sqlalchemy.ext.asyncio import async_sessionmaker
    async with async_sessionmaker(engine)() as session:
        assert await expire_stale(session, IntroSettings()) == 1
    async with engine.connect() as conn:
        assert await conn.scalar(select(m.IntroRequest.status)) == "expired"


async def test_last_slot_race_lets_exactly_one_through(api, factory):
    """Two requests for the last inbox slot at the same moment: the row lock serialises them."""
    target = await factory.member(open_intro_slots=1)
    x, y = await factory.member(level=3), await factory.member(level=3)
    hx, hy = await factory.headers(x), await factory.headers(y)
    results = await asyncio.gather(ask(api, hx, target), ask(api, hy, target))
    assert sorted(r.status_code for r in results) == [201, 409]
