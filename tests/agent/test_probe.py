"""Tests for errander/agent/probe.py — standalone daily probe runner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.probe import probe_vm, run_env_probe
from errander.config.settings import SRESignalSettings
from errander.models.events import EventType
from errander.models.reports import DigestReport, ProbeVMResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sre_settings() -> SRESignalSettings:
    return SRESignalSettings()


def _make_executor() -> MagicMock:
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))
    return executor


def _make_ssh_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))
    return mgr


def _make_audit_store() -> MagicMock:
    store = MagicMock()
    store.log_event = AsyncMock()
    return store


def _discover_ok() -> AsyncMock:
    """discover_node stub: SSH up, returns vm_info."""
    return AsyncMock(return_value={
        "vm_info": {
            "os_family": "ubuntu",
            "os_version": "22.04",
            "disk_usage": {},
            "docker_available": False,
            "pending_packages": [],
            "uptime_seconds": 3600,
        },
        "os_family": "ubuntu",
    })


def _discover_fail() -> AsyncMock:
    """discover_node stub: SSH unreachable."""
    return AsyncMock(return_value={"error": "Discovery failed: Connection refused"})


# ---------------------------------------------------------------------------
# probe_vm — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_vm_returns_reachable_result_on_success() -> None:
    audit = _make_audit_store()

    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        result = await probe_vm(
            vm_id="vm-1",
            hostname="host-1",
            ssh_user="ubuntu",
            ssh_key_path="/key",
            os_family="ubuntu",
            ssh_manager=_make_ssh_manager(),
            executor=_make_executor(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=audit,
            sre_settings=_sre_settings(),
        )

    assert isinstance(result, ProbeVMResult)
    assert result.reachable is True
    assert result.vm_id == "vm-1"
    assert result.error is None


@pytest.mark.asyncio
async def test_probe_vm_populates_disk_alerts() -> None:
    alert = {"vm_id": "vm-1", "mountpoint": "/", "used_pct_start": 70.0, "used_pct_end": 85.0}
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": [alert]})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        result = await probe_vm(
            vm_id="vm-1", hostname="h", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=_make_ssh_manager(), executor=_make_executor(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=_make_audit_store(), sre_settings=_sre_settings(),
        )

    assert len(result.disk_growth_alerts) == 1
    assert result.disk_growth_alerts[0]["mountpoint"] == "/"


@pytest.mark.asyncio
async def test_probe_vm_populates_drift_changes() -> None:
    change = {"vm_id": "vm-1", "kind": "sudoers", "scope_key": "", "unified_diff": "- old\n+ new"}
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": [change]})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        result = await probe_vm(
            vm_id="vm-1", hostname="h", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=_make_ssh_manager(), executor=_make_executor(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=_make_audit_store(), sre_settings=_sre_settings(),
        )

    assert len(result.drift_changes) == 1
    assert result.drift_changes[0]["kind"] == "sudoers"


@pytest.mark.asyncio
async def test_probe_vm_populates_failed_logins() -> None:
    summary = {"vm_id": "vm-1", "total_count": 42, "window_hours": 24}
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": summary})),
    ):
        result = await probe_vm(
            vm_id="vm-1", hostname="h", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=_make_ssh_manager(), executor=_make_executor(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=_make_audit_store(), sre_settings=_sre_settings(),
        )

    assert result.failed_login_summary is not None
    assert result.failed_login_summary["total_count"] == 42


# ---------------------------------------------------------------------------
# probe_vm — failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_vm_returns_unreachable_when_discover_fails() -> None:
    """discover_node SSH failure returns reachable=False immediately — no signal nodes run."""
    disk_mock = AsyncMock(return_value={"disk_growth_alerts": []})
    with (
        patch("errander.agent.probe.discover_node", new=_discover_fail()),
        patch("errander.agent.probe.disk_snapshot_node", new=disk_mock),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock()),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock()),
    ):
        result = await probe_vm(
            vm_id="vm-fail", hostname="h", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=_make_ssh_manager(), executor=_make_executor(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=_make_audit_store(), sre_settings=_sre_settings(),
        )

    assert result.reachable is False
    assert "Connection refused" in (result.error or "")
    # Signal nodes must not run when discover fails
    disk_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_vm_returns_unreachable_on_exception() -> None:
    """Unexpected exception in any node → reachable=False with error message."""
    with (
        patch("errander.agent.probe.discover_node", new=AsyncMock(side_effect=ConnectionError("SSH refused"))),
    ):
        result = await probe_vm(
            vm_id="vm-fail", hostname="h", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=_make_ssh_manager(), executor=_make_executor(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=_make_audit_store(), sre_settings=_sre_settings(),
        )

    assert result.reachable is False
    assert "SSH refused" in (result.error or "")


# ---------------------------------------------------------------------------
# run_env_probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_env_probe_fans_out_to_all_vms() -> None:
    vms = [
        {"vm_id": "v1", "hostname": "h1", "ssh_user": "u", "ssh_key_path": "k"},
        {"vm_id": "v2", "hostname": "h2", "ssh_user": "u", "ssh_key_path": "k"},
        {"vm_id": "v3", "hostname": "h3", "ssh_user": "u", "ssh_key_path": "k"},
    ]
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        report = await run_env_probe(
            env_name="dev",
            vms=vms,
            ssh_manager=_make_ssh_manager(),
            executor=_make_executor(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=_make_audit_store(),
            sre_settings=_sre_settings(),
        )

    assert isinstance(report, DigestReport)
    assert len(report.vm_results) == 3
    assert report.env_name == "dev"
    assert report.reachable_count == 3


@pytest.mark.asyncio
async def test_run_env_probe_emits_audit_events() -> None:
    audit = _make_audit_store()
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        await run_env_probe(
            env_name="dev",
            vms=[{"vm_id": "v1", "hostname": "h1", "ssh_user": "u", "ssh_key_path": "k"}],
            ssh_manager=_make_ssh_manager(),
            executor=_make_executor(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=audit,
            sre_settings=_sre_settings(),
        )

    event_types = [call.args[0].event_type for call in audit.log_event.await_args_list]
    assert EventType.DAILY_PROBE_STARTED in event_types
    assert EventType.DAILY_PROBE_COMPLETE in event_types


@pytest.mark.asyncio
async def test_run_env_probe_tolerates_single_vm_failure() -> None:
    call_count = 0

    async def _discover_maybe_fail(state: object, **kwargs: object) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"error": "Discovery failed: first VM unreachable"}
        return {
            "vm_info": {"os_family": "ubuntu", "os_version": "22.04",
                        "disk_usage": {}, "docker_available": False,
                        "pending_packages": [], "uptime_seconds": 0},
            "os_family": "ubuntu",
        }

    with (
        patch("errander.agent.probe.discover_node", new=_discover_maybe_fail),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        report = await run_env_probe(
            env_name="dev",
            vms=[
                {"vm_id": "v1", "hostname": "h1", "ssh_user": "u", "ssh_key_path": "k"},
                {"vm_id": "v2", "hostname": "h2", "ssh_user": "u", "ssh_key_path": "k"},
            ],
            ssh_manager=_make_ssh_manager(),
            executor=_make_executor(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=_make_audit_store(),
            sre_settings=_sre_settings(),
        )

    assert len(report.vm_results) == 2
    unreachable = [r for r in report.vm_results if not r.reachable]
    reachable = [r for r in report.vm_results if r.reachable]
    assert len(unreachable) == 1
    assert len(reachable) == 1
