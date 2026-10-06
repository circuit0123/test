"""Hits the real docker compose services. Skipped if they are not running."""

import socket

import pytest

pytestmark = pytest.mark.integration


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(autouse=True)
def _require_services():
    missing = [name for name, port in [("postgres", 5432), ("neo4j", 7687), ("redis", 6379)] if not _port_open(port)]
    if missing:
        pytest.skip(f"services not running: {', '.join(missing)} (run `docker compose up -d`)")


def test_all_dependencies_healthy(client):
    r = client.get("/health/deps")
    assert r.status_code == 200, r.text
    deps = r.json()["dependencies"]
    assert set(deps["postgres"]["details"]["extensions"]) == {"postgis", "vector"}
    assert deps["neo4j"]["ok"] and deps["redis"]["ok"]
