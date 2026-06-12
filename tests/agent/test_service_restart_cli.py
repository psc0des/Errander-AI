"""P0-1 live-mode tests for run_restart_service() CLI path.

These tests cover the durable web-approval gate (R2) and subgraph invocation
in live mode. The dry-run and validation paths are already covered in
tests/test_main.py.

Approval flow under test: the CLI persists an approval_requests row, posts a
Slack notification (notify-and-link, optional), waits for the web UI's
decision, then atomically claims the approval before executing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from errander.main import run_restart_service
from tests.conftest import TEST_DB_URL

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
    s.audit_db_url = TEST_DB_URL
    s.approval_timeout_seconds = 30
    s.web_base_url = "http://10.0.0.5:9090"
    if has_slack:
        s.slack_bot_token = "xoxb-test-token"
        s.slack_channel_id = "C12345"
    else:
        s.slack_bot_token = None
        s.slack_channel_id = None
    return s


def _mock_approval_store(
    *,
    status: str = "approved",
    decided_by: str | None = "ui:operator",
    decided_by_group: str | None = "admin",
    claim_won: bool = True,
) -> AsyncMock:
    """A stand-in ApprovalRequestStore whose wait resolves immediately."""
    store = AsyncMock()
    row = MagicMock()
    row.status = status
    row.decided_by = decided_by
    row.decided_by_group = decided_by_group
    store.create = AsyncMock(return_value=row)
    store.wait_for_decision = AsyncMock(return_value=row)
    store.mark_execution_started = AsyncMock(return_value=claim_won)
    store.set_slack_ts = AsyncMock()
    store.close = AsyncMock()
    return store


def _patch_store(store: AsyncMock):
    return patch(
        "errander.safety.approval_store.ApprovalRequestStore", return_value=store,
    )


class TestRestartServiceLiveMode:
    @pytest.mark.asyncio
    async def test_live_without_slack_still_waits_on_web_approval(self, tmp_path: Path) -> None:
        """R2: Slack is notify-only — its absence must not block the web gate."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store(status="timeout", decided_by=None)
        with (
            patch("errander.main.load_settings", return_value=_mock_settings(has_slack=False)),
            patch("errander.main.AuditStore", return_value=audit),
            _patch_store(store),
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        assert result == 1  # timed out, but the durable gate ran
        store.create.assert_awaited_once()
        store.wait_for_decision.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_live_persists_request_and_notifies_slack(self, tmp_path: Path) -> None:
        """Live mode persists the approval row first, then notifies Slack."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store(status="rejected")
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch(
                "errander.safety.approval.request_approval",
                new_callable=AsyncMock, return_value="ts-123",
            ) as mock_req,
        ):
            await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=False,
                inventory_path=inv,
            )
        store.create.assert_awaited_once()
        mock_req.assert_called_once()
        store.set_slack_ts.assert_awaited_once_with(
            store.create.call_args[0][0], "ts-123",
        )

    @pytest.mark.asyncio
    async def test_live_rejected_returns_1(self, tmp_path: Path) -> None:
        """A web rejection must return exit code 1 and not invoke the subgraph."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store(status="rejected", decided_by="ui:viewer")
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
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
        store.mark_execution_started.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_invokes_subgraph_on_approve(self, tmp_path: Path) -> None:
        """On web approval, the service_restart subgraph must be invoked per VM."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store()

        mock_compiled = AsyncMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "success"})
        mock_subgraph = MagicMock()
        mock_subgraph.compile.return_value = mock_compiled

        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
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
        store.mark_execution_started.assert_awaited_once()
        mock_compiled.ainvoke.assert_called_once()
        call_state = mock_compiled.ainvoke.call_args[0][0]
        assert call_state["unit_name"] == "nginx.service"
        assert call_state["vm_id"] == "web-01"

    @pytest.mark.asyncio
    async def test_lost_execution_claim_aborts(self, tmp_path: Path) -> None:
        """If another executor claimed the approval, the CLI must not execute."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store(claim_won=False)
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
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
    async def test_live_subgraph_failure_returns_nonzero(self, tmp_path: Path) -> None:
        """A failed subgraph result must cause run_restart_service to return 1."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store()

        mock_compiled = AsyncMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "failed", "error": "SSH timeout"})
        mock_subgraph = MagicMock()
        mock_subgraph.compile.return_value = mock_compiled

        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
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
    async def test_dry_run_does_not_create_approval(self, tmp_path: Path) -> None:
        """Dry-run mode must not persist an approval request or notify Slack."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock) as mock_req,
        ):
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=True,
                inventory_path=inv,
            )
        assert result == 0
        store.create.assert_not_awaited()
        mock_req.assert_not_called()


class TestRestartServiceWindowAndLock:
    """Maintenance window enforcement and VM locking in run_restart_service()."""

    @pytest.mark.asyncio
    async def test_outside_window_returns_1(self, tmp_path: Path) -> None:
        """Outside maintenance window with no --force must abort before approval."""
        from errander.scheduling.windows import MaintenanceWindow

        window = MaintenanceWindow(
            days=["saturday", "sunday"],
            start_hour=2,
            end_hour=6,
            timezone="UTC",
        )
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main._build_maintenance_window", return_value=window),
            patch("errander.main.check_window_from_config", return_value=False),
            _patch_store(store),
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
        store.create.assert_not_awaited()

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
        store = _mock_approval_store(status="timeout", decided_by=None)
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main._build_maintenance_window", return_value=window),
            patch("errander.main.check_window_from_config", return_value=False),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
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
        # Timed out at the approval gate, but window was NOT the blocker.
        assert result == 1
        store.create.assert_awaited_once()

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
        store = _mock_approval_store()
        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main._build_maintenance_window", return_value=window),
            patch("errander.main.check_window_from_config", return_value=False),
            _patch_store(store),
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
        store.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_locked_vm_skips_execution(self, tmp_path: Path) -> None:
        """If the VM lock cannot be acquired, execution is skipped and result is nonzero."""
        inv = _write_inv(tmp_path)
        audit = _mock_audit()
        store = _mock_approval_store()

        mock_compiled = AsyncMock()
        mock_compiled.ainvoke = AsyncMock(return_value={"status": "success"})
        mock_subgraph = MagicMock()
        mock_subgraph.compile.return_value = mock_compiled

        with (
            patch("errander.main.load_settings", return_value=_mock_settings()),
            patch("errander.main.AuditStore", return_value=audit),
            patch("errander.main.SlackClient"),
            _patch_store(store),
            patch("errander.safety.approval.request_approval", new_callable=AsyncMock, return_value="ts-123"),
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
