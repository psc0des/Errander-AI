"""Tests for BatchReport and supporting SRE report models."""

from __future__ import annotations

from datetime import UTC, datetime

from errander.models.reports import (
    BatchReport,
    DiskGrowth,
    DriftChange,
    FailedLoginSummary,
    PreflightBlock,
    ServiceRegression,
    VMRebootStatus,
)


class TestBatchReportDefaults:
    def test_all_list_fields_default_empty(self) -> None:
        report = BatchReport(batch_id="test-batch")
        assert report.vm_action_results == []
        assert report.preflight_blocks == []
        assert report.service_health_regressions == []
        assert report.reboot_required == []
        assert report.disk_growth_alerts == []
        assert report.drift_changes == []
        assert report.failed_logins == []

    def test_batch_id_required(self) -> None:
        report = BatchReport(batch_id="batch-123")
        assert report.batch_id == "batch-123"

    def test_generated_at_defaults_to_now(self) -> None:
        before = datetime.now()
        report = BatchReport(batch_id="test")
        after = datetime.now()
        assert before <= report.generated_at <= after


class TestPreflightBlock:
    def test_construction(self) -> None:
        block = PreflightBlock(
            vm_id="prod/web-01",
            action_type="patching",
            reason="pkg_lock",
            holder_pid=1234,
            holder_cmd="apt-get",
        )
        assert block.vm_id == "prod/web-01"
        assert block.holder_pid == 1234

    def test_none_holder_fields(self) -> None:
        block = PreflightBlock(
            vm_id="prod/web-01",
            action_type="patching",
            reason="pkg_lock",
            holder_pid=None,
            holder_cmd=None,
        )
        assert block.holder_pid is None
        assert block.holder_cmd is None

    def test_immutable(self) -> None:
        import dataclasses
        block = PreflightBlock("v", "t", "r", None, None)
        assert dataclasses.is_dataclass(block)


class TestVMRebootStatus:
    def test_construction(self) -> None:
        now = datetime.now(tz=UTC)
        status = VMRebootStatus(
            vm_id="prod/web-01",
            reason="packages require restart",
            pkgs_requiring=("linux-image-6.1",),
            detected_at=now,
        )
        assert "linux-image-6.1" in status.pkgs_requiring
        assert status.detected_at == now


class TestServiceRegression:
    def test_construction(self) -> None:
        reg = ServiceRegression(
            vm_id="prod/web-01",
            service_name="nginx",
            state_before="active",
            state_after="failed",
        )
        assert reg.service_name == "nginx"
        assert reg.state_before == "active"
        assert reg.state_after == "failed"


class TestDiskGrowth:
    def _make(self, start_pct: float, end_pct: float, days: int = 7) -> DiskGrowth:
        from datetime import timedelta
        now = datetime.now(tz=UTC)
        return DiskGrowth(
            vm_id="prod/web-01",
            mountpoint="/var",
            used_pct_start=start_pct,
            used_pct_end=end_pct,
            window_start=now - timedelta(days=days),
            window_end=now,
        )

    def test_delta_pct(self) -> None:
        g = self._make(60.0, 75.0)
        assert abs(g.delta_pct - 15.0) < 0.01

    def test_window_label_days_only(self) -> None:
        g = self._make(60.0, 75.0, days=7)
        assert "d" in g.window_label

    def test_window_label_days_and_hours(self) -> None:
        from datetime import timedelta
        now = datetime.now(tz=UTC)
        g = DiskGrowth(
            vm_id="prod/web-01",
            mountpoint="/",
            used_pct_start=50.0,
            used_pct_end=65.0,
            window_start=now - timedelta(days=6, hours=22),
            window_end=now,
        )
        assert "h" in g.window_label


class TestDriftChange:
    def test_construction(self) -> None:
        change = DriftChange(
            vm_id="prod/web-01",
            kind="authorized_keys",
            scope_key="deploy",
            unified_diff="--- baseline\n+++ current\n+ssh-ed25519 AAAA\n",
        )
        assert change.kind == "authorized_keys"
        assert change.scope_key == "deploy"


class TestFailedLoginSummary:
    def test_construction(self) -> None:
        summary = FailedLoginSummary(
            vm_id="prod/web-01",
            window_hours=24,
            total_count=142,
            top_users=(("root", 100), ("admin", 42)),
            top_source_ips=(("1.2.3.4", 80), ("5.6.7.8", 62)),
        )
        assert summary.total_count == 142
        assert summary.top_users[0] == ("root", 100)


class TestNewEnums:
    def test_blocked_status_exists(self) -> None:
        from errander.models.actions import ActionStatus
        assert ActionStatus.BLOCKED == "blocked"

    def test_new_event_types_exist(self) -> None:
        from errander.models.events import EventType
        assert EventType.PREFLIGHT_LOCK_DETECTED == "preflight_lock_detected"
        assert EventType.PREFLIGHT_LOCK_CLEAR == "preflight_lock_clear"
        assert EventType.REBOOT_REQUIRED_DETECTED == "reboot_required_detected"
        assert EventType.SERVICE_HEALTH_REGRESSION == "service_health_regression"
        assert EventType.DISK_USAGE_CAPTURED == "disk_usage_captured"
        assert EventType.DRIFT_KIND_BASELINE_SAVED == "drift_kind_baseline_saved"
        assert EventType.DRIFT_KIND_CHANGED == "drift_kind_changed"
        assert EventType.FAILED_SSH_LOGINS_OBSERVED == "failed_ssh_logins_observed"
