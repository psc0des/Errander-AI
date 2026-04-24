"""Integration tests for AuditStore — strengthened API and graph pipeline.

These tests use SQLite :memory: and run real graph nodes (with mocked SSH)
to assert that the audit trail is correctly written end-to-end.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.models.events import AuditEvent, EventType
from errander.safety.audit import AuditStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_type: EventType = EventType.ACTION_STARTED,
    batch_id: str = "batch-001",
    vm_id: str | None = "dev/web-01",
    action_type: str | None = "disk_cleanup",
    detail: str = "Starting disk cleanup",
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        batch_id=batch_id,
        vm_id=vm_id,
        action_type=action_type,
        detail=detail,
        timestamp=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# AuditStore: action_type filter (new)
# ---------------------------------------------------------------------------

class TestGetEventsActionTypeFilter:
    async def test_filter_by_action_type(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(_make_event(action_type="disk_cleanup"))
            await store.log_event(_make_event(action_type="patching"))
            await store.log_event(_make_event(action_type="disk_cleanup"))

            events = await store.get_events(action_type="disk_cleanup")
            assert len(events) == 2
            assert all(e.action_type == "disk_cleanup" for e in events)

    async def test_action_type_filter_returns_empty_when_no_match(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(_make_event(action_type="disk_cleanup"))
            events = await store.get_events(action_type="patching")
            assert events == []

    async def test_action_type_combined_with_batch_id(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(_make_event(batch_id="A", action_type="disk_cleanup"))
            await store.log_event(_make_event(batch_id="A", action_type="patching"))
            await store.log_event(_make_event(batch_id="B", action_type="disk_cleanup"))

            events = await store.get_events(batch_id="A", action_type="disk_cleanup")
            assert len(events) == 1
            assert events[0].batch_id == "A"
            assert events[0].action_type == "disk_cleanup"

    async def test_action_type_combined_with_vm_id(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(_make_event(vm_id="dev/web-01", action_type="disk_cleanup"))
            await store.log_event(_make_event(vm_id="prod/db-01", action_type="disk_cleanup"))

            events = await store.get_events(vm_id="dev/web-01", action_type="disk_cleanup")
            assert len(events) == 1

    async def test_action_type_none_returns_all(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(_make_event(action_type="disk_cleanup"))
            await store.log_event(_make_event(action_type="patching"))
            await store.log_event(_make_event(action_type=None))

            events = await store.get_events(action_type=None)
            assert len(events) == 3

    async def test_all_four_filters_combined(self) -> None:
        async with AuditStore(":memory:") as store:
            # The one we want
            await store.log_event(_make_event(
                batch_id="B1",
                vm_id="prod/app-01",
                action_type="disk_cleanup",
                event_type=EventType.ACTION_COMPLETED,
            ))
            # Noise
            await store.log_event(_make_event(
                batch_id="B1",
                vm_id="prod/app-01",
                action_type="disk_cleanup",
                event_type=EventType.ACTION_STARTED,
            ))
            await store.log_event(_make_event(
                batch_id="B2",
                vm_id="prod/app-01",
                action_type="disk_cleanup",
                event_type=EventType.ACTION_COMPLETED,
            ))

            events = await store.get_events(
                batch_id="B1",
                vm_id="prod/app-01",
                action_type="disk_cleanup",
                event_type=EventType.ACTION_COMPLETED,
            )
            assert len(events) == 1
            assert events[0].event_type == EventType.ACTION_COMPLETED


# ---------------------------------------------------------------------------
# AuditStore: get_recent_batches (new)
# ---------------------------------------------------------------------------

class TestGetRecentBatches:
    async def test_returns_batches_most_recent_first(self) -> None:
        async with AuditStore(":memory:") as store:
            # Batch A: older
            await store.log_event(AuditEvent(
                event_type=EventType.BATCH_STARTED,
                batch_id="batch-A",
                detail="start",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ))
            # Batch B: newer
            await store.log_event(AuditEvent(
                event_type=EventType.BATCH_STARTED,
                batch_id="batch-B",
                detail="start",
                timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
            ))

            batches = await store.get_recent_batches(limit=10)
            assert len(batches) == 2
            assert batches[0]["batch_id"] == "batch-B"
            assert batches[1]["batch_id"] == "batch-A"

    async def test_event_count_per_batch(self) -> None:
        async with AuditStore(":memory:") as store:
            for _ in range(3):
                await store.log_event(_make_event(batch_id="batch-X"))
            for _ in range(5):
                await store.log_event(_make_event(batch_id="batch-Y"))

            batches = {b["batch_id"]: b for b in await store.get_recent_batches()}
            assert batches["batch-X"]["event_count"] == 3
            assert batches["batch-Y"]["event_count"] == 5

    async def test_vm_ids_collected(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(_make_event(batch_id="B", vm_id="dev/web-01"))
            await store.log_event(_make_event(batch_id="B", vm_id="dev/web-02"))
            await store.log_event(_make_event(batch_id="B", vm_id="dev/web-01"))  # duplicate

            batches = await store.get_recent_batches()
            assert len(batches) == 1
            vm_ids = set(batches[0]["vm_ids"])
            assert "dev/web-01" in vm_ids
            assert "dev/web-02" in vm_ids

    async def test_limit_respected(self) -> None:
        async with AuditStore(":memory:") as store:
            for i in range(10):
                await store.log_event(_make_event(batch_id=f"batch-{i:03d}"))

            batches = await store.get_recent_batches(limit=3)
            assert len(batches) == 3

    async def test_empty_store(self) -> None:
        async with AuditStore(":memory:") as store:
            batches = await store.get_recent_batches()
            assert batches == []

    async def test_batch_with_no_vm_events(self) -> None:
        async with AuditStore(":memory:") as store:
            await store.log_event(AuditEvent(
                event_type=EventType.BATCH_STARTED,
                batch_id="batch-A",
                vm_id=None,
                detail="batch start",
                timestamp=datetime.now(tz=timezone.utc),
            ))

            batches = await store.get_recent_batches()
            assert len(batches) == 1
            assert batches[0]["vm_ids"] == []

    async def test_started_at_is_earliest_event(self) -> None:
        async with AuditStore(":memory:") as store:
            early = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
            late = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

            # Insert late first, then early
            await store.log_event(AuditEvent(
                event_type=EventType.ACTION_STARTED,
                batch_id="B",
                detail="d",
                timestamp=late,
            ))
            await store.log_event(AuditEvent(
                event_type=EventType.BATCH_STARTED,
                batch_id="B",
                detail="d",
                timestamp=early,
            ))

            batches = await store.get_recent_batches()
            assert "2026-03-01T10:00:00" in batches[0]["started_at"]


# ---------------------------------------------------------------------------
# VM Graph integration: audit trail written correctly
# ---------------------------------------------------------------------------

class TestVMGraphAuditTrail:
    """Run the per-VM graph with mocked SSH/OS detection, assert on audit trail."""

    @pytest.fixture()
    def lock_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "locks"

    @pytest.fixture()
    def mock_executor(self) -> MagicMock:
        """SandboxExecutor in dry-run mode with mocked execute."""
        executor = MagicMock()
        executor.dry_run = True
        executor.execute = AsyncMock(return_value="[DRY-RUN] ok")
        return executor

    @pytest.fixture()
    def mock_ssh_manager(self) -> MagicMock:
        return MagicMock()

    async def test_disk_cleanup_events_written(
        self,
        lock_dir: Path,
        mock_executor: MagicMock,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """After running disk_cleanup, audit_results node writes ACTION_COMPLETED."""
        from errander.agent.vm_graph import build_vm_graph
        from errander.models.actions import ActionStatus
        from errander.models.vm import OSFamily, VMInfo
        from errander.safety.locking import FileLocker

        locker = FileLocker(lock_dir=lock_dir)

        async with AuditStore(":memory:") as audit_store:
            fake_vm_info = VMInfo(
                os_family=OSFamily.UBUNTU,
                os_version="22.04",
                disk_usage={"/": 80.0},
                docker_available=False,
                pending_packages=0,
                uptime_seconds=3600.0,
            )

            disk_cleanup_result = {
                "action_type": "disk_cleanup",
                "status": ActionStatus.DRY_RUN_OK.value,
                "vm_id": "test/vm-01",
                "started_at": datetime.now(tz=timezone.utc).isoformat(),
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                "detail": "dry-run complete",
                "error": None,
            }

            graph = build_vm_graph(
                executor=mock_executor,
                locker=locker,
                audit_store=audit_store,
                ssh_manager=mock_ssh_manager,
            ).compile()

            def _mock_run_result(action_type: str) -> dict[str, object]:
                return {
                    "action_type": action_type,
                    "status": ActionStatus.DRY_RUN_OK.value,
                    "vm_id": "test/vm-01",
                    "started_at": datetime.now(tz=timezone.utc).isoformat(),
                    "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                    "detail": "dry-run complete",
                    "error": None,
                }

            with (
                patch(
                    "errander.agent.vm_graph.detect_os",
                    new=AsyncMock(return_value=fake_vm_info),
                ),
                patch(
                    "errander.agent.vm_graph._run_disk_cleanup",
                    new=AsyncMock(return_value=disk_cleanup_result),
                ),
                patch(
                    "errander.agent.vm_graph._run_log_rotation",
                    new=AsyncMock(return_value=_mock_run_result("log_rotation")),
                ),
                patch(
                    "errander.agent.vm_graph._run_patching",
                    new=AsyncMock(return_value=_mock_run_result("patching")),
                ),
                patch(
                    "errander.agent.vm_graph._run_backup_verify",
                    new=AsyncMock(return_value=_mock_run_result("backup_verify")),
                ),
            ):
                initial = {
                    "vm_id": "test/vm-01",
                    "batch_id": "integration-batch-01",
                    "hostname": "192.168.1.10",
                    "ssh_user": "ubuntu",
                    "ssh_key_path": "/home/user/.ssh/id_ed25519",
                    "os_family": "ubuntu",
                    "dry_run": True,
                }
                await graph.ainvoke(initial)

            # Audit trail should have at least one ACTION_COMPLETED for disk_cleanup
            events = await audit_store.get_events(
                batch_id="integration-batch-01",
                action_type="disk_cleanup",
            )
            assert len(events) >= 1
            assert events[0].event_type == EventType.ACTION_COMPLETED
            assert events[0].vm_id == "test/vm-01"
            assert events[0].batch_id == "integration-batch-01"

    async def test_lock_failure_writes_audit_event(
        self,
        lock_dir: Path,
        mock_executor: MagicMock,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """When lock cannot be acquired, audit_results writes an ACTION_FAILED event."""
        from errander.agent.vm_graph import build_vm_graph
        from errander.safety.locking import FileLocker

        locker = FileLocker(lock_dir=lock_dir)
        vm_id = "test/vm-locked"

        # Pre-acquire the lock so the graph cannot acquire it
        await locker.acquire(vm_id, "other-batch")

        async with AuditStore(":memory:") as audit_store:
            graph = build_vm_graph(
                executor=mock_executor,
                locker=locker,
                audit_store=audit_store,
                ssh_manager=mock_ssh_manager,
            ).compile()

            initial = {
                "vm_id": vm_id,
                "batch_id": "integration-batch-02",
                "hostname": "192.168.1.20",
                "ssh_user": "ubuntu",
                "ssh_key_path": "/home/user/.ssh/id_ed25519",
                "os_family": "ubuntu",
                "dry_run": True,
            }
            await graph.ainvoke(initial)

            # Audit trail should have an ACTION_FAILED for the lock error
            events = await audit_store.get_events(
                batch_id="integration-batch-02",
            )
            assert len(events) >= 1
            failed = [e for e in events if e.event_type == EventType.ACTION_FAILED]
            assert len(failed) >= 1
            assert "locked" in failed[0].detail.lower()

    async def test_get_recent_batches_after_two_runs(
        self,
        lock_dir: Path,
        mock_executor: MagicMock,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """Two separate batch runs appear as two entries in get_recent_batches()."""
        from errander.agent.vm_graph import build_vm_graph
        from errander.models.actions import ActionStatus
        from errander.models.vm import OSFamily, VMInfo
        from errander.safety.locking import FileLocker

        locker = FileLocker(lock_dir=lock_dir)

        fake_vm_info = VMInfo(
            os_family=OSFamily.UBUNTU,
            os_version="22.04",
            disk_usage={"/": 80.0},
            docker_available=False,
            pending_packages=0,
            uptime_seconds=3600.0,
        )

        def _make_result(batch_id: str) -> dict[str, object]:
            return {
                "action_type": "disk_cleanup",
                "status": ActionStatus.DRY_RUN_OK.value,
                "vm_id": "test/vm-01",
                "started_at": datetime.now(tz=timezone.utc).isoformat(),
                "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                "detail": "dry-run ok",
                "error": None,
            }

        async with AuditStore(":memory:") as audit_store:
            graph = build_vm_graph(
                executor=mock_executor,
                locker=locker,
                audit_store=audit_store,
                ssh_manager=mock_ssh_manager,
            ).compile()

            def _mock_sub_result(action_type: str) -> dict[str, object]:
                return {
                    "action_type": action_type,
                    "status": ActionStatus.DRY_RUN_OK.value,
                    "vm_id": "test/vm-01",
                    "started_at": datetime.now(tz=timezone.utc).isoformat(),
                    "completed_at": datetime.now(tz=timezone.utc).isoformat(),
                    "detail": "dry-run ok",
                    "error": None,
                }

            for batch_id in ("run-alpha", "run-beta"):
                with (
                    patch(
                        "errander.agent.vm_graph.detect_os",
                        new=AsyncMock(return_value=fake_vm_info),
                    ),
                    patch(
                        "errander.agent.vm_graph._run_disk_cleanup",
                        new=AsyncMock(return_value=_make_result(batch_id)),
                    ),
                    patch(
                        "errander.agent.vm_graph._run_log_rotation",
                        new=AsyncMock(return_value=_mock_sub_result("log_rotation")),
                    ),
                    patch(
                        "errander.agent.vm_graph._run_patching",
                        new=AsyncMock(return_value=_mock_sub_result("patching")),
                    ),
                    patch(
                        "errander.agent.vm_graph._run_backup_verify",
                        new=AsyncMock(return_value=_mock_sub_result("backup_verify")),
                    ),
                ):
                    initial = {
                        "vm_id": "test/vm-01",
                        "batch_id": batch_id,
                        "hostname": "192.168.1.10",
                        "ssh_user": "ubuntu",
                        "ssh_key_path": "/home/user/.ssh/id_ed25519",
                        "os_family": "ubuntu",
                        "dry_run": True,
                    }
                    await graph.ainvoke(initial)

            batches = await audit_store.get_recent_batches()
            batch_ids = {b["batch_id"] for b in batches}
            assert "run-alpha" in batch_ids
            assert "run-beta" in batch_ids


# ---------------------------------------------------------------------------
# Audit CLI: run_audit_query integration
# ---------------------------------------------------------------------------

class TestRunAuditQuery:
    """Test the --audit CLI mode queries against a real SQLite database."""

    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> str:
        return str(tmp_path / "test-audit.sqlite")

    async def _seed(self, db_path: str) -> None:
        async with AuditStore(db_path) as store:
            await store.log_event(_make_event(
                batch_id="batch-cli-01",
                vm_id="dev/web-01",
                action_type="disk_cleanup",
                event_type=EventType.ACTION_COMPLETED,
            ))
            await store.log_event(_make_event(
                batch_id="batch-cli-01",
                vm_id="dev/web-01",
                action_type="patching",
                event_type=EventType.ACTION_FAILED,
            ))
            await store.log_event(_make_event(
                batch_id="batch-cli-02",
                vm_id="prod/app-01",
                action_type="disk_cleanup",
                event_type=EventType.ACTION_COMPLETED,
            ))

    async def test_query_by_batch_id(self, db_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        from errander.config.settings import load_settings
        from errander.main import _parse_args, run_audit_query

        await self._seed(db_path)

        settings = load_settings()
        settings.audit_db_url = db_path

        args = _parse_args(["--audit", "--batch-id", "batch-cli-01", "--last", "20"])
        result = await run_audit_query(args, settings)
        assert result == 0

        captured = capsys.readouterr()
        assert "batch-cli-01" in captured.out
        assert "batch-cli-02" not in captured.out

    async def test_query_by_action_type(self, db_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        from errander.config.settings import load_settings
        from errander.main import _parse_args, run_audit_query

        await self._seed(db_path)

        settings = load_settings()
        settings.audit_db_url = db_path

        args = _parse_args(["--audit", "--action-type", "disk_cleanup"])
        result = await run_audit_query(args, settings)
        assert result == 0

        captured = capsys.readouterr()
        assert "disk_cleanup" in captured.out
        assert "patching" not in captured.out

    async def test_batches_mode(self, db_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        from errander.config.settings import load_settings
        from errander.main import _parse_args, run_audit_query

        await self._seed(db_path)

        settings = load_settings()
        settings.audit_db_url = db_path

        args = _parse_args(["--audit", "--batches"])
        result = await run_audit_query(args, settings)
        assert result == 0

        captured = capsys.readouterr()
        assert "batch-cli-01" in captured.out
        assert "batch-cli-02" in captured.out

    async def test_invalid_event_type_returns_error(self, db_path: str) -> None:
        from errander.config.settings import load_settings
        from errander.main import _parse_args, run_audit_query

        settings = load_settings()
        settings.audit_db_url = db_path

        args = _parse_args(["--audit", "--event-type", "not_a_real_type"])
        result = await run_audit_query(args, settings)
        assert result == 1

    async def test_empty_result_prints_message(self, db_path: str, capsys: pytest.CaptureFixture[str]) -> None:
        from errander.config.settings import load_settings
        from errander.main import _parse_args, run_audit_query

        settings = load_settings()
        settings.audit_db_url = db_path

        args = _parse_args(["--audit", "--batch-id", "nonexistent"])
        result = await run_audit_query(args, settings)
        assert result == 0
        captured = capsys.readouterr()
        assert "No events" in captured.out
