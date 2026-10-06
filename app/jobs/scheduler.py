"""APScheduler setup: background jobs that run inside the API process.

AsyncIOScheduler runs jobs on the same event loop as FastAPI, so a job can use
the same async database engine and Redis client as the API.

Note: if you ever run several API processes, enable the scheduler in only one of
them (SCHEDULER_ENABLED=false on the others), or every process will run the jobs.
"""

from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.jobs.matching import compute_all_matches
from app.resources import Resources


def create_scheduler(resources: Resources, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        compute_all_matches,
        "interval",
        hours=settings.matching.job_interval_hours,
        args=[resources],
        id="compute_all_matches",
        max_instances=1,  # never run two at once
        coalesce=True,  # if runs were missed (e.g. laptop asleep), run once, not many times
        next_run_time=datetime.now(UTC) if settings.match_job_on_startup else None,
    )
    return scheduler
