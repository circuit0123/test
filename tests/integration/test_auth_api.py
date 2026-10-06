"""Auth end to end through the API: tokens, revocation, levels, roles."""

import logging

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def test_dev_token_by_role_then_me(api, factory):
    low = await factory.member("mentor", level=2)
    high = await factory.member("mentor", level=4)
    r = await api.post("/auth/dev/token", json={"role": "mentor"})
    assert r.status_code == 200
    body = r.json()
    assert body["member"]["id"] == str(high)  # most-verified member of that role
    me = await api.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json() == {"id": str(high), "role": "mentor", "verification_level": 4}
    by_id = await api.post("/auth/dev/token", json={"member_id": str(low)})
    assert by_id.json()["member"]["id"] == str(low)


async def test_dev_token_validation(api, factory):
    assert (await api.post("/auth/dev/token", json={})).status_code == 422
    assert (await api.post("/auth/dev/token", json={"role": "investor"})).status_code == 404


async def test_jwks_token_works_end_to_end(api, factory, idp):
    member = await factory.member("investor", level=3)
    token = idp.token(member, role="investor", verification_level=3)
    r = await api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["id"] == str(member)


async def test_unknown_member_is_rejected(api, factory, idp):
    import uuid

    r = await api.get("/auth/me", headers={"Authorization": f"Bearer {idp.token(uuid.uuid4())}"})
    assert r.status_code == 401 and "unknown member" in r.json()["detail"]


async def test_revoke_tokens_invalidates_old_tokens(api, factory):
    member = await factory.member()
    old = await factory.headers(member)
    assert (await api.post("/members/me/revoke-tokens", headers=old)).status_code == 204
    r = await api.get("/auth/me", headers=old)
    assert r.status_code == 401 and "revoked" in r.json()["detail"]
    assert (await api.get("/auth/me", headers=await factory.headers(member))).status_code == 200


async def test_level_change_revokes_tokens_and_takes_effect(api, factory):
    admin = await factory.member("partner_admin", level=4)
    student = await factory.member("student", level=1)
    old = await factory.headers(student)
    startup = {"name": "Nope", "website_domain": "nope.example"}
    r = await api.post("/startups", json=startup, headers=old)
    assert r.status_code == 403 and "level 2" in r.json()["detail"]

    r = await api.put(f"/members/{student}/verification", json={"verification_level": 2},
                      headers=await factory.headers(admin))
    assert r.status_code == 200 and r.json()["verification_level"] == 2
    assert (await api.get("/auth/me", headers=old)).status_code == 401  # old token carries old level
    r = await api.post("/startups", json=startup, headers=await factory.headers(student))
    assert r.status_code == 201


async def test_admin_only_endpoints_forbid_others(api, factory):
    founder = await factory.member("founder", level=4)
    headers = await factory.headers(founder)
    assert (await api.get("/startups/claims", headers=headers)).status_code == 403
    assert (await api.post("/members", json={"role": "student", "display_name": "x"}, headers=headers)).status_code == 403


async def test_request_log_contains_user_id_not_token(api, factory, caplog):
    caplog.set_level(logging.INFO)
    member = await factory.member()
    headers = await factory.headers(member)
    await api.get("/auth/me", headers=headers)
    lines = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg.get("event") == "request"]
    assert lines[-1]["user_id"] == str(member)
    token = headers["Authorization"].split()[1]
    assert all(token not in str(r.msg) for r in caplog.records)
