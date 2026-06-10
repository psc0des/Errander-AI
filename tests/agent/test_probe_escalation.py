"""Phase F3 tests: probe escalation — critical signals trigger Slack alert."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.probe import _check_escalation, run_env_probe
from errander.db.core import AsyncDatabase
from errander.models.reports import ProbeVMResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    vm_id: str = "vm-01",
    *,
    reachable: bool = True,
    disk_pct: float = 50.0,
    delta_pct: float = 0.0,
    failed_services: list[str] | None = None,
    drift_changes: list[dict] | None = None,
    failed_login_count: int = 0,
) -> ProbeVMResult:
    disk_alerts = []
    if disk_pct > 0 or delta_pct > 0:
        disk_alerts = [{"mountpoint": "/", "used_pct_end": disk_pct, "delta_pct": delta_pct}]
    login_summary = None
    if failed_login_count > 0:
        login_summary = {"total_count": failed_login_count, "window_hours": 24}
    return ProbeVMResult(
        vm_id=vm_id,
        hostname=f"{vm_id}.example.com",
        reachable=reachable,
        disk_growth_alerts=disk_alerts,
        drift_changes=drift_changes or [],
        failed_login_summary=login_summary,
        failed_services=failed_services or [],
    )


# ---------------------------------------------------------------------------
# _check_escalation unit tests
# ---------------------------------------------------------------------------

class TestCheckEscalation:
    def test_no_signals_no_escalation(self) -> None:
        results = [_make_result("vm-01", disk_pct=50.0)]
        needed, reasons = _check_escalation(results)
        assert not needed
        assert reasons == []

    def test_disk_at_90_pct_escalates(self) -> None:
        results = [_make_result("vm-01", disk_pct=90.0)]
        needed, reasons = _check_escalation(results)
        assert needed
        assert any("vm-01" in r and "/" in r for r in reasons)

    def test_disk_at_89_pct_does_not_escalate(self) -> None:
        results = [_make_result("vm-01", disk_pct=89.9)]
        needed, reasons = _check_escalation(results)
        assert not needed

    def test_delta_pct_15_escalates(self) -> None:
        results = [_make_result("vm-01", disk_pct=50.0, delta_pct=15.0)]
        needed, reasons = _check_escalation(results)
        assert needed
        assert any("over window" in r for r in reasons)

    def test_delta_pct_14_does_not_escalate(self) -> None:
        results = [_make_result("vm-01", disk_pct=50.0, delta_pct=14.9)]
        needed, reasons = _check_escalation(results)
        assert not needed

    def test_two_failed_services_escalates(self) -> None:
        results = [_make_result("vm-01", failed_services=["nginx.service", "sshd.service"])]
        needed, reasons = _check_escalation(results)
        assert needed
        assert any("failed services" in r for r in reasons)

    def test_one_failed_service_does_not_escalate(self) -> None:
        results = [_make_result("vm-01", failed_services=["nginx.service"])]
        needed, reasons = _check_escalation(results)
        assert not needed

    def test_drift_plus_logins_escalates(self) -> None:
        results = [_make_result(
            "vm-01",
            drift_changes=[{"kind": "authorized_keys"}],
            failed_login_count=25,
        )]
        needed, reasons = _check_escalation(results)
        assert needed
        assert any("drift" in r and "login" in r for r in reasons)

    def test_drift_without_logins_does_not_escalate(self) -> None:
        results = [_make_result("vm-01", drift_changes=[{"kind": "sudoers"}], failed_login_count=0)]
        needed, reasons = _check_escalation(results)
        assert not needed

    def test_unreachable_vm_skipped(self) -> None:
        results = [_make_result("vm-01", reachable=False, disk_pct=95.0)]
        needed, reasons = _check_escalation(results)
        assert not needed

    def test_multiple_vms_multiple_reasons(self) -> None:
        results = [
            _make_result("vm-01", disk_pct=92.0),
            _make_result("vm-02", failed_services=["a.service", "b.service"]),
        ]
        needed, reasons = _check_escalation(results)
        assert needed
        assert len(reasons) == 2

    def test_logins_at_threshold_20_does_not_escalate_drift(self) -> None:
        """Exactly 20 logins with drift does NOT escalate — threshold is > 20."""
        results = [_make_result("vm-01", drift_changes=[{"kind": "sudoers"}], failed_login_count=20)]
        needed, _ = _check_escalation(results)
        assert not needed


# ---------------------------------------------------------------------------
# run_env_probe escalation integration tests
# ---------------------------------------------------------------------------

class TestRunEnvProbeEscalation:
    """run_env_probe populates escalation fields when thresholds are exceeded."""

    @pytest.mark.asyncio
    async def test_escalation_needed_set_on_critical_disk(self) -> None:
        from errander.safety.audit import AuditStore

        critical_result = _make_result("vm-01", disk_pct=91.0)

        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with patch("errander.agent.probe.probe_vm", new=AsyncMock(return_value=critical_result)):
                from errander.config.settings import SRESignalSettings
                report = await run_env_probe(
                    env_name="test-env",
                    vms=[{"vm_id": "vm-01", "hostname": "10.0.0.1", "ssh_user": "u",
                          "ssh_key_path": "/k", "os_family": "ubuntu"}],
                    ssh_manager=MagicMock(),
                    executor=MagicMock(),
                    disk_history_store=MagicMock(),
                    baseline_store=MagicMock(),
                    audit_store=store,
                    sre_settings=SRESignalSettings(),
                )

        assert report.escalation_needed
        assert len(report.escalation_reasons) >= 1

    @pytest.mark.asyncio
    async def test_no_escalation_on_healthy_fleet(self) -> None:
        from errander.safety.audit import AuditStore

        healthy_result = _make_result("vm-01", disk_pct=50.0)

        async with AuditStore(AsyncDatabase(":memory:")) as store:
            with patch("errander.agent.probe.probe_vm", new=AsyncMock(return_value=healthy_result)):
                from errander.config.settings import SRESignalSettings
                report = await run_env_probe(
                    env_name="test-env",
                    vms=[{"vm_id": "vm-01", "hostname": "10.0.0.1", "ssh_user": "u",
                          "ssh_key_path": "/k", "os_family": "ubuntu"}],
                    ssh_manager=MagicMock(),
                    executor=MagicMock(),
                    disk_history_store=MagicMock(),
                    baseline_store=MagicMock(),
                    audit_store=store,
                    sre_settings=SRESignalSettings(),
                )

        assert not report.escalation_needed
        assert report.escalation_reasons == []
