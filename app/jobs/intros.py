"""Hourly job: mark unanswered intro requests past their deadline as expired."""

import structlog

from app.resources import Resources
from app.services.intros import expire_stale

log = structlog.get_logger("circuit.jobs")


async def expire_intros(resources: Resources) -> int:
    try:
        async with resources.sessionmaker() as session:
            count = await expire_stale(session, resources.settings.intros)
        if count:
            log.info("intros_expired", count=count)
        return count
    except Exception:
        log.exception("expire_intros_failed")  # never kill the scheduler; retry next hour
        return 0
