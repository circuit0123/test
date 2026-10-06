from typing import Any, Literal

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class DependencyStatus(BaseModel):
    ok: bool
    latency_ms: float
    error: str | None = Field(None, description="Exception type or 'timeout' when the check failed.")
    details: dict[str, Any] | None = None


class DependencyHealth(BaseModel):
    status: Literal["ok", "degraded"]
    dependencies: dict[str, DependencyStatus]
