"""Health endpoints, with fake dependency checks (no services needed)."""

import asyncio

from app.api.health import get_dependency_checks


def _fake_checks(**outcomes):
    """Build checks: True -> healthy, an exception -> raises it, 'hang' -> sleeps."""

    def make(outcome):
        async def check():
            if outcome is True:
                return None
            if outcome == "hang":
                await asyncio.sleep(10)
            raise outcome

        return check

    return lambda: {name: make(o) for name, o in outcomes.items()}


def test_health_is_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_deps_all_healthy(app, client):
    app.dependency_overrides[get_dependency_checks] = _fake_checks(postgres=True, neo4j=True, redis=True)
    r = client.get("/health/deps")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"postgres", "neo4j", "redis"}
    assert all(d["ok"] for d in body["dependencies"].values())


def test_deps_one_down_returns_503_without_leaking_details(app, client):
    app.dependency_overrides[get_dependency_checks] = _fake_checks(
        postgres=True, neo4j=ConnectionError("bolt://secret-host:7687 refused"), redis=True
    )
    r = client.get("/health/deps")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["neo4j"] == {
        "ok": False, "latency_ms": body["dependencies"]["neo4j"]["latency_ms"],
        "error": "ConnectionError", "details": None,
    }
    assert "secret-host" not in r.text


def test_deps_hanging_service_times_out(app, client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "health_check_timeout_s", 0.05)
    app.dependency_overrides[get_dependency_checks] = _fake_checks(postgres="hang", neo4j=True, redis=True)
    r = client.get("/health/deps")
    assert r.status_code == 503
    assert r.json()["dependencies"]["postgres"]["error"] == "timeout"
