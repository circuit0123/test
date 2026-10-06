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


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_provider_token_finds_member_by_linked_account(api, factory, idp):
    member = await factory.member("investor", level=3, auth_subject="user_2abc")
    r = await api.get("/auth/me", headers=bearer(idp.token("user_2abc", role="investor", verification_level=3)))
    assert r.status_code == 200 and r.json()["id"] == str(member)


async def test_unlinked_provider_account_is_rejected(api, factory, idp):
    r = await api.get("/auth/me", headers=bearer(idp.token("user_nobody")))
    assert r.status_code == 401 and "no member is linked" in r.json()["detail"]


async def test_provider_sub_is_never_treated_as_a_member_id(api, factory, idp):
    # A provider user whose id happens to equal one of our member ids must NOT
    # log in as that member: provider tokens only match on auth_subject.
    member = await factory.member("partner_admin", level=4)
    r = await api.get("/auth/me", headers=bearer(idp.token(str(member), role="partner_admin", verification_level=4)))
    assert r.status_code == 401


async def test_admin_links_account_and_relinking_revokes(api, factory, idp):
    admin = await factory.headers(await factory.member("partner_admin", level=4))
    member = await factory.member("founder")
    await factory.member("founder", auth_subject="user_taken")

    r = await api.put(f"/members/{member}/auth-subject", headers=admin, json={"auth_subject": "user_new"})
    assert r.status_code == 204
    token = idp.token("user_new", token_version=1)  # linking bumped token_version 0 -> 1
    assert (await api.get("/auth/me", headers=bearer(token))).json()["id"] == str(member)

    r = await api.put(f"/members/{member}/auth-subject", headers=admin, json={"auth_subject": "user_taken"})
    assert r.status_code == 409
    r = await api.put(f"/members/{member}/auth-subject", headers=admin, json={"auth_subject": None})
    assert r.status_code == 204
    assert (await api.get("/auth/me", headers=bearer(token))).status_code == 401


async def test_admin_can_create_member_with_linked_account(api, factory, idp):
    admin = await factory.headers(await factory.member("partner_admin", level=4))
    body = {"role": "mentor", "display_name": "Linked", "auth_subject": "user_fresh"}
    r = await api.post("/members", headers=admin, json=body)
    assert r.status_code == 201
    assert (await api.get("/auth/me", headers=bearer(idp.token("user_fresh")))).json()["id"] == r.json()["id"]
    assert (await api.post("/members", headers=admin, json=body)).status_code == 409


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
