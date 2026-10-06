"""A fake identity provider for tests: an RSA key pair and a JWKS file.

It serves its JWKS over real HTTP from a tiny local server (Python's built-in
http.server), so the production JWKS code path runs unchanged.
"""

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://issuer.test"
AUDIENCE = "circuit-api"
KID = "test-key-1"


class FakeIdP:
    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
        body = json.dumps({"keys": [jwk]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (name required by http.server)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep test output quiet
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # port 0 = any free port
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def jwks_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/.well-known/jwks.json"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def token(self, member_id: uuid.UUID | str, *, role: str = "founder", verification_level: int = 2,
              token_version: int = 0, exp_in: int = 3600, kid: str = KID, drop: tuple[str, ...] = (),
              **overrides) -> str:
        claims = {
            "sub": str(member_id), "role": role, "verification_level": verification_level,
            "token_version": token_version, "iss": ISSUER, "aud": AUDIENCE,
            "iat": int(time.time()), "exp": int(time.time()) + exp_in, **overrides,
        }
        for key in drop:
            claims.pop(key)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": kid})
