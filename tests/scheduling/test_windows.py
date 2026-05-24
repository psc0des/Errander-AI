"""Tests for maintenance window enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from errander.scheduling.windows import (
    MaintenanceWindow,
    check_window_from_config,
    is_within_window,
    next_window_open,
    window_start_cron,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


# 2026-04-06 is a Monday (UTC)
MON_02H = _utc(2026, 4, 6, 2)   # Monday 02:00 UTC
MON_05H = _utc(2026, 4, 6, 5)   # Monday 05:00 UTC
MON_10H = _utc(2026, 4, 6, 10)  # Monday 10:00 UTC
TUE_03H = _utc(2026, 4, 7, 3)   # Tuesday 03:00 UTC
SAT_12H = _utc(2026, 4, 11, 12) # Saturday 12:00 UTC
SUN_04H = _utc(2026, 4, 12, 4)  # Sunday 04:00 UTC


# ---------------------------------------------------------------------------
# is_within_window — basic cases
# ---------------------------------------------------------------------------

class TestIsWithinWindow:
    def test_inside_window(self) -> None:
        assert is_within_window(_utc(2026, 4, 6, 3), ["monday"], 2, 6, "UTC") is True

    def test_at_start_boundary(self) -> None:
        assert is_within_window(MON_02H, ["monday"], 2, 6, "UTC") is True

    def test_at_end_boundary_exclusive(self) -> None:
        # end_hour is exclusive — hour=6 is NOT in window [2, 6)
        assert is_within_window(_utc(2026, 4, 6, 6), ["monday"], 2, 6, "UTC") is False

    def test_before_start(self) -> None:
        assert is_within_window(MON_02H, ["monday"], 3, 6, "UTC") is False

    def test_after_end(self) -> None:
        assert is_within_window(MON_10H, ["monday"], 2, 6, "UTC") is False

    def test_wrong_day(self) -> None:
        # Tuesday — window only allows monday
        assert is_within_window(TUE_03H, ["monday"], 2, 6, "UTC") is False

    def test_multiple_days_match(self) -> None:
        assert is_within_window(
            TUE_03H, ["monday", "tuesday", "wednesday"], 2, 6, "UTC"
        ) is True

    def test_weekday_only_rejects_weekend(self) -> None:
        weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday"]
        assert is_within_window(SAT_12H, weekdays, 0, 24, "UTC") is False

    def test_weekend_window(self) -> None:
        assert is_within_window(SAT_12H, ["saturday", "sunday"], 10, 14, "UTC") is True

    def test_timezone_conversion(self) -> None:
        # 02:00 UTC = 03:00 CET (UTC+1 in April)
        # Window: tuesday 03:00–07:00 CET
        # 2026-04-07 02:00 UTC = tuesday 03:00 CET → inside
        assert is_within_window(TUE_03H, ["tuesday"], 3, 7, "Europe/Paris") is True

    def test_timezone_conversion_outside(self) -> None:
        # 01:00 UTC = 03:00 CEST (UTC+2 in April), window is 04:00–06:00 CEST → outside
        tue_01h_utc = _utc(2026, 4, 7, 1)
        assert is_within_window(tue_01h_utc, ["tuesday"], 4, 6, "Europe/Paris") is False


# ---------------------------------------------------------------------------
# is_within_window — overnight windows
# ---------------------------------------------------------------------------

class TestOvernightWindow:
    def test_overnight_in_first_half(self) -> None:
        # Window 23:00–03:00, time is 23:30 → inside
        late = _utc(2026, 4, 6, 23)
        assert is_within_window(late, ["monday"], 23, 3, "UTC") is True

    def test_overnight_in_second_half(self) -> None:
        # Window 23:00–03:00, time is 01:00 → inside
        early = _utc(2026, 4, 7, 1)  # Tuesday 01:00
        assert is_within_window(early, ["monday", "tuesday"], 23, 3, "UTC") is True

    def test_overnight_outside_gap(self) -> None:
        # Window 23:00–03:00, time is 12:00 → outside
        mid = _utc(2026, 4, 6, 12)
        assert is_within_window(mid, ["monday"], 23, 3, "UTC") is False

    def test_zero_length_window_never_matches(self) -> None:
        # start_hour == end_hour → zero-length window
        assert is_within_window(MON_02H, ["monday"], 2, 2, "UTC") is False


# ---------------------------------------------------------------------------
# is_within_window — error handling
# ---------------------------------------------------------------------------

class TestWindowErrors:
    def test_unknown_timezone_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown timezone"):
            is_within_window(MON_02H, ["monday"], 2, 6, "Not/A/Timezone")

    def test_unknown_day_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown day"):
            is_within_window(MON_02H, ["moonday"], 2, 6, "UTC")


# ---------------------------------------------------------------------------
# MaintenanceWindow dataclass
# ---------------------------------------------------------------------------

class TestMaintenanceWindowDataclass:
    def test_valid_construction(self) -> None:
        w = MaintenanceWindow(
            days=["monday", "friday"],
            start_hour=2,
            end_hour=6,
            timezone="UTC",
        )
        assert w.start_hour == 2

    def test_invalid_day_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown day"):
            MaintenanceWindow(days=["funday"], start_hour=2, end_hour=6, timezone="UTC")

    def test_invalid_start_hour_raises(self) -> None:
        with pytest.raises(ValueError, match="start_hour"):
            MaintenanceWindow(days=["monday"], start_hour=25, end_hour=6, timezone="UTC")

    def test_invalid_end_hour_raises(self) -> None:
        with pytest.raises(ValueError, match="end_hour"):
            MaintenanceWindow(days=["monday"], start_hour=2, end_hour=99, timezone="UTC")

    def test_invalid_timezone_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown timezone"):
            MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="Fake/Zone")


# ---------------------------------------------------------------------------
# check_window_from_config
# ---------------------------------------------------------------------------

class TestCheckWindowFromConfig:
    def test_inside_window(self) -> None:
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="UTC")
        assert check_window_from_config(MON_05H, w) is True

    def test_outside_window(self) -> None:
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=4, timezone="UTC")
        assert check_window_from_config(MON_05H, w) is False

    def test_wrong_day(self) -> None:
        w = MaintenanceWindow(days=["tuesday"], start_hour=2, end_hour=6, timezone="UTC")
        assert check_window_from_config(MON_05H, w) is False


# ---------------------------------------------------------------------------
# next_window_open
# ---------------------------------------------------------------------------

class TestNextWindowOpen:
    # Reference: 2026-04-06 is a Monday

    def test_returns_future_start_when_before_window_today(self) -> None:
        """Currently before window on a valid day → returns today at start_hour."""
        # Monday 00:00, window Mon 02:00-06:00 → next open = Monday 02:00
        now = _utc(2026, 4, 6, 0)
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="UTC")
        result = next_window_open(now, w)
        expected = _utc(2026, 4, 6, 2)
        assert result == expected

    def test_skips_past_start_to_next_occurrence(self) -> None:
        """Now is inside window (start_hour already passed) → next occurrence next week."""
        # Monday 03:00 inside [02:00, 06:00) — window already open, skip to next Monday
        now = _utc(2026, 4, 6, 3)
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="UTC")
        result = next_window_open(now, w)
        expected = _utc(2026, 4, 13, 2)  # next Monday 02:00
        assert result == expected

    def test_skips_to_next_valid_day(self) -> None:
        """On a non-window day → skip to next allowed day."""
        # Tuesday 10:00, window Sat-Sun 23:00 → next Saturday
        now = _utc(2026, 4, 7, 10)   # Tuesday
        w = MaintenanceWindow(days=["saturday", "sunday"], start_hour=23, end_hour=3, timezone="UTC")
        result = next_window_open(now, w)
        expected = _utc(2026, 4, 11, 23)  # Saturday 23:00
        assert result == expected

    def test_result_is_utc(self) -> None:
        """Return value is always UTC."""
        now = _utc(2026, 4, 6, 0)
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="UTC")
        result = next_window_open(now, w)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_at_exact_start_hour_skips_current(self) -> None:
        """Called at exactly start_hour:00 (window just opened) → skip current, return next."""
        now = _utc(2026, 4, 6, 2)  # Monday 02:00 exactly
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="UTC")
        result = next_window_open(now, w)
        expected = _utc(2026, 4, 13, 2)  # next Monday
        assert result == expected


# ---------------------------------------------------------------------------
# window_start_cron
# ---------------------------------------------------------------------------

class TestWindowStartCron:
    def test_single_day(self) -> None:
        w = MaintenanceWindow(days=["monday"], start_hour=2, end_hour=6, timezone="UTC")
        assert window_start_cron(w) == "0 2 * * mon"

    def test_multiple_days(self) -> None:
        w = MaintenanceWindow(days=["tuesday", "thursday"], start_hour=23, end_hour=3, timezone="UTC")
        assert window_start_cron(w) == "0 23 * * tue,thu"

    def test_all_seven_days_produces_star(self) -> None:
        all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        w = MaintenanceWindow(days=all_days, start_hour=4, end_hour=6, timezone="UTC")
        assert window_start_cron(w) == "0 4 * * *"

    def test_weekend(self) -> None:
        w = MaintenanceWindow(days=["saturday", "sunday"], start_hour=10, end_hour=14, timezone="UTC")
        assert window_start_cron(w) == "0 10 * * sat,sun"
