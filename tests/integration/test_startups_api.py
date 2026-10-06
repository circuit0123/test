"""Startups CRUD, startup needs and the claiming flow through the API."""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_list_and_detail(api, factory):
    viewer = await factory.headers(await factory.member("student", level=1))
    await factory.startup("alpha.example", sector="fintech", hiring=True)
    await factory.startup("beta.example", sector="biotech", hiring=False)
    page = (await api.get("/startups", headers=viewer, params={"hiring": "true"})).json()
    assert page["total"] == 1 and page["items"][0]["website_domain"] == "alpha.example"
    assert (await api.get("/startups", headers=viewer, params={"q": "BET"})).json()["total"] == 1
    sid = page["items"][0]["id"]
    detail = (await api.get(f"/startups/{sid}", headers=viewer)).json()
    assert (detail["lat"], detail["lng"]) == (12.97, 77.59)
    assert detail["claimed"] is False and detail["team"] == []


async def test_browsing_requires_login(api, factory):
    assert (await api.get("/startups")).status_code == 401


async def test_create_startup_normalises_domain_and_owns_it(api, factory):
    founder = await factory.member("founder", level=2)
    h = await factory.headers(founder)
    r = await api.post("/startups", headers=h, json={
        "name": "Acme", "website_domain": "https://www.Acme.io/about", "lat": 12.9, "lng": 77.6,
        "sector": "saas", "title": "CEO",
    })
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["website_domain"] == "acme.io" and s["source"] == "user"
    assert s["claimed_by_member_id"] == str(founder)
    assert s["team"] == [{"member_id": str(founder), "display_name": "Test founder", "title": "CEO"}]

    dup = await api.post("/startups", headers=h, json={"name": "Acme 2", "website_domain": "acme.io"})
    assert dup.status_code == 409 and "claim it instead" in dup.json()["detail"]
    bad = await api.post("/startups", headers=h, json={"name": "X", "website_domain": "not a domain"})
    assert bad.status_code == 422


async def test_only_team_can_edit_or_add_needs(api, factory):
    founder = await factory.member("founder")
    h = await factory.headers(founder)
    sid = (await api.post("/startups", headers=h, json={"name": "Acme", "website_domain": "acme.io"})).json()["id"]
    outsider = await factory.headers(await factory.member("founder"))

    assert (await api.patch(f"/startups/{sid}", headers=outsider, json={"hiring": True})).status_code == 403
    r = await api.patch(f"/startups/{sid}", headers=h, json={"hiring": True, "open_roles": ["Backend Engineer"]})
    assert r.status_code == 200 and r.json()["hiring"] is True
    assert (await api.patch(f"/startups/{sid}", headers=h, json={"name": None})).status_code == 422

    need = {"capability": "b2b-sales", "text": "We need a first sales hire playbook"}
    assert (await api.post(f"/startups/{sid}/needs", headers=outsider, json=need)).status_code == 403
    r = await api.post(f"/startups/{sid}/needs", headers=h, json=need)
    assert r.status_code == 201 and r.json()["owner_type"] == "startup"
    detail = (await api.get(f"/startups/{sid}", headers=h)).json()
    assert [n["capability"] for n in detail["needs"]] == ["b2b-sales"]
    # Team members can also edit the startup's needs.
    assert (await api.patch(f"/needs/{r.json()['id']}", headers=h, json={"active": False})).status_code == 200

    assert (await api.delete(f"/startups/{sid}", headers=outsider)).status_code == 403
    assert (await api.delete(f"/startups/{sid}", headers=h)).status_code == 204


async def test_claiming_flow(api, factory):
    sid = await factory.startup("crawled.example", source="crawler")
    alice, bob = await factory.member("founder"), await factory.member("founder")
    ah, bh = await factory.headers(alice), await factory.headers(bob)
    admin = await factory.headers(await factory.member("partner_admin", level=4))
    evidence = {"title": "CEO", "evidence": "Company email alice@crawled.example, LinkedIn profile"}

    # Before claiming, Alice can't edit.
    assert (await api.patch(f"/startups/{sid}", headers=ah, json={"hiring": True})).status_code == 403

    r = await api.post(f"/startups/{sid}/claims", headers=ah, json=evidence)
    assert r.status_code == 201 and r.json()["status"] == "pending"
    alice_claim = r.json()["id"]
    assert (await api.post(f"/startups/{sid}/claims", headers=ah, json=evidence)).status_code == 409  # duplicate
    bob_claim = (await api.post(f"/startups/{sid}/claims", headers=bh, json=evidence)).json()["id"]

    pending = (await api.get("/startups/claims", headers=admin)).json()
    assert pending["total"] == 2

    r = await api.post(f"/startups/claims/{alice_claim}/approve", headers=admin, json={"note": "email verified"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    detail = (await api.get(f"/startups/{sid}", headers=ah)).json()
    assert detail["claimed_by_member_id"] == str(alice)
    assert [t["member_id"] for t in detail["team"]] == [str(alice)]
    assert (await api.patch(f"/startups/{sid}", headers=ah, json={"hiring": True})).status_code == 200

    bobs = (await api.get("/members/me/claims", headers=bh)).json()["items"]
    assert bobs[0]["id"] == bob_claim and bobs[0]["status"] == "rejected"
    assert (await api.post(f"/startups/claims/{bob_claim}/approve", headers=admin)).status_code == 409
    carol = await factory.headers(await factory.member("founder"))
    assert (await api.post(f"/startups/{sid}/claims", headers=carol, json=evidence)).status_code == 409


async def test_claim_rejection_and_level_gate(api, factory):
    sid = await factory.startup("other.example")
    student = await factory.headers(await factory.member("student", level=1))
    evidence = {"evidence": "I am the founder, see our registration"}
    assert (await api.post(f"/startups/{sid}/claims", headers=student, json=evidence)).status_code == 403

    founder = await factory.headers(await factory.member("founder", level=2))
    claim = (await api.post(f"/startups/{sid}/claims", headers=founder, json=evidence)).json()
    admin = await factory.headers(await factory.member("partner_admin", level=4))
    r = await api.post(f"/startups/claims/{claim['id']}/reject", headers=admin, json={"note": "no evidence"})
    assert r.json()["status"] == "rejected" and r.json()["decision_note"] == "no evidence"
    detail = (await api.get(f"/startups/{sid}", headers=founder)).json()
    assert detail["claimed"] is False
    # Rejected claimants may try again with better evidence.
    assert (await api.post(f"/startups/{sid}/claims", headers=founder, json=evidence)).status_code == 201
