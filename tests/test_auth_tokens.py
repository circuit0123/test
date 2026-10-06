"""Token verification, both paths, without a database."""

import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.services.auth import AuthError, create_jwks_client, issue_dev_token, verify_token
from tests.jwt_helpers import AUDIENCE, ISSUER, FakeIdP

pytestmark = pytest.mark.anyio
DEV_SECRET = "d" * 40
MEMBER = uuid.uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def idp():
    server = FakeIdP()
    yield server
    server.close()


def settings(idp, **overrides) -> Settings:
    base = dict(jwks_url=idp.jwks_url, jwt_issuer=ISSUER, jwt_audience=AUDIENCE, dev_auth=False, _env_file=None)
    return Settings(**{**base, **overrides})


async def check(token: str, s: Settings):
    return await verify_token(token, s, create_jwks_client(s))


async def test_valid_jwks_token(idp):
    verified = await check(idp.token("user_2abc", role="mentor", verification_level=3, token_version=2), settings(idp))
    c = verified.claims
    assert (c.sub, c.role, c.verification_level, c.token_version) == ("user_2abc", "mentor", 3, 2)
    assert verified.is_dev is False


async def test_is_dev_cannot_be_set_from_inside_the_token(idp):
    verified = await check(idp.token("user_2abc", is_dev=True), settings(idp))
    assert verified.is_dev is False


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda idp: idp.token(MEMBER, exp_in=-10), "expired"),
        (lambda idp: idp.token(MEMBER, iss="https://evil.test"), "invalid token"),
        (lambda idp: idp.token(MEMBER, aud="someone-else"), "invalid token"),
        (lambda idp: idp.token(MEMBER, drop=("token_version",)), "invalid token"),
        (lambda idp: idp.token(MEMBER, sub=""), "invalid token claims"),
        (lambda idp: "not.a.jwt", "malformed"),
    ],
    ids=["expired", "wrong-issuer", "wrong-audience", "missing-claim", "empty-sub", "garbage"],
)
async def test_rejected_jwks_tokens(idp, make, reason):
    with pytest.raises(AuthError, match=reason):
        await check(make(idp), settings(idp))


async def test_tampered_token_is_rejected(idp):
    header, payload, sig = idp.token(MEMBER).split(".")
    forged = jwt.encode({"sub": str(MEMBER), "role": "partner_admin"}, "x" * 32, algorithm="HS256").split(".")[1]
    with pytest.raises(AuthError):
        await check(f"{header}.{forged}.{sig}", settings(idp))


async def test_token_signed_by_another_key_is_rejected(idp):
    other = FakeIdP()  # same kid, different private key
    try:
        with pytest.raises(AuthError):
            await check(other.token(MEMBER), settings(idp))
    finally:
        other.close()


async def test_unreachable_jwks_is_rejected_not_crashing(idp):
    with pytest.raises(AuthError, match="could not fetch signing keys"):
        await check(idp.token(MEMBER), settings(idp, jwks_url="http://127.0.0.1:9/jwks.json"))


async def test_alg_none_is_rejected(idp):
    unsigned = jwt.encode({"sub": str(MEMBER), "role": "x", "verification_level": 4, "token_version": 0,
                           "exp": 9999999999}, None, algorithm="none")
    with pytest.raises(AuthError):
        await check(unsigned, settings(idp))


async def test_no_jwks_configured(idp):
    with pytest.raises(AuthError, match="no JWKS_URL"):
        await check(idp.token(MEMBER), settings(idp, jwks_url=None))


async def test_dev_token_accepted_only_when_dev_auth_is_on(idp):
    dev = settings(idp, dev_auth=True, dev_auth_secret=DEV_SECRET)
    token, _ = issue_dev_token(member_id=MEMBER, role="student", verification_level=1, token_version=0, settings=dev)
    verified = await check(token, dev)
    assert verified.claims.sub == str(MEMBER) and verified.is_dev is True
    # Same token, server running with DEV_AUTH off (e.g. production): rejected.
    with pytest.raises(AuthError, match="disabled"):
        await check(token, settings(idp))


async def test_dev_token_with_wrong_secret_is_rejected(idp):
    dev = settings(idp, dev_auth=True, dev_auth_secret=DEV_SECRET)
    attacker = settings(idp, dev_auth=True, dev_auth_secret="a" * 40)
    token, _ = issue_dev_token(member_id=MEMBER, role="student", verification_level=4, token_version=0,
                               settings=attacker)
    with pytest.raises(AuthError):
        await check(token, dev)


def test_dev_token_endpoint_absent_without_dev_auth(monkeypatch):
    monkeypatch.setenv("DEV_AUTH", "false")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.post("/auth/dev/token", json={"role": "student"}).status_code == 404
    finally:
        get_settings.cache_clear()


def test_missing_token_is_401_with_bearer_challenge(client):
    r = client.get("/auth/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
