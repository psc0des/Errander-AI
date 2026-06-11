"""APScheduler setup for periodic maintenance runs.

The agent owns its own schedule. APScheduler drives batch maintenance
runs at configured cron intervals within maintenance windows.

Design:
- One `AsyncIOScheduler` per agent process.
- Jobs are registered by the caller (typically main.py) via `add_maintenance_job()`.
- The scheduler does NOT enforce maintenance windows — the batch graph's
  `validate_window` node does that. The scheduler just triggers the run;
  the graph decides whether to proceed.
- Misfire grace: 600s — if the agent was down at trigger time, run within
  10 minutes of recovery rather than skipping silently.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

#: Grace period before a misfired job is skipped (seconds).
_MISFIRE_GRACE_SECONDS = 600


class MaintenanceScheduler:
    """Wrapper around APScheduler AsyncIOScheduler for maintenance jobs.

    Usage:
        scheduler = MaintenanceScheduler()
        scheduler.add_maintenance_job(run_batch, "0 2 * * mon-fri", job_id="prod")
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._started = False

    def add_maintenance_job(
        self,
        func: Callable[..., Awaitable[Any]],
        cron_expr: str,
        job_id: str,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Register a maintenance job on a cron schedule.

        Args:
            func: Async callable to invoke at each trigger.
            cron_expr: Standard 5-field cron expression (e.g., "0 2 * * mon-fri").
            job_id: Unique job identifier. Duplicate IDs replace existing jobs.
            kwargs: Keyword arguments passed to `func` at each invocation.

        Raises:
            ValueError: If the cron expression is invalid.
        """
        trigger = CronTrigger.from_crontab(cron_expr)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            misfire_grace_time=_MISFIRE_GRACE_SECONDS,
            coalesce=True,  # collapse multiple misfires into one run
        )
        logger.info("Registered maintenance job id=%s cron=%r", job_id, cron_expr)

    def add_interval_job(
        self,
        func: Callable[..., Awaitable[Any]],
        seconds: int,
        job_id: str,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Register a job on a fixed interval (e.g. the approval reconciler).

        Args:
            func: Async callable to invoke at each trigger.
            seconds: Interval between invocations.
            job_id: Unique job identifier. Duplicate IDs replace existing jobs.
            kwargs: Keyword arguments passed to `func` at each invocation.
        """
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=seconds),
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            misfire_grace_time=_MISFIRE_GRACE_SECONDS,
            coalesce=True,
            max_instances=1,  # a slow pass must never overlap the next tick
        )
        logger.info("Registered interval job id=%s every %ds", job_id, seconds)

    def list_jobs(self) -> list[dict[str, str]]:
        """Return a summary of registered jobs.

        Returns:
            List of dicts with 'id', 'next_run', and 'cron' keys.
        """
        result = []
        for job in self._scheduler.get_jobs():
            next_run_time = getattr(job, "next_run_time", None)
            result.append({
                "id": job.id,
                "next_run": str(next_run_time) if next_run_time else "pending",
                "trigger": str(job.trigger),
            })
        return result

    async def start(self) -> None:
        """Start the scheduler background thread.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._started:
            logger.debug("Scheduler already started — ignoring duplicate start()")
            return
        self._scheduler.start()
        self._started = True
        logger.info("MaintenanceScheduler started (%d jobs)", len(self._scheduler.get_jobs()))

    async def stop(self) -> None:
        """Gracefully shut down the scheduler.

        Waits for any running jobs to complete (wait=True) before returning.
        """
        if not self._started:
            return
        self._scheduler.shutdown(wait=True)
        self._started = False
        logger.info("MaintenanceScheduler stopped")
