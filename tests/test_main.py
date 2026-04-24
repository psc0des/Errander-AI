"""Tests for main.py — CLI parsing and helper functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.config.schema import EnvironmentSchema, TargetSchema
from errander.main import _build_maintenance_window, _parse_args


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self) -> None:
        args = _parse_args([])
        assert args.config == Path("settings.yaml")
        assert args.inventory == Path("inventory.yaml")
        assert args.run_now is False
        assert args.dry_run is True
        assert args.live is False
        assert args.force is False
        assert args.force_reason == ""

    def test_run_now_with_env(self) -> None:
        args = _parse_args(["--run-now", "--env", "production"])
        assert args.run_now is True
        assert args.env == "production"

    def test_live_flag(self) -> None:
        args = _parse_args(["--live"])
        assert args.live is True

    def test_force_with_reason(self) -> None:
        args = _parse_args(["--force", "--force-reason", "emergency patch"])
        assert args.force is True
        assert args.force_reason == "emergency patch"

    def test_custom_config_and_inventory(self) -> None:
        args = _parse_args(["--config", "prod/settings.yaml", "--inventory", "prod/inventory.yaml"])
        assert args.config == Path("prod/settings.yaml")
        assert args.inventory == Path("prod/inventory.yaml")

    def test_log_level(self) -> None:
        args = _parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# _build_maintenance_window
# ---------------------------------------------------------------------------

def _make_env(
    window: str | None = "02:00-06:00",
    days: list[str] | None = None,
    timezone: str = "UTC",
) -> EnvironmentSchema:
    target = TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")
    return EnvironmentSchema(
        maintenance_window=window,
        maintenance_days=days if days is not None else ["tuesday", "thursday"],
        maintenance_timezone=timezone,
        targets=[target],
    )


class TestBuildMaintenanceWindow:
    def test_returns_window_when_configured(self) -> None:
        env = _make_env(window="02:00-06:00", days=["monday", "tuesday"])
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.start_hour == 2
        assert w.end_hour == 6
        assert w.days == ["monday", "tuesday"]
        assert w.timezone == "UTC"

    def test_returns_none_when_no_window(self) -> None:
        env = _make_env(window=None)
        assert _build_maintenance_window(env) is None

    def test_returns_none_when_no_days(self) -> None:
        env = _make_env(days=[])
        assert _build_maintenance_window(env) is None

    def test_overnight_window(self) -> None:
        env = _make_env(window="23:00-03:00", days=["saturday", "sunday"])
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.start_hour == 23
        assert w.end_hour == 3

    def test_custom_timezone(self) -> None:
        env = _make_env(window="02:00-06:00", timezone="Australia/Sydney")
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.timezone == "Australia/Sydney"

    def test_returns_none_on_malformed_window_string(self) -> None:
        env = _make_env(window="not-a-window")
        # Should not raise — returns None
        result = _build_maintenance_window(env)
        assert result is None

    def test_parses_hhmm_format(self) -> None:
        env = _make_env(window="02:30-06:45")  # minutes are ignored, only hours used
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.start_hour == 2
        assert w.end_hour == 6


# ---------------------------------------------------------------------------
# async_main — --run-now mode
# ---------------------------------------------------------------------------

class TestAsyncMainRunNow:
    @pytest.mark.asyncio
    async def test_missing_env_flag_returns_error(self, tmp_path: Path) -> None:
        """--run-now without --env should return exit code 1."""
        from errander.main import async_main

        # Create a minimal inventory
        inventory_file = tmp_path / "inventory.yaml"
        inventory_file.write_text(
            "environments:\n"
            "  dev:\n"
            "    targets:\n"
            "      - host: 10.0.1.1\n"
            "        name: web-01\n"
            "        os_family: ubuntu\n"
        )

        args = _parse_args([
            "--run-now",
            "--inventory", str(inventory_file),
            "--config", str(tmp_path / "nonexistent.yaml"),
        ])
        # --env not provided
        args.env = None

        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_unknown_env_returns_error(self, tmp_path: Path) -> None:
        """--run-now with unknown env name should return exit code 1."""
        from errander.main import async_main

        inventory_file = tmp_path / "inventory.yaml"
        inventory_file.write_text(
            "environments:\n"
            "  dev:\n"
            "    targets:\n"
            "      - host: 10.0.1.1\n"
            "        name: web-01\n"
            "        os_family: ubuntu\n"
        )

        args = _parse_args([
            "--run-now", "--env", "nonexistent",
            "--inventory", str(inventory_file),
            "--config", str(tmp_path / "nonexistent.yaml"),
        ])

        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_missing_inventory_returns_error(self, tmp_path: Path) -> None:
        """Missing inventory file should return exit code 1."""
        from errander.main import async_main

        args = _parse_args([
            "--run-now", "--env", "dev",
            "--inventory", str(tmp_path / "missing.yaml"),
        ])

        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_force_without_reason_returns_error(self, tmp_path: Path) -> None:
        """--force without --force-reason should return exit code 1."""
        from errander.main import async_main

        inventory_file = tmp_path / "inventory.yaml"
        inventory_file.write_text(
            "environments:\n"
            "  dev:\n"
            "    targets:\n"
            "      - host: 10.0.1.1\n"
            "        name: web-01\n"
            "        os_family: ubuntu\n"
        )

        args = _parse_args([
            "--run-now", "--env", "dev",
            "--inventory", str(inventory_file),
            "--force",
        ])

        result = await async_main(args)
        assert result == 1
