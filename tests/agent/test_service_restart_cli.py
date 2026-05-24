"""P0-1 live-mode tests for run_restart_service() CLI path.

These tests cover the Slack approval gate and subgraph invocation in live mode.
The dry-run and validation paths are already covered in tests/test_main.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from errander.main import run_restart_service

_INVENTORY = {
    "environments": {
        "production": {
            "targets": [{"host": "10.0.1.1", "name": "web-01", "os_family": "ubuntu"}],
            "actions": {
                "service_restart": {
                    "enabled": True,
                    "restartable_units": ["nginx.service", "gunicorn.service"],
                }
            },
            "ssh_user": "errander-ai",
            "ssh_key_path": "/home/errander/.ssh/id_ed25519",
        }
    }
}


def _write_inv(tmp_path: Path) -> Path:
    inv = tmp_path / "inventory.yaml"
    inv.write_text(yaml.dump(_INVENTORY))
    return inv


def _mock_audit() -> MagicMock:
    m = AsyncMock()
    m.__aenter__ = AsyncMock(return_value=m)
    m.__aexit__ = AsyncMock(return_value=None)
    m.log_event = AsyncMock()
    return m


def _mock_locker(*, acquired: bool = True) -> MagicMock:
    m = MagicMock()
    m.acquire = AsyncMock(return_value=acquired)
    m.release = AsyncMock(return_value=True)
    return m


def _mock_settings(*, has_slack: bool = True) -> MagicMock:
    s = MagicMock()
    s.audit_db_url = ":memory:"
    s.approval_timeout_seconds = 30
    s.approval_poll_interval_seconds = 1
    if has_slack:
        s.slack_bot_token = "xoxb-test-token"
        s.slack_channel_id = "C12345"
    else:
        s.slack_bot_token = None
        s.slack_channel_id = None
    return s


class TestRestartServiceLiveMode:
    @pytest.mark.asyncio
    async def test_live_no_slack_config_returns_1(self, tmp_path: Path) -> None:
        """Live mode without Slack tokens must fail fast (approval gate required)."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings(has_slack=False)),
            patch("errander.main.AuditStore", return_value=audit),
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        assert result == 1

    @pytest.mark.asyncio
    async def test_live_creates_approval_request(self, tmp_path: Path) -> None:
        """Live mode must call request_approval before doing anything."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            patch(
                "errander.safety.approval.request_approval",
                new_callable=AsyncMock, return_value="ts-123",
            ) as mock_req,
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock, return_value=(False, "timeout")),
        ):
            await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        mock_req.assert_called_once()

    @pytest.mark.asyncio
    async def test_live_rejected_returns_1(self, tmp_path: Path) -> None:
        """Slack rejection must return exit code 1 and not invoke the subgraph."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock, return_value=(False, "timeout")),
            patch("errander.agent.subgraphs.service_restart.build_service_restart_subgraph") as mock_build,
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        assert result == 1
        mock_build.assert_not_called()

    @pytest.mark.asyncio
    async def test_live_invokes_subgraph_on_approve(self, tmp_path: Path) -> None:
        """On approval, the service_restart subgraph must be invoked per VM."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()

        mock_compiled = AsyncMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "success"})
        mock_subgraph = MagicMock()
        mock_subgraph.compile.return_value = mock_compiled

        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock, return_value=(True, "operator")),
            patch("errander.main.SandboxExecutor"),
            patch("errander.main.FileLocker", return_value=_mock_locker()),
            patch(
                "errander.agent.subgraphs.service_restart.build_service_restart_subgraph",
                return_value=mock_subgraph,
            ),
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        assert result == 0
        mock_compiled.ainvoke.assert_called_once()
        call_state = mock_compiled.ainvoke.call_args[0][0]
        assert call_state["unit_name"] == "nginx.service"
        assert call_state["vm_id"] == "web-01"

    @pytest.mark.asyncio
    async def test_live_subgraph_failure_returns_nonzero(self, tmp_path: Path) -> None:
        """A failed subgraph result must cause run_restart_service to return 1."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()

        mock_compiled = AsyncMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "failed", "error": "SSH timeout"})
        mock_subgraph = MagicMock()
        mock_subgraph.compile.return_value = mock_compiled

        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock, return_value=(True, "operator")),
            patch("errander.main.SandboxExecutor"),
            patch("errander.main.FileLocker", return_value=_mock_locker()),
            patch(
                "errander.agent.subgraphs.service_restart.build_service_restart_subgraph",
                return_value=mock_subgraph,
            ),
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        assert result == 1

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_approval(self, tmp_path: Path) -> None:
        """Dry-run mode must not post to Slack or poll for approval."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock) as mock_req,
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock) as mock_poll,
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=True,
                inventory_path=inv,
            )
        assert result == 0
        mock_req.assert_not_called()
        mock_poll.assert_not_called()


class TestRestartServiceWindowAndLock:
    """Maintenance window enforcement and VM locking in run_restart_service()."""

    @pytest.mark.asyncio
    async def test_outside_window_returns_1(self, tmp_path: Path) -> None:
        """Outside maintenance window with no --force must abort before Slack."""
        from errander.scheduling.windows import MaintenanceWindow

        window = MaintenanceWindow(
            days=["saturday", "sunday"],
            start_hour=2,
            end_hour=6,
            timezone="UTC",
        )
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main._build_maintenance_window", return_value=window),
            patch("errander.main.check_window_from_config", return_value=False),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock) as mock_req,
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
                force=False,
            )
        assert result == 1
        mock_req.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_bypasses_window(self, tmp_path: Path) -> None:
        """--restart-force with a reason proceeds despite being outside window."""
        from errander.scheduling.windows import MaintenanceWindow

        window = MaintenanceWindow(
            days=["saturday", "sunday"],
            start_hour=2,
            end_hour=6,
            timezone="UTC",
        )
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main._build_maintenance_window", return_value=window),
            patch("errander.main.check_window_from_config", return_value=False),
            patch("errander.main.SlackClient"),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock, return_value=(False, "timeout")),
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
                force=True,
                force_reason="emergency: nginx OOM loop",
            )
        # Rejected at Slack (poll returns False), but window was NOT the blocker.
        assert result == 1  # rejected, not blocked by window

    @pytest.mark.asyncio
    async def test_force_without_reason_returns_1(self, tmp_path: Path) -> None:
        """--restart-force without --restart-force-reason must be rejected."""
        from errander.scheduling.windows import MaintenanceWindow

        window = MaintenanceWindow(
            days=["saturday", "sunday"],
            start_hour=2,
            end_hour=6,
            timezone="UTC",
        )
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main._build_maintenance_window", return_value=window),
            patch("errander.main.check_window_from_config", return_value=False),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock) as mock_req,
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
                force=True,
                force_reason=None,
            )
        assert result == 1
        mock_req.assert_not_called()

    @pytest.mark.asyncio
    async def test_locked_vm_skips_execution(self, tmp_path: Path) -> None:
        """If the VM lock cannot be acquired, execution is skipped and result is nonzero."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()

        mock_compiled = AsyncMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "success"})
        mock_subgraph = MagicMock()
        mock_subgraph.compile.return_value = mock_compiled

        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
            patch("errander.safety.approval.poll_approval", new_callable=AsyncMock, return_value=(True, "operator")),
            patch("errander.main.SandboxExecutor"),
            patch("errander.main.FileLocker", return_value=_mock_locker(acquired=False)),
            patch(
                "errander.agent.subgraphs.service_restart.build_service_restart_subgraph",
                return_value=mock_subgraph,
            ),
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        assert result == 1
        mock_compiled.ainvoke.assert_not_called()
