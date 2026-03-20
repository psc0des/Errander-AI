"""APScheduler setup for periodic maintenance runs.

The agent owns its own schedule. APScheduler triggers batch maintenance
runs at configured intervals within maintenance windows.

Features:
- Cron-style scheduling for maintenance runs
- Persistent job store (survives agent restart)
- Misfire grace time for delayed execution
"""

from __future__ import annotations


async def start_scheduler() -> None:
    """Initialize and start APScheduler with configured maintenance jobs.

    Jobs are loaded from configuration. The scheduler runs as a background
    task within the agent process.
    """
    raise NotImplementedError("Scheduler not yet implemented")


async def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    raise NotImplementedError("Scheduler stop not yet implemented")
