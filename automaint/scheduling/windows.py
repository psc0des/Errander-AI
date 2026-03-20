"""Maintenance window enforcement.

The agent refuses to run maintenance outside defined windows unless
explicitly overridden with --force (which requires a mandatory reason).

Windows are defined in configuration as day-of-week + hour ranges
in a specified timezone.
"""

from __future__ import annotations

from datetime import datetime


def is_within_window(
    now: datetime,
    days: list[str],
    start_hour: int,
    end_hour: int,
    timezone: str,
) -> bool:
    """Check if the given time falls within the maintenance window.

    Args:
        now: Current time.
        days: Allowed days of the week (e.g., ["monday", "wednesday"]).
        start_hour: Window start hour (0-23).
        end_hour: Window end hour (0-23).
        timezone: Timezone name for the window.

    Returns:
        True if within the maintenance window.
    """
    raise NotImplementedError("Window check not yet implemented")
