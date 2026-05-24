"""Tests for render_digest_report() in errander/observability/reporting.py."""

from __future__ import annotations

from datetime import UTC, datetime

from errander.models.reports import DigestReport, ProbeVMResult
from errander.observability.reporting import render_digest_report


def _ts() -> datetime:
    return datetime(2026, 5, 15, 6, 0, 0, tzinfo=UTC)


def _healthy_report(env: str = "dev", vm_count: int = 3) -> DigestReport:
    return DigestReport(
        probe_id="probe-dev-20260515T060000",
        env_name=env,
        generated_at=_ts(),
        vm_results=[
            ProbeVMResult(vm_id=f"v{i}", hostname=f"h{i}", reachable=True)
            for i in range(vm_count)
        ],
    )


# ---------------------------------------------------------------------------
# Healthy fleet
# ---------------------------------------------------------------------------


def test_render_digest_report_healthy_fleet() -> None:
    report = _healthy_report()
    text = render_digest_report(report)
    assert ":white_check_mark:" in text
    assert "No signals detected" in text
    assert "3/3 VMs reachable" in text


def test_render_digest_report_env_name_in_header() -> None:
    report = _healthy_report(env="production")
    text = render_digest_report(report)
    first_line = text.splitlines()[0]
    assert "production" in first_line


def test_render_digest_report_timestamp_in_header() -> None:
    report = _healthy_report()
    text = render_digest_report(report)
    assert "2026-05-15" in text


# ---------------------------------------------------------------------------
# Unreachable VMs
# ---------------------------------------------------------------------------


def test_render_digest_report_with_unreachable_vms() -> None:
    report = DigestReport(
        probe_id="p1", env_name="dev", generated_at=_ts(),
        vm_results=[
            ProbeVMResult(vm_id="v1", hostname="host-1", reachable=True),
            ProbeVMResult(vm_id="v2", hostname="host-2", reachable=False, error="SSH refused"),
        ],
    )
    text = render_digest_report(report)
    assert ":x: Unreachable VMs" in text
    assert "v2" in text
    assert "SSH refused" in text
    assert "1/2 VMs reachable" in text


# ---------------------------------------------------------------------------
# Disk alerts
# ---------------------------------------------------------------------------


def test_render_digest_report_with_disk_alerts() -> None:
    alert1 = {"vm_id": "v1", "mountpoint": "/", "used_pct_start": 70.0, "used_pct_end": 85.0}
    alert2 = {"vm_id": "v2", "mountpoint": "/data", "used_pct_start": 60.0, "used_pct_end": 75.0}
    report = DigestReport(
        probe_id="p1", env_name="dev", generated_at=_ts(),
        vm_results=[
            ProbeVMResult(vm_id="v1", hostname="h1", reachable=True, disk_growth_alerts=[alert1]),
            ProbeVMResult(vm_id="v2", hostname="h2", reachable=True, disk_growth_alerts=[alert2]),
        ],
    )
    text = render_digest_report(report)
    assert ":chart_with_upwards_trend: Disk Growth Alerts (2)" in text
    assert "/" in text
    assert "/data" in text
    assert "70.0" in text
    assert "85.0" in text


# ---------------------------------------------------------------------------
# Drift changes
# ---------------------------------------------------------------------------


def test_render_digest_report_with_drift_grouped_by_kind() -> None:
    changes = [
        {"vm_id": "v1", "kind": "sudoers", "scope_key": "", "unified_diff": "- old\n+ new"},
        {"vm_id": "v2", "kind": "sudoers", "scope_key": "", "unified_diff": "- x\n+ y"},
        {"vm_id": "v1", "kind": "authorized_keys", "scope_key": "ubuntu", "unified_diff": "- k1"},
    ]
    report = DigestReport(
        probe_id="p1", env_name="dev", generated_at=_ts(),
        vm_results=[
            ProbeVMResult(vm_id="v1", hostname="h1", reachable=True, drift_changes=changes[:2]),
            ProbeVMResult(vm_id="v2", hostname="h2", reachable=True, drift_changes=changes[2:]),
        ],
    )
    text = render_digest_report(report)
    assert ":warning: Drift Detected (3 change(s))" in text
    assert "sudoers" in text
    assert "authorized_keys" in text


# ---------------------------------------------------------------------------
# Failed logins
# ---------------------------------------------------------------------------


def test_render_digest_report_with_failed_logins() -> None:
    summary1 = {"vm_id": "v1", "total_count": 10, "window_hours": 24}
    summary2 = {"vm_id": "v2", "total_count": 0, "window_hours": 24}
    report = DigestReport(
        probe_id="p1", env_name="dev", generated_at=_ts(),
        vm_results=[
            ProbeVMResult(vm_id="v1", hostname="h1", reachable=True, failed_login_summary=summary1),
            ProbeVMResult(vm_id="v2", hostname="h2", reachable=True, failed_login_summary=summary2),
        ],
    )
    text = render_digest_report(report)
    assert ":lock: Failed SSH Logins (last 24h)" in text
    # total_count=10 across both summaries (0 from v2)
    assert "10 total" in text
    assert "v1" in text
    # v2 has count 0 — should not appear as a line item
    assert "v2 --" not in text


# ---------------------------------------------------------------------------
# Combined all signals
# ---------------------------------------------------------------------------


def test_render_digest_report_combined_all_signals() -> None:
    report = DigestReport(
        probe_id="p1", env_name="staging", generated_at=_ts(),
        vm_results=[
            ProbeVMResult(
                vm_id="v1", hostname="h1", reachable=True,
                disk_growth_alerts=[{"vm_id": "v1", "mountpoint": "/", "used_pct_start": 70.0, "used_pct_end": 82.0}],
                drift_changes=[{"vm_id": "v1", "kind": "sudoers", "scope_key": "", "unified_diff": "- x\n+ y"}],
                failed_login_summary={"vm_id": "v1", "total_count": 5, "window_hours": 24},
            ),
            ProbeVMResult(vm_id="v2", hostname="h2", reachable=False, error="timeout"),
        ],
    )
    text = render_digest_report(report)
    assert ":x: Unreachable VMs" in text
    assert ":chart_with_upwards_trend: Disk Growth Alerts" in text
    assert ":warning: Drift Detected" in text
    assert ":lock: Failed SSH Logins" in text
    # Healthy sentinel should NOT appear when signals exist
    assert ":white_check_mark:" not in text
