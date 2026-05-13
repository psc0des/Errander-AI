"""Tests for report generation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from errander.models.reports import (
    BatchReport,
    DiskGrowth,
    DriftChange,
    FailedLoginSummary,
    PreflightBlock,
    ServiceRegression,
    VMRebootStatus,
)
from errander.observability.reporting import format_reboot_required_section, render_batch_report
from errander.safety.vm_state import VMState


def _vm_state(
    vm_id: str,
    needs_reboot: bool = True,
    reason: str | None = "packages require reboot",
    pkgs: tuple[str, ...] = (),
) -> VMState:
    return VMState(
        vm_id=vm_id,
        needs_reboot=needs_reboot,
        needs_reboot_reason=reason,
        needs_reboot_pkgs=pkgs,
        needs_reboot_detected_at=datetime.now(tz=UTC),
        last_uptime_seconds=None,
        updated_at=datetime.now(tz=UTC),
    )


def _make_report(**kwargs) -> BatchReport:  # type: ignore[no-untyped-def]
    defaults: dict = {
        "batch_id": "b-test-01",
        "generated_at": datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return BatchReport(**defaults)


def _make_disk_growth(
    vm_id: str = "prod/db-01",
    mountpoint: str = "/var",
    start: float = 50.0,
    end: float = 75.0,
    days: int = 7,
) -> DiskGrowth:
    now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    return DiskGrowth(
        vm_id=vm_id,
        mountpoint=mountpoint,
        used_pct_start=start,
        used_pct_end=end,
        window_start=now - timedelta(days=days),
        window_end=now,
    )


class TestFormatRebootRequiredSection:
    def test_empty_list_returns_empty_string(self) -> None:
        assert format_reboot_required_section([]) == ""

    def test_single_vm_no_pkgs(self) -> None:
        result = format_reboot_required_section([_vm_state("dev/web-01")])
        assert "dev/web-01" in result
        assert "packages require reboot" in result

    def test_multiple_vms_listed(self) -> None:
        vms = [_vm_state("dev/web-01"), _vm_state("prod/db-01")]
        result = format_reboot_required_section(vms)
        assert "dev/web-01" in result
        assert "prod/db-01" in result

    def test_pkg_names_included(self) -> None:
        vms = [_vm_state("dev/web-01", pkgs=("libc6", "linux-base"))]
        result = format_reboot_required_section(vms)
        assert "libc6" in result
        assert "linux-base" in result

    def test_more_than_five_pkgs_truncated(self) -> None:
        pkgs = tuple(f"pkg-{i}" for i in range(8))
        vms = [_vm_state("dev/web-01", pkgs=pkgs)]
        result = format_reboot_required_section(vms)
        assert "+3 more" in result

    def test_exactly_five_pkgs_not_truncated(self) -> None:
        pkgs = tuple(f"pkg-{i}" for i in range(5))
        vms = [_vm_state("dev/web-01", pkgs=pkgs)]
        result = format_reboot_required_section(vms)
        assert "more" not in result

    def test_none_reason_falls_back(self) -> None:
        vms = [_vm_state("dev/web-01", reason=None)]
        result = format_reboot_required_section(vms)
        assert "dev/web-01" in result
        assert "reboot required" in result

    def test_section_header_present(self) -> None:
        result = format_reboot_required_section([_vm_state("dev/web-01")])
        assert "awaiting reboot" in result.lower()


# ---------------------------------------------------------------------------
# render_batch_report
# ---------------------------------------------------------------------------

class TestRenderBatchReportHeader:
    def test_contains_batch_id(self) -> None:
        report = _make_report(batch_id="b-abc-123")
        result = render_batch_report(report)
        assert "b-abc-123" in result

    def test_contains_generated_at(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "2026-05-13" in result

    def test_empty_report_renders_cleanly(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        # Should not raise, should contain batch ID
        assert isinstance(result, str)
        assert "b-test-01" in result

    def test_no_action_section_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Action Results" not in result


class TestRenderBatchReportActionResults:
    def _action(self, vm_id: str, status: str, action: str = "disk_cleanup") -> dict:
        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC).isoformat()
        return {
            "vm_id": vm_id,
            "action_type": action,
            "status": status,
            "started_at": now,
            "completed_at": now,
            "detail": f"{action} done",
            "error": None,
        }

    def test_action_section_present_when_non_empty(self) -> None:
        report = _make_report(vm_action_results=[
            self._action("dev/web-01", "succeeded"),
        ])
        result = render_batch_report(report)
        assert "Action Results" in result

    def test_vm_id_appears_in_result(self) -> None:
        report = _make_report(vm_action_results=[
            self._action("prod/db-01", "succeeded"),
        ])
        result = render_batch_report(report)
        assert "prod/db-01" in result

    def test_succeeded_count_shown(self) -> None:
        report = _make_report(vm_action_results=[
            self._action("dev/web-01", "succeeded"),
            self._action("dev/web-02", "succeeded"),
        ])
        result = render_batch_report(report)
        assert "2 succeeded" in result

    def test_failed_count_shown(self) -> None:
        report = _make_report(vm_action_results=[
            self._action("dev/web-01", "failed"),
        ])
        result = render_batch_report(report)
        assert "1 failed" in result

    def test_error_shown_when_present(self) -> None:
        action = self._action("dev/web-01", "failed")
        action["error"] = "SSH connection refused"
        report = _make_report(vm_action_results=[action])
        result = render_batch_report(report)
        assert "SSH connection refused" in result


class TestRenderBatchReportPreflightBlocks:
    def test_section_absent_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Preflight" not in result

    def test_section_present_when_non_empty(self) -> None:
        block = PreflightBlock(
            vm_id="prod/db-01",
            action_type="patching",
            reason="pkg_lock",
            holder_pid=1234,
            holder_cmd="apt-get",
        )
        report = _make_report(preflight_blocks=[block])
        result = render_batch_report(report)
        assert "Preflight" in result
        assert "prod/db-01" in result
        assert "apt-get" in result

    def test_holder_cmd_shown(self) -> None:
        block = PreflightBlock(
            vm_id="dev/web-01",
            action_type="patching",
            reason="pkg_lock",
            holder_pid=None,
            holder_cmd="dpkg",
        )
        report = _make_report(preflight_blocks=[block])
        result = render_batch_report(report)
        assert "dpkg" in result

    def test_no_holder_cmd(self) -> None:
        block = PreflightBlock(
            vm_id="dev/web-01",
            action_type="patching",
            reason="maintenance_window",
            holder_pid=None,
            holder_cmd=None,
        )
        report = _make_report(preflight_blocks=[block])
        result = render_batch_report(report)
        assert "maintenance_window" in result


class TestRenderBatchReportServiceRegressions:
    def test_section_absent_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Service Regressions" not in result

    def test_section_present_when_non_empty(self) -> None:
        reg = ServiceRegression(
            vm_id="prod/db-01",
            service_name="postgresql",
            state_before="active",
            state_after="failed",
        )
        report = _make_report(service_health_regressions=[reg])
        result = render_batch_report(report)
        assert "Service Regressions" in result
        assert "postgresql" in result
        assert "active" in result
        assert "failed" in result

    def test_vm_id_shown(self) -> None:
        reg = ServiceRegression(
            vm_id="prod/svc-01",
            service_name="nginx",
            state_before="active",
            state_after="failed",
        )
        report = _make_report(service_health_regressions=[reg])
        result = render_batch_report(report)
        assert "prod/svc-01" in result


class TestRenderBatchReportRebootRequired:
    def test_section_absent_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Reboot Required" not in result

    def test_section_present_when_non_empty(self) -> None:
        vm = VMRebootStatus(
            vm_id="dev/web-01",
            reason="kernel update pending",
            pkgs_requiring=("linux-image-6.1.0",),
            detected_at=datetime(2026, 5, 13, 9, 0, 0, tzinfo=UTC),
        )
        report = _make_report(reboot_required=[vm])
        result = render_batch_report(report)
        assert "Reboot Required" in result
        assert "dev/web-01" in result
        assert "linux-image-6.1.0" in result

    def test_pkg_truncation_at_five(self) -> None:
        pkgs = tuple(f"pkg-{i}" for i in range(8))
        vm = VMRebootStatus(
            vm_id="dev/web-01",
            reason="patching",
            pkgs_requiring=pkgs,
            detected_at=datetime(2026, 5, 13, 9, 0, 0, tzinfo=UTC),
        )
        report = _make_report(reboot_required=[vm])
        result = render_batch_report(report)
        assert "+3 more" in result


class TestRenderBatchReportDriftChanges:
    def test_section_absent_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Configuration Drift" not in result

    def test_section_present_when_non_empty(self) -> None:
        change = DriftChange(
            vm_id="prod/db-01",
            kind="sudoers",
            scope_key="",
            unified_diff="--- old\n+++ new\n+admin ALL=(ALL) NOPASSWD: ALL",
        )
        report = _make_report(drift_changes=[change])
        result = render_batch_report(report)
        assert "Configuration Drift" in result
        assert "prod/db-01" in result
        assert "sudoers" in result

    def test_grouped_by_kind(self) -> None:
        changes = [
            DriftChange(vm_id="vm-1", kind="sudoers", scope_key="", unified_diff="+ foo"),
            DriftChange(vm_id="vm-2", kind="authorized_keys", scope_key="alice", unified_diff="+ key"),
            DriftChange(vm_id="vm-3", kind="sudoers", scope_key="", unified_diff="+ bar"),
        ]
        report = _make_report(drift_changes=changes)
        result = render_batch_report(report)
        # authorized_keys comes before sudoers alphabetically
        idx_ak = result.index("authorized_keys")
        idx_su = result.index("sudoers")
        assert idx_ak < idx_su

    def test_scope_key_shown_for_authorized_keys(self) -> None:
        change = DriftChange(
            vm_id="prod/db-01",
            kind="authorized_keys",
            scope_key="deploy",
            unified_diff="+ ssh-rsa AAAA...",
        )
        report = _make_report(drift_changes=[change])
        result = render_batch_report(report)
        assert "deploy" in result

    def test_diff_lines_included(self) -> None:
        change = DriftChange(
            vm_id="prod/db-01",
            kind="listening_ports",
            scope_key="",
            unified_diff="--- old\n+++ new\n+ tcp LISTEN 0.0.0.0:4444",
        )
        report = _make_report(drift_changes=[change])
        result = render_batch_report(report)
        assert "4444" in result


class TestRenderBatchReportDiskGrowth:
    def test_section_absent_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Disk Growth" not in result

    def test_section_present_when_non_empty(self) -> None:
        report = _make_report(disk_growth_alerts=[_make_disk_growth()])
        result = render_batch_report(report)
        assert "Disk Growth" in result

    def test_vm_and_mountpoint_shown(self) -> None:
        report = _make_report(disk_growth_alerts=[
            _make_disk_growth(vm_id="prod/db-01", mountpoint="/data"),
        ])
        result = render_batch_report(report)
        assert "prod/db-01" in result
        assert "/data" in result

    def test_pct_values_shown(self) -> None:
        report = _make_report(disk_growth_alerts=[
            _make_disk_growth(start=60.0, end=82.0),
        ])
        result = render_batch_report(report)
        assert "60.0%" in result
        assert "82.0%" in result

    def test_delta_shown(self) -> None:
        report = _make_report(disk_growth_alerts=[
            _make_disk_growth(start=50.0, end=75.0),
        ])
        result = render_batch_report(report)
        assert "+25.0%" in result

    def test_window_label_shown(self) -> None:
        report = _make_report(disk_growth_alerts=[
            _make_disk_growth(days=7),
        ])
        result = render_batch_report(report)
        assert "7d" in result


class TestRenderBatchReportFailedLogins:
    def _make_summary(
        self,
        vm_id: str = "prod/web-01",
        total: int = 100,
        top_users: tuple = (("root", 60), ("admin", 30)),
        top_ips: tuple = (("1.2.3.4", 80), ("5.6.7.8", 20)),
    ) -> FailedLoginSummary:
        return FailedLoginSummary(
            vm_id=vm_id,
            window_hours=24,
            total_count=total,
            top_users=top_users,
            top_source_ips=top_ips,
        )

    def test_section_absent_when_empty(self) -> None:
        report = _make_report()
        result = render_batch_report(report)
        assert "Failed SSH" not in result

    def test_section_present_when_non_empty(self) -> None:
        report = _make_report(failed_logins=[self._make_summary()])
        result = render_batch_report(report)
        assert "Failed SSH" in result

    def test_vm_id_and_count_shown(self) -> None:
        report = _make_report(failed_logins=[self._make_summary(total=247)])
        result = render_batch_report(report)
        assert "prod/web-01" in result
        assert "247" in result

    def test_top_users_shown(self) -> None:
        report = _make_report(failed_logins=[self._make_summary()])
        result = render_batch_report(report)
        assert "root" in result
        assert "admin" in result

    def test_top_ips_shown(self) -> None:
        report = _make_report(failed_logins=[self._make_summary()])
        result = render_batch_report(report)
        assert "1.2.3.4" in result

    def test_window_hours_shown(self) -> None:
        report = _make_report(failed_logins=[self._make_summary()])
        result = render_batch_report(report)
        assert "24h" in result

    def test_multiple_vms(self) -> None:
        report = _make_report(failed_logins=[
            self._make_summary(vm_id="vm-1"),
            self._make_summary(vm_id="vm-2"),
        ])
        result = render_batch_report(report)
        assert "vm-1" in result
        assert "vm-2" in result


class TestRenderBatchReportFull:
    def test_all_sections_present(self) -> None:
        """Full report with all sections populated renders all headers."""
        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
        report = BatchReport(
            batch_id="b-full",
            generated_at=now,
            vm_action_results=[{
                "vm_id": "prod/db-01",
                "action_type": "disk_cleanup",
                "status": "succeeded",
                "started_at": now.isoformat(),
                "completed_at": now.isoformat(),
                "detail": "cleaned 500MB",
                "error": None,
            }],
            preflight_blocks=[
                PreflightBlock("prod/db-01", "patching", "pkg_lock", 1234, "apt"),
            ],
            service_health_regressions=[
                ServiceRegression("prod/db-01", "postgresql", "active", "failed"),
            ],
            reboot_required=[
                VMRebootStatus("prod/db-01", "kernel", ("linux-image",), now),
            ],
            drift_changes=[
                DriftChange("prod/db-01", "sudoers", "", "--- old\n+++ new"),
            ],
            disk_growth_alerts=[_make_disk_growth()],
            failed_logins=[
                FailedLoginSummary(
                    "prod/db-01", 24, 50,
                    (("root", 30),), (("1.2.3.4", 50),),
                ),
            ],
        )
        result = render_batch_report(report)
        assert "Action Results" in result
        assert "Preflight" in result
        assert "Service Regressions" in result
        assert "Reboot Required" in result
        assert "Configuration Drift" in result
        assert "Disk Growth" in result
        assert "Failed SSH" in result

    def test_section_ordering(self) -> None:
        """Sections appear in canonical order."""
        now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
        report = BatchReport(
            batch_id="b-order",
            generated_at=now,
            vm_action_results=[{
                "vm_id": "vm-1", "action_type": "disk_cleanup",
                "status": "succeeded", "started_at": now.isoformat(),
                "completed_at": now.isoformat(), "detail": "", "error": None,
            }],
            drift_changes=[DriftChange("vm-1", "sudoers", "", "")],
            disk_growth_alerts=[_make_disk_growth()],
        )
        result = render_batch_report(report)
        idx_actions = result.index("Action Results")
        idx_drift = result.index("Configuration Drift")
        idx_disk = result.index("Disk Growth")
        assert idx_actions < idx_drift < idx_disk
