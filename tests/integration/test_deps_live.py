"""Hits the real docker compose services."""

import pytest

pytestmark = pytest.mark.integration


def test_all_dependencies_healthy(client):
    r = client.get("/health/deps")
    assert r.status_code == 200, r.text
    deps = r.json()["dependencies"]
    assert set(deps["postgres"]["details"]["extensions"]) == {"postgis", "vector"}
    assert deps["neo4j"]["ok"] and deps["redis"]["ok"]
