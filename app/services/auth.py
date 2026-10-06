"""JWT verification (production: JWKS) and test-token issuing (local: DEV_AUTH).

A JWT is a signed JSON blob: header.payload.signature. The identity provider signs
it with its private key; we check the signature with the matching public key,
which the provider publishes at a JWKS URL ("JSON Web Key Set"). If the signature
checks out and it hasn't expired, we can trust the claims inside.

Two paths, chosen by the token's header:
- RS256/ES256... -> verified against JWKS_URL (real provider).
- HS256 with issuer "circuit-dev" -> verified with DEV_AUTH_SECRET, only when
  DEV_AUTH is on. With DEV_AUTH off these tokens are always rejected.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import structlog
from pydantic import BaseModel, ValidationError

from app.config import Settings

log = structlog.get_logger("circuit.auth")

DEV_ISSUER = "circuit-dev"
DEV_ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "role", "verification_level", "token_version", "exp"]


class AuthError(Exception):
    """The token is missing, malformed, expired, or not signed by a trusted key."""


@dataclass(frozen=True)
class CurrentMember:
    """The authenticated caller, with role and level read fresh from the database."""

    id: uuid.UUID
    role: str
    verification_level: int

    @property
    def is_admin(self) -> bool:
        return self.role == "partner_admin"


class TokenClaims(BaseModel):
    sub: uuid.UUID  # the member id
    role: str
    verification_level: int
    token_version: int
    exp: int


def create_jwks_client(settings: Settings) -> jwt.PyJWKClient | None:
    if not settings.jwks_url:
        return None
    # Caches the provider's keys; refetches when it sees an unknown key id.
    return jwt.PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=settings.jwks_cache_seconds)


async def verify_token(token: str, settings: Settings, jwks_client: jwt.PyJWKClient | None) -> TokenClaims:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token") from exc

    try:
        if header.get("alg") == DEV_ALGORITHM:
            payload = _decode_dev(token, settings)
        else:
            payload = await _decode_jwks(token, settings, jwks_client)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.PyJWKClientConnectionError as exc:
        # The provider's key server is unreachable. We can't verify, so we refuse.
        log.warning("jwks_fetch_failed", error=str(exc))
        raise AuthError("could not fetch signing keys") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("invalid token") from exc

    try:
        return TokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise AuthError("invalid token claims") from exc


def _decode_dev(token: str, settings: Settings) -> dict:
    if not settings.dev_auth or settings.dev_auth_secret is None:
        raise AuthError("dev tokens are disabled")
    return jwt.decode(
        token,
        settings.dev_auth_secret.get_secret_value(),
        algorithms=[DEV_ALGORITHM],
        issuer=DEV_ISSUER,
        options={"require": REQUIRED_CLAIMS + ["iss"]},
    )


async def _decode_jwks(token: str, settings: Settings, jwks_client: jwt.PyJWKClient | None) -> dict:
    if jwks_client is None:
        raise AuthError("no JWKS_URL configured")
    # PyJWKClient fetches over the network with blocking I/O: run it in a thread
    # so one slow fetch doesn't stall every other request.
    signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=settings.jwt_algorithms,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={
            "require": REQUIRED_CLAIMS,
            "verify_iss": settings.jwt_issuer is not None,
            "verify_aud": settings.jwt_audience is not None,
        },
    )


def issue_dev_token(
    *, member_id: uuid.UUID, role: str, verification_level: int, token_version: int, settings: Settings
) -> tuple[str, datetime]:
    if not settings.dev_auth or settings.dev_auth_secret is None:
        raise RuntimeError("DEV_AUTH is disabled")
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.dev_token_ttl_minutes)
    claims = {
        "sub": str(member_id),
        "role": role,
        "verification_level": verification_level,
        "token_version": token_version,
        "iss": DEV_ISSUER,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(claims, settings.dev_auth_secret.get_secret_value(), algorithm=DEV_ALGORITHM)
    return token, expires_at
