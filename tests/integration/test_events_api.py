"""Product events: client events, admin listing, server events for actions and recommendations."""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def rows(engine, sql="SELECT type, actor_id, target_type, target_id, payload FROM events ORDER BY id"):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).all()


async def test_client_events(api, factory, engine):
    me = await factory.member()
    h = await factory.headers(me)
    target = await factory.member()
    r = await api.post("/events", headers=h, json={"type": "match_clicked", "target_type": "member",
                                                   "target_id": str(target), "payload": {"rank": 2}})
    assert r.status_code == 204
    (row,) = await rows(engine)
    assert (row.type, row.actor_id, row.target_id, row.payload) == ("match_clicked", me, str(target), {"rank": 2})

    bad_type = {"type": "intro_accepted", "target_type": "member", "target_id": str(target)}
    assert (await api.post("/events", headers=h, json=bad_type)).status_code == 422  # server-only type
    huge = {"type": "profile_viewed", "target_type": "member", "target_id": str(target), "payload": {"x": "y" * 3000}}
    assert (await api.post("/events", headers=h, json=huge)).status_code == 422
    assert (await api.post("/events", json=bad_type)).status_code == 401


async def test_admin_can_list_and_filter_events(api, factory):
    admin = await factory.headers(await factory.member("partner_admin", level=4))
    member = await factory.member()
    h = await factory.headers(member)
    await api.patch("/members/me", headers=h, json={"bio": "Hello there", "lat": 12.9, "lng": 77.6})
    await api.put("/members/me/traits", headers=h, json={"traits": ["technical"]})
    page = (await api.get("/events", headers=admin)).json()
    assert [e["type"] for e in page["items"]] == ["traits_updated", "profile_updated"]  # newest first
    assert page["items"][1]["payload"] == {"fields": ["bio", "location"]}  # field names only, never values
    filtered = (await api.get("/events", headers=admin, params={"type": "profile_updated"})).json()
    assert filtered["total"] == 1 and filtered["items"][0]["actor_id"] == str(member)
    assert (await api.get("/events", headers=h)).status_code == 403


async def test_write_actions_are_logged(api, factory, engine):
    founder = await factory.member("founder", level=2)
    h = await factory.headers(founder)
    need = (await api.post("/members/me/needs", headers=h, json={"capability": "seo", "text": "Need SEO help soon"})).json()
    await api.patch(f"/needs/{need['id']}", headers=h, json={"active": False})
    offer = (await api.post("/members/me/offers", headers=h, json={"capability": "b2b-sales", "text": "Sales playbooks"})).json()
    await api.delete(f"/offers/{offer['id']}", headers=h)
    sid = (await api.post("/startups", headers=h, json={"name": "Acme", "website_domain": "acme.io"})).json()["id"]
    await api.patch(f"/startups/{sid}", headers=h, json={"hiring": True})

    crawled = await factory.startup("crawled.example")
    claim = (await api.post(f"/startups/{crawled}/claims", headers=h, json={"evidence": "Company email domain"})).json()
    admin = await factory.member("partner_admin", level=4)
    await api.post(f"/startups/claims/{claim['id']}/approve", headers=await factory.headers(admin))

    logged = await rows(engine)
    assert [r.type for r in logged] == [
        "need_created", "need_updated", "offer_created", "offer_deleted", "startup_created", "startup_updated",
        "claim_created", "claim_approved",
    ]
    assert logged[1].payload == {"fields": ["active"]}
    assert logged[-1].actor_id == admin and logged[-1].payload["claimant_id"] == str(founder)
    assert all("text" not in r.payload for r in logged)  # free text never stored in events
