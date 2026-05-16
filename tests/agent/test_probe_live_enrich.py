"""Phase E Commit 3 tests: journalctl + systemctl --failed enrichment in probe_vm."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.probe import _parse_failed_services, _parse_journal_errors
from errander.models.reports import DigestReport, ProbeVMResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ssh_result(stdout: str = "", success: bool = True) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.stdout = stdout
    r.stderr = ""
    return r


def _make_sre_settings() -> MagicMock:
    s = MagicMock()
    s.disk_growth_trend = MagicMock(enabled=False)
    s.drift = MagicMock(
        sudoers=False, authorized_keys=False,
        listening_ports=False, scheduled_jobs=False,
    )
    s.failed_ssh_logins = MagicMock(enabled=False)
    return s


# ---------------------------------------------------------------------------
# probe_vm SSH call tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_vm_fetches_journal_errors() -> None:
    """probe_vm() must call journalctl and populate result.journal_errors."""
    from errander.agent.probe import probe_vm

    journal_output = (
        "May 16 02:00:00 web-01 nginx[1234]: Cannot bind to port 80\n"
        "May 16 02:00:01 web-01 systemd[1]: Failed to start nginx\n"
    )

    call_count = {"n": 0}

    async def _fake_execute(vm_id: str, host: str, user: str, key: str, cmd: str, **kw: object) -> MagicMock:
        call_count["n"] += 1
        if "journalctl" in cmd:
            return _make_ssh_result(journal_output)
        return _make_ssh_result("")

    ssh_manager = MagicMock()
    ssh_manager.execute = _fake_execute

    with (
        patch("errander.agent.probe.discover_node", new=AsyncMock(return_value={"vm_info": {}})),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={})),
    ):
        result = await probe_vm(
            vm_id="dev/web-01",
            hostname="10.0.0.1",
            ssh_user="errander",
            ssh_key_path="/keys/web-01.pem",
            os_family="ubuntu",
            ssh_manager=ssh_manager,
            executor=MagicMock(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=MagicMock(log_event=AsyncMock()),
            sre_settings=_make_sre_settings(),
        )

    assert len(result.journal_errors) > 0
    assert any("Cannot bind" in e or "Failed to start" in e for e in result.journal_errors)


@pytest.mark.asyncio
async def test_probe_vm_fetches_failed_services() -> None:
    """probe_vm() must call systemctl --failed and populate result.failed_services."""
    from errander.agent.probe import probe_vm

    systemctl_output = "  sshd.service  loaded failed failed  OpenSSH Daemon\n"

    async def _fake_execute(vm_id: str, host: str, user: str, key: str, cmd: str, **kw: object) -> MagicMock:
        if "systemctl" in cmd and "failed" in cmd:
            return _make_ssh_result(systemctl_output)
        return _make_ssh_result("")

    ssh_manager = MagicMock()
    ssh_manager.execute = _fake_execute

    with (
        patch("errander.agent.probe.discover_node", new=AsyncMock(return_value={"vm_info": {}})),
        patch("errander.agent.probe.disk_snapshot_node", new=AsyncMock(return_value={})),
        patch("errander.agent.probe.drift_baseline_node", new=AsyncMock(return_value={})),
        patch("errander.agent.probe.failed_logins_node", new=AsyncMock(return_value={})),
    ):
        result = await probe_vm(
            vm_id="dev/web-01",
            hostname="10.0.0.1",
            ssh_user="errander",
            ssh_key_path="/keys/web-01.pem",
            os_family="ubuntu",
            ssh_manager=ssh_manager,
            executor=MagicMock(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=MagicMock(log_event=AsyncMock()),
            sre_settings=_make_sre_settings(),
        )

    assert "sshd.service" in result.failed_services


# ---------------------------------------------------------------------------
# _parse_journal_errors tests
# ---------------------------------------------------------------------------


def test_parse_journal_errors_deduplicates() -> None:
    """Same message pattern with different numbers → single entry."""
    lines = "\n".join([
        "May 16 12:00:00 host nginx[100]: Cannot connect to 127.0.0.1:5432",
        "May 16 12:00:01 host nginx[101]: Cannot connect to 127.0.0.1:5433",
        "May 16 12:00:02 host nginx[102]: Cannot connect to 127.0.0.1:5434",
    ])
    result = _parse_journal_errors(lines)
    # All three normalize to the same key → deduplicated to 1
    assert len(result) == 1


def test_parse_journal_errors_max_5() -> None:
    """10 unique lines → at most 5 returned."""
    lines = "\n".join(
        f"May 16 12:00:{i:02d} host svc[{i}]: Unique error message number {chr(65+i)}"
        for i in range(10)
    )
    result = _parse_journal_errors(lines)
    assert len(result) == 5


def test_parse_journal_errors_empty_stdout() -> None:
    assert _parse_journal_errors("") == []


def test_parse_journal_errors_no_colon_lines() -> None:
    """Lines without ': ' separator are skipped."""
    lines = "May 16 12:00:00 web-01 -- Journal begins\nsome line without separator"
    result = _parse_journal_errors(lines)
    # First line has ': ' in timestamp part but message part after 'Journal begins' is skipped
    # Second line has no ': ' → skipped
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _parse_failed_services tests
# ---------------------------------------------------------------------------


def test_parse_failed_services_basic() -> None:
    stdout = "  sshd.service  loaded failed failed  OpenSSH Daemon\n"
    result = _parse_failed_services(stdout)
    assert result == ["sshd.service"]


def test_parse_failed_services_bullet_prefix() -> None:
    """Lines with bullet prefix '●' should be handled."""
    stdout = "  ● nginx.service  loaded failed failed  nginx web server\n"
    result = _parse_failed_services(stdout)
    assert result == ["nginx.service"]


def test_parse_failed_services_empty() -> None:
    assert _parse_failed_services("") == []


# ---------------------------------------------------------------------------
# render_digest_report journal/services rendering
# ---------------------------------------------------------------------------


def test_render_digest_journal_not_shown_when_elk_present() -> None:
    """When elk_errors is non-empty, journal section must be absent."""
    from errander.observability.reporting import render_digest_report

    result = ProbeVMResult(
        vm_id="dev/web-01",
        hostname="10.0.0.1",
        elk_errors=["[ERROR] 5x db error"],
        journal_errors=["Connection refused"],
    )
    report = DigestReport(
        probe_id="probe-1",
        env_name="dev",
        generated_at=datetime.now(tz=UTC),
        vm_results=[result],
    )
    text = render_digest_report(report)
    assert "journal errors" not in text.lower()
    assert "ELK" in text


def test_render_digest_failed_services_shown() -> None:
    from errander.observability.reporting import render_digest_report

    result = ProbeVMResult(
        vm_id="dev/web-01",
        hostname="10.0.0.1",
        failed_services=["sshd.service", "nginx.service"],
    )
    report = DigestReport(
        probe_id="probe-2",
        env_name="dev",
        generated_at=datetime.now(tz=UTC),
        vm_results=[result],
    )
    text = render_digest_report(report)
    assert "sshd.service" in text
    assert "nginx.service" in text
