"""Request logging middleware: one JSON line per request, request ids, exceptions."""

import logging

from fastapi.testclient import TestClient


def test_logs_one_line_per_request_with_required_fields(client, request_logs):
    r = client.get("/health?email=someone@example.com")
    lines = request_logs()
    assert len(lines) == 1
    line = lines[0]
    assert line["method"] == "GET"
    assert line["path"] == "/health"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert line["user_id"] is None
    assert line["request_id"] == r.headers["x-request-id"]
    # The query string must never reach the logs.
    assert "example.com" not in str(line)


def test_reuses_safe_incoming_request_id(client):
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


def test_replaces_unsafe_incoming_request_id(client):
    r = client.get("/health", headers={"X-Request-ID": "bad id with spaces\nand newline"})
    assert r.headers["x-request-id"] != "bad id with spaces\nand newline"
    assert len(r.headers["x-request-id"]) == 32


def test_logs_user_id_set_by_auth(app, client, request_logs):
    from fastapi import Request

    @app.get("/whoami-test")
    async def whoami(request: Request):
        request.state.user_id = "member-42"  # what the Phase 2 auth dependency will do
        return {}

    client.get("/whoami-test")
    assert request_logs()[0]["user_id"] == "member-42"


def test_unhandled_exception_logged_with_trace_and_request_id(app, caplog, request_logs):
    caplog.set_level(logging.INFO)

    @app.get("/boom-test")
    async def boom():
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/boom-test")

    assert r.status_code == 500
    request_id = r.headers["x-request-id"]
    assert r.json() == {"detail": "Internal Server Error", "request_id": request_id}

    errors = [rec for rec in caplog.records if isinstance(rec.msg, dict) and rec.msg.get("event") == "unhandled_exception"]
    assert len(errors) == 1
    assert errors[0].msg["request_id"] == request_id
    assert errors[0].exc_info is not None  # stack trace attached
    assert request_logs()[0]["status"] == 500
