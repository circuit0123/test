"""Members, traits, needs and offers through the API."""

import pytest
from sqlalchemy import select, text

from app.db import models as m

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_me_shows_private_fields_and_patch_updates(api, factory):
    member = await factory.member("founder", lat=12.9, lng=77.6)
    h = await factory.headers(member)
    me = (await api.get("/members/me", headers=h)).json()
    assert (me["lat"], me["lng"]) == (12.9, 77.6)
    assert me["open_intro_slots"] == 3

    r = await api.patch("/members/me", headers=h, json={"bio": "Building things", "lat": 13.0, "lng": 77.5,
                                                         "circuits_opt_in": True})
    assert r.status_code == 200
    me = r.json()
    assert (me["bio"], me["lat"], me["lng"], me["circuits_opt_in"]) == ("Building things", 13.0, 77.5, True)


@pytest.mark.parametrize("body", [{"lat": 13.0}, {"display_name": None}, {"open_intro_slots": -1}, {"lat": 100, "lng": 0}])
async def test_patch_me_validation(api, factory, body):
    h = await factory.headers(await factory.member())
    assert (await api.patch("/members/me", headers=h, json=body)).status_code == 422


async def test_public_profile_hides_location_and_inactive_items(api, factory):
    owner = await factory.member("mentor", lat=12.9, lng=77.6)
    oh = await factory.headers(owner)
    live = (await api.post("/members/me/offers", headers=oh, json={"capability": "seo", "text": "I help with SEO audits"})).json()
    paused = (await api.post("/members/me/offers", headers=oh, json={"capability": "b2b-sales", "text": "Sales playbooks for SaaS"})).json()
    await api.patch(f"/offers/{paused['id']}", headers=oh, json={"active": False})

    viewer = await factory.headers(await factory.member("student", level=1))
    profile = (await api.get(f"/members/{owner}", headers=viewer)).json()
    assert "lat" not in profile and "lng" not in profile
    assert [o["id"] for o in profile["offers"]] == [live["id"]]
    mine = (await api.get("/members/me/offers", headers=oh)).json()
    assert {o["id"] for o in mine} == {live["id"], paused["id"]}


async def test_list_members_filters_and_paginates(api, factory):
    viewer = await factory.headers(await factory.member("student", level=1))
    for i in range(3):
        mid = await factory.member("mentor", display_name=f"Mentor {i}")
        await api.post("/members/me/offers", headers=await factory.headers(mid),
                       json={"capability": "fundraising", "text": "Investor intros and pitch prep"})
    await factory.member("investor")
    page = (await api.get("/members", headers=viewer, params={"role": "mentor", "limit": 2})).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    by_cap = (await api.get("/members", headers=viewer, params={"capability": "fundraising"})).json()
    assert by_cap["total"] == 3
    assert (await api.get("/members", headers=viewer, params={"limit": 500})).status_code == 422


async def test_set_traits(api, factory):
    h = await factory.headers(await factory.member())
    r = await api.put("/members/me/traits", headers=h, json={"traits": ["technical", "b2b"]})
    assert r.json() == ["b2b", "technical"]
    r = await api.put("/members/me/traits", headers=h, json={"traits": ["made-up"]})
    assert r.status_code == 422 and "made-up" in r.json()["detail"]


async def test_need_lifecycle_with_embeddings(api, factory, engine):
    owner = await factory.member()
    h = await factory.headers(owner)
    r = await api.post("/members/me/needs", headers=h, json={"capability": "growth-marketing",
                                                             "text": "Need help launching paid ads"})
    assert r.status_code == 201
    need = r.json()
    assert need["owner_type"] == "member" and need["owner_id"] == str(owner) and need["active"]

    async with engine.connect() as conn:
        before = await conn.scalar(text("SELECT embedding::text FROM needs WHERE id = :id"), {"id": need["id"]})
        assert await conn.scalar(text("SELECT vector_dims(embedding) FROM needs WHERE id = :id"), {"id": need["id"]}) == 384

    r = await api.patch(f"/needs/{need['id']}", headers=h, json={"text": "Need help with SEO and content instead"})
    assert r.status_code == 200
    async with engine.connect() as conn:
        after = await conn.scalar(text("SELECT embedding::text FROM needs WHERE id = :id"), {"id": need["id"]})
    assert after != before  # text changed -> re-embedded

    other = await factory.headers(await factory.member())
    assert (await api.patch(f"/needs/{need['id']}", headers=other, json={"active": False})).status_code == 403
    assert (await api.delete(f"/needs/{need['id']}", headers=other)).status_code == 403
    assert (await api.delete(f"/needs/{need['id']}", headers=h)).status_code == 204
    assert (await api.delete(f"/needs/{need['id']}", headers=h)).status_code == 404


async def test_need_validation(api, factory):
    h = await factory.headers(await factory.member())
    r = await api.post("/members/me/needs", headers=h, json={"capability": "nope", "text": "long enough text"})
    assert r.status_code == 422 and "unknown capability" in r.json()["detail"]
    r = await api.post("/members/me/needs", headers=h, json={"capability": "seo", "text": "short"})
    assert r.status_code == 422


async def test_admin_creates_member_and_member_deletes_self(api, factory, engine):
    admin = await factory.headers(await factory.member("partner_admin", level=4))
    r = await api.post("/members", headers=admin, json={"role": "student", "display_name": "New Student",
                                                        "lat": 12.95, "lng": 77.6, "verification_level": 2})
    assert r.status_code == 201
    new_id = r.json()["id"]
    h = await factory.headers(new_id)
    assert (await api.delete("/members/me", headers=h)).status_code == 204
    async with engine.connect() as conn:
        assert await conn.scalar(select(m.Member.id).where(m.Member.id == new_id)) is None
    assert (await api.get("/members/me", headers=h)).status_code == 401
