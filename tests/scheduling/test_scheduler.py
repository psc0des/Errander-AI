"""Tests for MaintenanceScheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.scheduling.scheduler import MaintenanceScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler() -> MaintenanceScheduler:
    return MaintenanceScheduler()


# ---------------------------------------------------------------------------
# add_maintenance_job
# ---------------------------------------------------------------------------

class TestAddMaintenanceJob:
    def test_registers_job(self) -> None:
        sched = _make_scheduler()
        func = AsyncMock()
        sched.add_maintenance_job(func, "0 2 * * mon-fri", job_id="prod-nightly")
        jobs = sched.list_jobs()
        assert any(j["id"] == "prod-nightly" for j in jobs)

    def test_registers_multiple_jobs(self) -> None:
        sched = _make_scheduler()
        sched.add_maintenance_job(AsyncMock(), "0 2 * * mon-fri", job_id="job-a")
        sched.add_maintenance_job(AsyncMock(), "0 3 * * sat", job_id="job-b")
        ids = [j["id"] for j in sched.list_jobs()]
        assert "job-a" in ids
        assert "job-b" in ids

    def test_invalid_cron_raises(self) -> None:
        sched = _make_scheduler()
        with pytest.raises(Exception):  # noqa: B017
            sched.add_maintenance_job(AsyncMock(), "not-a-cron", job_id="bad")

    def test_kwargs_accepted(self) -> None:
        sched = _make_scheduler()
        func = AsyncMock()
        # Should not raise — kwargs stored for invocation
        sched.add_maintenance_job(func, "0 2 * * *", job_id="kwargs-job", kwargs={"env": "prod"})
        jobs = sched.list_jobs()
        assert any(j["id"] == "kwargs-job" for j in jobs)


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_empty_when_no_jobs(self) -> None:
        sched = _make_scheduler()
        assert sched.list_jobs() == []

    def test_returns_id_and_trigger(self) -> None:
        sched = _make_scheduler()
        sched.add_maintenance_job(AsyncMock(), "0 2 * * *", job_id="check-fields")
        jobs = sched.list_jobs()
        assert len(jobs) == 1
        assert "id" in jobs[0]
        assert "next_run" in jobs[0]
        assert "trigger" in jobs[0]


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_start_starts_apscheduler(self) -> None:
        sched = _make_scheduler()
        with patch.object(sched._scheduler, "start") as mock_start:
            await sched.start()
        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self) -> None:
        sched = _make_scheduler()
        with patch.object(sched._scheduler, "start") as mock_start:
            await sched.start()
            await sched.start()  # second call should be ignored
        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_shuts_down_apscheduler(self) -> None:
        sched = _make_scheduler()
        with patch.object(sched._scheduler, "start"):
            await sched.start()
        with patch.object(sched._scheduler, "shutdown") as mock_shutdown:
            await sched.stop()
        mock_shutdown.assert_called_once_with(wait=True)

    @pytest.mark.asyncio
    async def test_stop_before_start_is_noop(self) -> None:
        sched = _make_scheduler()
        with patch.object(sched._scheduler, "shutdown") as mock_shutdown:
            await sched.stop()  # should not raise
        mock_shutdown.assert_not_called()

    @pytest.mark.asyncio
    async def test_started_flag_resets_after_stop(self) -> None:
        sched = _make_scheduler()
        with patch.object(sched._scheduler, "start"), \
             patch.object(sched._scheduler, "shutdown"):
            await sched.start()
            assert sched._started is True
            await sched.stop()
            assert sched._started is False
