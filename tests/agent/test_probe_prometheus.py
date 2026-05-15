"""Tests for PrometheusClient integration in probe_vm() and render_digest_report()."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.probe import probe_vm, run_env_probe
from errander.config.settings import SRESignalSettings
from errander.models.reports import DigestReport, ProbeVMResult
from errander.observability.reporting import render_digest_report


def _sre() -> SRESignalSettings:
    return SRESignalSettings()


def _discover_ok() -> AsyncMock:
    return AsyncMock(return_value={
        "vm_info": {
            "os_family": "ubuntu", "os_version": "22.04",
            "disk_usage": {}, "docker_available": False,
            "pending_packages": [], "uptime_seconds": 3600,
        },
        "os_family": "ubuntu",
    })


def _make_prom_client(metrics: list[str]) -> MagicMock:
    client = MagicMock()
    client.fetch_vm_metrics = AsyncMock(return_value=metrics)
    client.close = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# probe_vm + PrometheusClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_vm_fetches_prometheus_metrics_when_client_present() -> None:
    prom = _make_prom_client(["CPU (5m): 72.3%", "Memory: 84.1%", "Load(5m): 2.40"])
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        result = await probe_vm(
            vm_id="v1", hostname="10.0.0.1", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=MagicMock(), executor=MagicMock(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=MagicMock(log_event=AsyncMock()),
            sre_settings=_sre(),
            prometheus_client=prom,
        )

    assert result.prometheus_metrics == ["CPU (5m): 72.3%", "Memory: 84.1%", "Load(5m): 2.40"]
    prom.fetch_vm_metrics.assert_awaited_once_with("10.0.0.1")


@pytest.mark.asyncio
async def test_probe_vm_empty_metrics_when_client_none() -> None:
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        result = await probe_vm(
            vm_id="v1", hostname="10.0.0.1", ssh_user="u", ssh_key_path="k",
            os_family="ubuntu", ssh_manager=MagicMock(), executor=MagicMock(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=MagicMock(log_event=AsyncMock()),
            sre_settings=_sre(),
            prometheus_client=None,
        )

    assert result.prometheus_metrics == []


# ---------------------------------------------------------------------------
# run_env_probe threads client through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_env_probe_passes_client_to_each_probe_vm() -> None:
    prom = _make_prom_client(["CPU (5m): 50.0%"])
    vms = [
        {"vm_id": "v1", "hostname": "10.0.0.1", "ssh_user": "u", "ssh_key_path": "k"},
        {"vm_id": "v2", "hostname": "10.0.0.2", "ssh_user": "u", "ssh_key_path": "k"},
    ]
    with (
        patch("errander.agent.probe.discover_node", new=_discover_ok()),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={"disk_growth_alerts": []})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={"drift_changes": []})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={"failed_login_summary": None})),
    ):
        report = await run_env_probe(
            env_name="dev", vms=vms,
            ssh_manager=MagicMock(), executor=MagicMock(),
            disk_history_store=MagicMock(), baseline_store=MagicMock(),
            audit_store=MagicMock(log_event=AsyncMock()),
            sre_settings=_sre(),
            prometheus_client=prom,
        )

    # Both VMs should have metrics
    assert all(r.prometheus_metrics == ["CPU (5m): 50.0%"] for r in report.vm_results)
    # fetch_vm_metrics called once per VM
    assert prom.fetch_vm_metrics.await_count == 2


# ---------------------------------------------------------------------------
# render_digest_report — Prometheus section
# ---------------------------------------------------------------------------


def _report_with_prom(metrics: list[str]) -> DigestReport:
    return DigestReport(
        probe_id="p1",
        env_name="dev",
        generated_at=datetime(2026, 5, 16, 6, 0, tzinfo=timezone.utc),
        vm_results=[
            ProbeVMResult(
                vm_id="v1", hostname="host-1", reachable=True,
                prometheus_metrics=metrics,
            ),
        ],
    )


def test_render_digest_report_includes_prometheus_section() -> None:
    report = _report_with_prom(["CPU (5m): 72.3%", "Memory: 84.1%", "Load(5m): 2.40"])
    text = render_digest_report(report)
    assert ":bar_chart: Prometheus Metrics" in text
    assert "v1" in text
    assert "CPU (5m): 72.3%" in text
    assert "Memory: 84.1%" in text


def test_render_digest_report_no_prometheus_section_when_empty() -> None:
    report = _report_with_prom([])
    text = render_digest_report(report)
    assert ":bar_chart:" not in text


def test_render_digest_report_prometheus_counts_vm() -> None:
    report = DigestReport(
        probe_id="p1", env_name="dev",
        generated_at=datetime(2026, 5, 16, 6, 0, tzinfo=timezone.utc),
        vm_results=[
            ProbeVMResult(vm_id="v1", hostname="h1", reachable=True,
                          prometheus_metrics=["CPU (5m): 40.0%"]),
            ProbeVMResult(vm_id="v2", hostname="h2", reachable=True,
                          prometheus_metrics=["CPU (5m): 95.0%"]),
        ],
    )
    text = render_digest_report(report)
    assert "2 VM(s)" in text
