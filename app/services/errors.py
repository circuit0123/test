"""Errors raised by services. main.py turns each into an HTTP response.

Services raise these instead of HTTPException so business logic doesn't depend
on the web framework (the background jobs call the same services).
"""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(ServiceError):
    status_code = 404


class Forbidden(ServiceError):
    status_code = 403


class Conflict(ServiceError):
    status_code = 409


class Invalid(ServiceError):
    status_code = 422


class TooMany(ServiceError):
    """A rate limit was hit (HTTP 429 Too Many Requests)."""

    status_code = 429
