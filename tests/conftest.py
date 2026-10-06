import logging
import os

# Never start background jobs during tests (set before the app reads settings).
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI):
    # Using TestClient as a context manager runs startup/shutdown (the lifespan).
    with TestClient(app) as c:
        yield c


@pytest.fixture
def request_logs(caplog):
    """Structured 'request' log lines emitted by the middleware, as dicts."""
    caplog.set_level(logging.INFO)

    def _get() -> list[dict]:
        return [
            r.msg for r in caplog.records
            if isinstance(r.msg, dict) and r.msg.get("event") == "request"
        ]

    return _get
