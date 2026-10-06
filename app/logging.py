"""Structured JSON logging (structlog) and the per-request logging middleware.

Rules (from the spec):
- one JSON line per request: request_id, method, path, status, duration_ms, user_id
- exceptions are logged with a stack trace and the request_id
- never log tokens, passwords, emails, phone numbers or names: IDs only.
  That is why we log the path but NOT the query string or any headers/bodies.
- logs are for debugging; product analytics go in the events table (Phase 5).
"""

import logging
import re
import sys
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
# Accept a caller-supplied request id only if it is short and harmless.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

log = structlog.get_logger("circuit.request")


def configure_logging(level: str = "INFO") -> None:
    """Send both structlog and stdlib logging (uvicorn, libraries) out as JSON lines."""
    shared = [
        structlog.contextvars.merge_contextvars,  # adds request_id etc. bound per request
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            # Stack traces as structured JSON. show_locals=False is essential: frame
            # locals include request headers, i.e. bearer tokens, which must never be logged.
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Our middleware writes the access log; silence uvicorn's duplicate one.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


class RequestLoggingMiddleware:
    """Pure ASGI middleware: times each request, tags it with an id, logs one line.

    Auth (Phase 2) records the caller by setting `request.state.user_id`; Starlette
    stores request.state in scope["state"], which is how we read it here.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status = 500
        response_started = False
        start = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            status = 500
            log.exception("unhandled_exception", method=scope["method"], path=scope["path"])
            if not response_started:
                await _send_500(send, request_id)
        finally:
            log.info(
                "request",
                method=scope["method"],
                path=scope["path"],  # path only: query strings may carry personal data
                status=status,
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
                user_id=scope["state"].get("user_id"),
            )
            structlog.contextvars.clear_contextvars()


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _SAFE_REQUEST_ID.match(candidate) else None
    return None


async def _send_500(send: Send, request_id: str) -> None:
    body = b'{"detail":"Internal Server Error","request_id":"' + request_id.encode() + b'"}'
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"x-request-id", request_id.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
