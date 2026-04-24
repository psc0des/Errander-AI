"""Maintenance window enforcement.

The agent refuses to run maintenance outside defined windows unless
explicitly overridden with --force (which requires a mandatory reason).

Windows are defined as day-of-week lists + hour ranges in a named timezone.
All timezone lookups use the standard IANA tz database via `zoneinfo`.

Examples:
    Weekday nights (UTC):
        days=["monday","tuesday","wednesday","thursday","friday"]
        start_hour=2, end_hour=6, timezone="UTC"

    Weekend maintenance (Sydney):
        days=["saturday","sunday"]
        start_hour=10, end_hour=14, timezone="Australia/Sydney"

    Overnight window (23:00 → 03:00):
        start_hour=23, end_hour=3  — handled via overnight detection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: Canonical lowercase day names in calendar order.
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class MaintenanceWindow:
    """A configured maintenance window.

    Attributes:
        days: Allowed days of week, lowercase (e.g., ["monday", "wednesday"]).
        start_hour: Window open hour in [0, 23].
        end_hour: Window close hour in [0, 23]. May be less than start_hour
            for overnight windows (e.g., start=23, end=3).
        timezone: IANA timezone name (e.g., "UTC", "Europe/London").
    """

    days: list[str]
    start_hour: int
    end_hour: int
    timezone: str

    def __post_init__(self) -> None:
        """Validate fields at construction time."""
        invalid_days = [d for d in self.days if d.lower() not in _DAYS]
        if invalid_days:
            msg = f"Unknown day names: {invalid_days}. Must be lowercase weekday names."
            raise ValueError(msg)
        if not 0 <= self.start_hour <= 23:
            msg = f"start_hour must be 0–23, got {self.start_hour}"
            raise ValueError(msg)
        if not 0 <= self.end_hour <= 23:
            msg = f"end_hour must be 0–23, got {self.end_hour}"
            raise ValueError(msg)
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            msg = f"Unknown timezone: '{self.timezone}'"
            raise ValueError(msg) from None


def is_within_window(
    now: datetime,
    days: list[str],
    start_hour: int,
    end_hour: int,
    timezone: str,
) -> bool:
    """Check if the given time falls within the maintenance window.

    Handles overnight windows (start_hour > end_hour).

    Args:
        now: Current time. Timezone-aware or naive (naive treated as UTC).
        days: Allowed days of the week, lowercase (e.g., ["monday", "friday"]).
        start_hour: Window start hour (0-23, inclusive).
        end_hour: Window end hour (0-23, exclusive). Use 0 for midnight.
        timezone: IANA timezone name for the window.

    Returns:
        True if `now` falls within the maintenance window.

    Raises:
        ValueError: If timezone is unknown or day names are invalid.
    """
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        msg = f"Unknown timezone: '{timezone}'"
        raise ValueError(msg) from None

    local_now = now.astimezone(tz)
    day_name = local_now.strftime("%A").lower()  # "monday", "tuesday", etc.
    hour = local_now.hour

    normalized_days = [d.lower() for d in days]
    unknown = [d for d in normalized_days if d not in _DAYS]
    if unknown:
        msg = f"Unknown day names: {unknown}"
        raise ValueError(msg)

    if day_name not in normalized_days:
        logger.debug("Window check: day %s not in allowed days %s", day_name, normalized_days)
        return False

    # Normal window: start <= hour < end  (e.g., 02:00–06:00)
    # Overnight window: hour >= start OR hour < end (e.g., 23:00–03:00)
    if start_hour < end_hour:
        in_hours = start_hour <= hour < end_hour
    elif start_hour > end_hour:
        # Overnight: e.g., start=23, end=3 → hour in [23,24) or [0,3)
        in_hours = hour >= start_hour or hour < end_hour
    else:
        # start_hour == end_hour: zero-length window — never in window
        in_hours = False

    logger.debug(
        "Window check: %s %02d:xx — days_ok=%s hours_ok=%s (window=%02d:00–%02d:00 %s)",
        day_name, hour, day_name in normalized_days, in_hours,
        start_hour, end_hour, timezone,
    )
    return in_hours


def check_window_from_config(now: datetime, window: MaintenanceWindow) -> bool:
    """Convenience wrapper for checking a MaintenanceWindow dataclass.

    Args:
        now: Current time.
        window: Configured maintenance window.

    Returns:
        True if `now` falls within the window.
    """
    return is_within_window(
        now=now,
        days=window.days,
        start_hour=window.start_hour,
        end_hour=window.end_hour,
        timezone=window.timezone,
    )
