"""The match computation job. Also runnable by hand:

    uv run python -m app.jobs.matching
"""

import asyncio

import structlog

from app.config import get_settings
from app.logging import configure_logging
from app.resources import Resources
from app.schemas.matches import RecomputeResult
from app.services.matching import recompute_all

log = structlog.get_logger("circuit.jobs")


async def compute_all_matches(resources: Resources) -> RecomputeResult | None:
    try:
        return await recompute_all(resources.sessionmaker, resources.redis, resources.settings.matching)
    except Exception:
        # A failed run must not kill the scheduler; the next run will try again.
        log.exception("compute_all_matches_failed")
        return None


async def _main() -> None:
    resources = Resources.from_settings(get_settings())
    try:
        result = await compute_all_matches(resources)
        print(result.model_dump_json(indent=2) if result else "failed (see logs)")
    finally:
        await resources.close()


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    asyncio.run(_main())
