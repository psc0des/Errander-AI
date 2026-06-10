"""Tests for main.py — CLI parsing and helper functions."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from sqlalchemy import text

from errander.config.schema import EnvironmentSchema, TargetSchema
from errander.db.core import AsyncDatabase
from errander.main import _build_maintenance_window, _parse_args, run_restart_service

# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self) -> None:
        args = _parse_args([])
        assert args.config == Path("settings.yaml")
        assert args.inventory == Path("inventory.yaml")
        assert args.run_now is False
        assert args.dry_run is True
        assert args.live is False
        assert args.force is False
        assert args.force_reason == ""

    def test_run_now_with_env(self) -> None:
        args = _parse_args(["--run-now", "--env", "production"])
        assert args.run_now is True
        assert args.env == "production"

    def test_live_flag(self) -> None:
        args = _parse_args(["--live"])
        assert args.live is True

    def test_force_with_reason(self) -> None:
        args = _parse_args(["--force", "--force-reason", "emergency patch"])
        assert args.force is True
        assert args.force_reason == "emergency patch"

    def test_custom_config_and_inventory(self) -> None:
        args = _parse_args(["--config", "prod/settings.yaml", "--inventory", "prod/inventory.yaml"])
        assert args.config == Path("prod/settings.yaml")
        assert args.inventory == Path("prod/inventory.yaml")

    def test_log_level(self) -> None:
        args = _parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# _build_maintenance_window
# ---------------------------------------------------------------------------

def _make_env(
    window: str | None = "02:00-06:00",
    days: list[str] | None = None,
    timezone: str = "UTC",
) -> EnvironmentSchema:
    target = TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")
    return EnvironmentSchema(
        maintenance_window=window,
        maintenance_days=days if days is not None else ["tuesday", "thursday"],
        maintenance_timezone=timezone,
        targets=[target],
    )


class TestBuildMaintenanceWindow:
    def test_returns_window_when_configured(self) -> None:
        env = _make_env(window="02:00-06:00", days=["monday", "tuesday"])
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.start_hour == 2
        assert w.end_hour == 6
        assert w.days == ["monday", "tuesday"]
        assert w.timezone == "UTC"

    def test_returns_none_when_no_window(self) -> None:
        env = _make_env(window=None)
        assert _build_maintenance_window(env) is None

    def test_returns_none_when_no_days(self) -> None:
        env = _make_env(days=[])
        assert _build_maintenance_window(env) is None

    def test_overnight_window(self) -> None:
        env = _make_env(window="23:00-03:00", days=["saturday", "sunday"])
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.start_hour == 23
        assert w.end_hour == 3

    def test_custom_timezone(self) -> None:
        env = _make_env(window="02:00-06:00", timezone="Australia/Sydney")
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.timezone == "Australia/Sydney"

    def test_returns_none_on_malformed_window_string(self) -> None:
        env = _make_env(window="not-a-window")
        # Should not raise — returns None
        result = _build_maintenance_window(env)
        assert result is None

    def test_parses_hhmm_format(self) -> None:
        env = _make_env(window="02:30-06:45")  # minutes are ignored, only hours used
        w = _build_maintenance_window(env)
        assert w is not None
        assert w.start_hour == 2
        assert w.end_hour == 6


# ---------------------------------------------------------------------------
# async_main — --run-now mode
# ---------------------------------------------------------------------------

class TestAsyncMainRunNow:
    @pytest.mark.asyncio
    async def test_missing_env_flag_returns_error(self, tmp_path: Path) -> None:
        """--run-now without --env should return exit code 1."""
        from errander.main import async_main

        # Create a minimal inventory
        inventory_file = tmp_path / "inventory.yaml"
        inventory_file.write_text(
            "environments:\n"
            "  dev:\n"
            "    targets:\n"
            "      - host: 10.0.1.1\n"
            "        name: web-01\n"
            "        os_family: ubuntu\n"
        )

        args = _parse_args([
            "--run-now",
            "--inventory", str(inventory_file),
            "--config", str(tmp_path / "nonexistent.yaml"),
        ])
        # --env not provided
        args.env = None

        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_unknown_env_returns_error(self, tmp_path: Path) -> None:
        """--run-now with unknown env name should return exit code 1."""
        from errander.main import async_main

        inventory_file = tmp_path / "inventory.yaml"
        inventory_file.write_text(
            "environments:\n"
            "  dev:\n"
            "    targets:\n"
            "      - host: 10.0.1.1\n"
            "        name: web-01\n"
            "        os_family: ubuntu\n"
        )

        args = _parse_args([
            "--run-now", "--env", "nonexistent",
            "--inventory", str(inventory_file),
            "--config", str(tmp_path / "nonexistent.yaml"),
        ])

        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_missing_inventory_returns_error(self, tmp_path: Path) -> None:
        """Missing inventory file should return exit code 1."""
        from errander.main import async_main

        args = _parse_args([
            "--run-now", "--env", "dev",
            "--inventory", str(tmp_path / "missing.yaml"),
        ])

        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_force_without_reason_returns_error(self, tmp_path: Path) -> None:
        """--force without --force-reason should return exit code 1."""
        from errander.main import async_main

        inventory_file = tmp_path / "inventory.yaml"
        inventory_file.write_text(
            "environments:\n"
            "  dev:\n"
            "    targets:\n"
            "      - host: 10.0.1.1\n"
            "        name: web-01\n"
            "        os_family: ubuntu\n"
        )

        args = _parse_args([
            "--run-now", "--env", "dev",
            "--inventory", str(inventory_file),
            "--force",
        ])

        result = await async_main(args)
        assert result == 1


# ---------------------------------------------------------------------------
# _window_opener
# ---------------------------------------------------------------------------

class TestWindowOpener:
    @pytest.mark.asyncio
    async def test_no_pending_skips_run_env_batch(self, tmp_path: Path) -> None:
        """When no pending deferred records, run_env_batch is not called."""
        from unittest.mock import AsyncMock, patch

        from errander.config.schema import EnvironmentSchema, TargetSchema
        from errander.config.settings import Settings
        from errander.execution.sandbox import SandboxExecutor
        from errander.execution.ssh import SSHConnectionManager
        from errander.main import _window_opener
        from errander.safety.approval import ApprovalManager
        from errander.safety.audit import AuditStore
        from errander.safety.deferred import DeferredExecutionStore
        from errander.safety.locking import FileLocker
        from errander.safety.overrides import OverridesStore

        target = TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")
        env_schema = EnvironmentSchema(
            maintenance_window="02:00-06:00",
            maintenance_days=["monday"],
            targets=[target],
        )

        deferred_store = DeferredExecutionStore(AsyncDatabase(":memory:"))
        await deferred_store.initialize()

        async with AuditStore(AsyncDatabase(":memory:")) as audit_store:
            overrides_store = OverridesStore(AsyncDatabase(":memory:"))
            await overrides_store.initialize()
            try:
                with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
                    await _window_opener(
                        env_name="dev",
                        env_schema=env_schema,
                        settings=Settings(),
                        executor=SandboxExecutor(SSHConnectionManager(), dry_run=True),
                        locker=FileLocker(lock_dir=tmp_path),
                        ssh_manager=SSHConnectionManager(),
                        audit_store=audit_store,
                        deferred_store=deferred_store,
                        approval_manager=ApprovalManager(),
                        slack_client=None,
                        overrides_store=overrides_store,
                    )
                mock_run.assert_not_awaited()
            finally:
                await deferred_store.close()
                await overrides_store.close()

    @pytest.mark.asyncio
    async def test_pending_record_triggers_live_run(self, tmp_path: Path) -> None:
        """When a pending deferred record exists, run_env_batch is called with dry_run=False."""
        from datetime import datetime, timedelta
        from unittest.mock import AsyncMock, patch

        from errander.config.schema import EnvironmentSchema, TargetSchema
        from errander.config.settings import Settings
        from errander.execution.sandbox import SandboxExecutor
        from errander.execution.ssh import SSHConnectionManager
        from errander.main import _window_opener
        from errander.safety.approval import ApprovalManager
        from errander.safety.audit import AuditStore
        from errander.safety.deferred import DeferredExecutionStore
        from errander.safety.locking import FileLocker
        from errander.safety.overrides import OverridesStore

        target = TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")
        env_schema = EnvironmentSchema(
            maintenance_window="02:00-06:00",
            maintenance_days=["monday"],
            targets=[target],
        )

        deferred_store = DeferredExecutionStore(AsyncDatabase(":memory:"))
        await deferred_store.initialize()
        future_window = datetime.now(tz=UTC).replace(hour=2, minute=0, second=0, microsecond=0) + timedelta(days=30)
        await deferred_store.save("b-test", "dev", "alice", future_window)

        async with AuditStore(AsyncDatabase(":memory:")) as audit_store:
            overrides_store = OverridesStore(AsyncDatabase(":memory:"))
            await overrides_store.initialize()
            try:
                with patch("errander.main.run_env_batch", new_callable=AsyncMock) as mock_run:
                    await _window_opener(
                        env_name="dev",
                        env_schema=env_schema,
                        settings=Settings(),
                        executor=SandboxExecutor(SSHConnectionManager(), dry_run=True),
                        locker=FileLocker(lock_dir=tmp_path),
                        ssh_manager=SSHConnectionManager(),
                        audit_store=audit_store,
                        deferred_store=deferred_store,
                        approval_manager=ApprovalManager(),
                        slack_client=None,
                        overrides_store=overrides_store,
                    )

                mock_run.assert_awaited_once()
                call_kwargs = mock_run.call_args.kwargs
                assert call_kwargs["dry_run"] is False
                assert call_kwargs["force"] is True
            finally:
                await deferred_store.close()
                await overrides_store.close()

    @pytest.mark.asyncio
    async def test_pending_record_marked_done_after_run(self, tmp_path: Path) -> None:
        """After _window_opener runs, the deferred record is marked done."""
        from datetime import datetime, timedelta
        from unittest.mock import AsyncMock, patch

        from errander.config.schema import EnvironmentSchema, TargetSchema
        from errander.config.settings import Settings
        from errander.execution.sandbox import SandboxExecutor
        from errander.execution.ssh import SSHConnectionManager
        from errander.main import _window_opener
        from errander.safety.approval import ApprovalManager
        from errander.safety.audit import AuditStore
        from errander.safety.deferred import DeferredExecutionStore
        from errander.safety.locking import FileLocker
        from errander.safety.overrides import OverridesStore

        target = TargetSchema(host="10.0.1.1", name="web-01", os_family="ubuntu")
        env_schema = EnvironmentSchema(
            maintenance_window="02:00-06:00",
            maintenance_days=["monday"],
            targets=[target],
        )

        deferred_store = DeferredExecutionStore(AsyncDatabase(":memory:"))
        await deferred_store.initialize()
        future_window = datetime.now(tz=UTC).replace(hour=2, minute=0, second=0, microsecond=0) + timedelta(days=30)
        await deferred_store.save("b-test", "dev", "alice", future_window)

        async with AuditStore(AsyncDatabase(":memory:")) as audit_store:
            overrides_store = OverridesStore(AsyncDatabase(":memory:"))
            await overrides_store.initialize()
            try:
                with patch("errander.main.run_env_batch", new_callable=AsyncMock):
                    await _window_opener(
                        env_name="dev",
                        env_schema=env_schema,
                        settings=Settings(),
                        executor=SandboxExecutor(SSHConnectionManager(), dry_run=True),
                        locker=FileLocker(lock_dir=tmp_path),
                        ssh_manager=SSHConnectionManager(),
                        audit_store=audit_store,
                        deferred_store=deferred_store,
                        approval_manager=ApprovalManager(),
                        slack_client=None,
                        overrides_store=overrides_store,
                    )

                assert deferred_store._db is not None
                async with deferred_store._db.begin() as conn:
                    result = await conn.execute(
                        text("SELECT status FROM deferred_executions WHERE batch_id = :bid"),
                        {"bid": "b-test"},
                    )
                    row = result.mappings().fetchone()
                assert row is not None
                assert row["status"] == "done"
            finally:
                await deferred_store.close()
                await overrides_store.close()


# ---------------------------------------------------------------------------
# --check-targets CLI
# ---------------------------------------------------------------------------

class TestCheckTargetsArg:
    def test_check_targets_flag_parses(self) -> None:
        args = _parse_args(["--check-targets", "production"])
        assert args.check_targets == "production"

    def test_check_targets_default_none(self) -> None:
        args = _parse_args([])
        assert args.check_targets is None

    def test_migrate_inventory_default_none(self) -> None:
        args = _parse_args([])
        assert args.migrate_inventory is None

    def test_migrate_inventory_flag_accepted(self, tmp_path: Path) -> None:
        args = _parse_args(["--migrate-inventory", str(tmp_path / "inventory.yaml")])
        assert args.migrate_inventory == str(tmp_path / "inventory.yaml")


class TestMigrateInventoryCLI:
    @pytest.mark.asyncio
    async def test_migrate_exits_0_on_success(self, tmp_path: Path) -> None:
        import yaml

        from errander.main import async_main

        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump({
            "environments": {
                "dev": {
                    "docker_command_mode": "disabled",
                    "targets": [{"host": "10.0.0.1", "name": "dev-01", "os_family": "ubuntu"}],
                },
            },
        }))

        args = _parse_args(["--migrate-inventory", str(inv)])
        result = await async_main(args)
        assert result == 0

    @pytest.mark.asyncio
    async def test_migrate_exits_1_when_file_missing(self, tmp_path: Path) -> None:
        from errander.main import async_main

        args = _parse_args(["--migrate-inventory", str(tmp_path / "nonexistent.yaml")])
        result = await async_main(args)
        assert result == 1


class TestCheckTargetsRegistryDriven:
    """--check-targets uses manifest to filter by enabled actions (commit 1.3)."""

    @pytest.mark.asyncio
    async def test_check_targets_passes_disabled_docker_mode_when_docker_disabled(self, tmp_path: Path) -> None:
        import yaml

        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump({
            "environments": {
                "dev": {
                    "targets": [{"host": "10.0.0.1", "name": "dev-01", "os_family": "ubuntu"}],
                    "actions": {"docker_hygiene": {"enabled": False, "command_mode": "disabled"}},
                },
            },
        }))

        ready = TargetReadiness(vm_id="dev-01", hostname="10.0.0.1", verdict="ready")

        with (
            patch("errander.execution.target_validation.check_target", return_value=ready) as mock_check,
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
        ):
            result = await run_check_targets(env_name="dev", inventory_path=inv)

        assert result == 0
        call_kwargs = mock_check.call_args.kwargs
        assert call_kwargs.get("docker_command_mode") == "disabled"

    @pytest.mark.asyncio
    async def test_check_targets_warns_but_reports_when_binary_missing(self, tmp_path: Path) -> None:
        import yaml

        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump({
            "environments": {
                "dev": {
                    "targets": [{"host": "10.0.0.1", "name": "dev-01", "os_family": "ubuntu"}],
                },
            },
        }))

        blocked = TargetReadiness(
            vm_id="dev-01", hostname="10.0.0.1", verdict="blocked",
            issues=["missing binary: /usr/bin/journalctl"],
        )

        with (
            patch("errander.execution.target_validation.check_target", return_value=blocked),
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
        ):
            result = await run_check_targets(env_name="dev", inventory_path=inv)

        # exits 1 — signals not fully ready, but the check completed and reported
        assert result == 1


class TestRunCheckTargets:
    @pytest.mark.asyncio
    async def test_check_targets_exits_0_when_all_ready(self, tmp_path: Path) -> None:
        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(
            "environments:\n"
            "  dev:\n"
            "    ssh_user: u\n"
            "    ssh_key_path: /key\n"
            "    approval_policy: relaxed\n"
            "    actions:\n"
            "      docker_hygiene:\n"
            "        enabled: false\n"
            "        command_mode: disabled\n"
            "    targets:\n"
            "      - host: 10.0.0.1\n"
            "        name: dev-01\n"
            "        os_family: ubuntu\n"
        )

        ready = TargetReadiness(vm_id="dev-01", hostname="10.0.0.1", verdict="ready")

        with (
            patch("errander.execution.target_validation.check_target", return_value=ready),
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
        ):
            result = await run_check_targets(env_name="dev", inventory_path=inv)

        assert result == 0

    @pytest.mark.asyncio
    async def test_check_targets_exits_1_when_any_blocked(self, tmp_path: Path) -> None:
        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(
            "environments:\n"
            "  dev:\n"
            "    ssh_user: u\n"
            "    ssh_key_path: /key\n"
            "    approval_policy: relaxed\n"
            "    actions:\n"
            "      docker_hygiene:\n"
            "        enabled: false\n"
            "        command_mode: disabled\n"
            "    targets:\n"
            "      - host: 10.0.0.1\n"
            "        name: dev-01\n"
            "        os_family: ubuntu\n"
        )

        blocked = TargetReadiness(
            vm_id="dev-01", hostname="10.0.0.1", verdict="blocked",
            issues=["missing binary: /usr/bin/apt-get"],
        )

        with (
            patch("errander.execution.target_validation.check_target", return_value=blocked),
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
        ):
            result = await run_check_targets(env_name="dev", inventory_path=inv)

        assert result == 1

    @pytest.mark.asyncio
    async def test_check_targets_unknown_env(self, tmp_path: Path) -> None:
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(
            "environments:\n"
            "  dev:\n"
            "    ssh_user: u\n"
            "    ssh_key_path: /key\n"
            "    approval_policy: relaxed\n"
            "    targets:\n"
            "      - host: 10.0.0.1\n"
            "        name: dev-01\n"
            "        os_family: ubuntu\n"
        )
        result = await run_check_targets(env_name="nonexistent", inventory_path=inv)
        assert result == 1


# ---------------------------------------------------------------------------
# --restart-service CLI
# ---------------------------------------------------------------------------

_SR_INVENTORY = {
    "environments": {
        "production": {
            "targets": [{"host": "10.0.1.1", "name": "web-01", "os_family": "ubuntu"}],
            "actions": {"service_restart": {
                "enabled": True, "restartable_units": ["nginx.service", "gunicorn.service"],
            }},
        }
    }
}


class TestRestartServiceArgs:
    def test_restart_service_flag_parses(self) -> None:
        args = _parse_args(["--restart-service", "production", "--unit", "nginx", "--vm", "web-01"])
        assert args.restart_service == "production"
        assert args.unit == "nginx"
        assert args.vm == "web-01"
        assert args.vms is None

    def test_restart_service_multi_vm_flag(self) -> None:
        args = _parse_args(["--restart-service", "prod", "--unit", "nginx", "--vms", "web-01,web-02"])
        assert args.restart_service == "prod"
        assert args.vms == "web-01,web-02"
        assert args.vm is None

    def test_restart_service_default_none(self) -> None:
        args = _parse_args([])
        assert args.restart_service is None
        assert args.unit is None
        assert args.vm is None
        assert args.vms is None


class TestRestartServiceCLI:
    def _write_inventory(self, tmp_path: Path, data: object = None) -> Path:
        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump(data if data is not None else _SR_INVENTORY))
        return inv

    @pytest.mark.asyncio
    async def test_dry_run_returns_0(self, tmp_path: Path) -> None:
        inv = self._write_inventory(tmp_path)
        mock_audit = AsyncMock()
        mock_audit.__aenter__ = AsyncMock(return_value=mock_audit)
        mock_audit.__aexit__ = AsyncMock(return_value=None)
        mock_audit.log_event = AsyncMock()
        with (
            patch("errander.main.load_settings") as mock_settings,
            patch("errander.main.AuditStore", return_value=mock_audit),
        ):
            mock_settings.return_value = MagicMock(audit_db_url=":memory:")
            result = await run_restart_service(
                env_name="production",
                unit_name="nginx.service",
                vm_ids=["web-01"],
                dry_run=True,
                inventory_path=inv,
            )
        assert result == 0

    @pytest.mark.asyncio
    async def test_unknown_env_returns_1(self, tmp_path: Path) -> None:
        inv = self._write_inventory(tmp_path)
        result = await run_restart_service(
            env_name="nonexistent",
            unit_name="nginx",
            vm_ids=["web-01"],
            dry_run=True,
            inventory_path=inv,
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_unit_not_in_allowlist_returns_1(self, tmp_path: Path) -> None:
        inv = self._write_inventory(tmp_path)
        result = await run_restart_service(
            env_name="production",
            unit_name="redis-server",  # not in restartable_units
            vm_ids=["web-01"],
            dry_run=True,
            inventory_path=inv,
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_unknown_vm_returns_1(self, tmp_path: Path) -> None:
        inv = self._write_inventory(tmp_path)
        result = await run_restart_service(
            env_name="production",
            unit_name="nginx.service",
            vm_ids=["nonexistent-vm"],
            dry_run=True,
            inventory_path=inv,
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_service_restart_disabled_returns_1(self, tmp_path: Path) -> None:
        inv = self._write_inventory(tmp_path, {
            "environments": {
                "staging": {
                    "targets": [{"host": "10.0.2.1", "name": "stg-01", "os_family": "ubuntu"}],
                    "actions": {"service_restart": {"enabled": False}},
                }
            }
        })
        result = await run_restart_service(
            env_name="staging",
            unit_name="nginx",
            vm_ids=["stg-01"],
            dry_run=True,
            inventory_path=inv,
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_async_main_no_vm_or_vms_returns_1(self, tmp_path: Path) -> None:
        from errander.main import async_main

        inv = self._write_inventory(tmp_path)
        args = _parse_args([
            "--restart-service", "production",
            "--unit", "nginx",
            "--inventory", str(inv),
        ])
        result = await async_main(args)
        assert result == 1

    @pytest.mark.asyncio
    async def test_async_main_no_unit_returns_1(self, tmp_path: Path) -> None:
        from errander.main import async_main

        inv = self._write_inventory(tmp_path)
        args = _parse_args([
            "--restart-service", "production",
            "--vm", "web-01",
            "--inventory", str(inv),
        ])
        result = await async_main(args)
        assert result == 1


# ---------------------------------------------------------------------------
# --check-targets allowlist drift
# ---------------------------------------------------------------------------

class TestCheckTargetsAllowlistDrift:
    @pytest.mark.asyncio
    async def test_allowlist_drift_prints_missing_and_extra(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from errander.execution.ssh import SSHResult
        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump({
            "environments": {
                "production": {
                    "targets": [{"host": "10.0.1.1", "name": "web-01", "os_family": "ubuntu"}],
                    "actions": {
                        "service_restart": {
                            "enabled": True,
                            "restartable_units": ["nginx.service", "gunicorn.service"],
                        },
                    },
                }
            }
        }))

        ready = TargetReadiness(vm_id="web-01", hostname="10.0.1.1", verdict="ready")
        # On-target allowlist: has "nginx.service" and "redis.service" but NOT "gunicorn.service"
        allowlist_result = SSHResult(
            exit_code=0,
            stdout="nginx.service\nredis.service\n",
            stderr="",
            command="cat /etc/errander/restart-allowlist 2>/dev/null || echo '__not_found__'",
        )

        with (
            patch("errander.execution.target_validation.check_target", return_value=ready),
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
            patch(
                "errander.execution.ssh.SSHConnectionManager.execute",
                new_callable=AsyncMock,
                return_value=allowlist_result,
            ),
        ):
            result = await run_check_targets(env_name="production", inventory_path=inv)

        captured = capsys.readouterr()
        assert "gunicorn" in captured.out
        assert "redis" in captured.out
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_drift_when_allowlist_matches(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from errander.execution.ssh import SSHResult
        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump({
            "environments": {
                "production": {
                    "targets": [{"host": "10.0.1.1", "name": "web-01", "os_family": "ubuntu"}],
                    "actions": {
                        "service_restart": {
                            "enabled": True,
                            "restartable_units": ["nginx.service", "gunicorn.service"],
                        },
                    },
                }
            }
        }))

        ready = TargetReadiness(vm_id="web-01", hostname="10.0.1.1", verdict="ready")
        allowlist_result = SSHResult(
            exit_code=0,
            stdout="nginx.service\ngunicorn.service\n",
            stderr="",
            command="cat /etc/errander/restart-allowlist 2>/dev/null || echo '__not_found__'",
        )

        with (
            patch("errander.execution.target_validation.check_target", return_value=ready),
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
            patch(
                "errander.execution.ssh.SSHConnectionManager.execute",
                new_callable=AsyncMock,
                return_value=allowlist_result,
            ),
        ):
            result = await run_check_targets(env_name="production", inventory_path=inv)

        captured = capsys.readouterr()
        assert "ALLOWLIST DRIFT" not in captured.out
        assert "ALLOWLIST OK" in captured.out
        assert "nginx.service" in captured.out
        assert "gunicorn.service" in captured.out
        assert result == 0

    @pytest.mark.asyncio
    async def test_service_restart_disabled_skips_allowlist_check(
        self, tmp_path: Path
    ) -> None:
        from errander.execution.target_validation import TargetReadiness
        from errander.main import run_check_targets

        inv = tmp_path / "inventory.yaml"
        inv.write_text(yaml.dump({
            "environments": {
                "dev": {
                    "targets": [{"host": "10.0.2.1", "name": "dev-01", "os_family": "ubuntu"}],
                    "actions": {"service_restart": {"enabled": False}},
                }
            }
        }))

        ready = TargetReadiness(vm_id="dev-01", hostname="10.0.2.1", verdict="ready")

        with (
            patch("errander.execution.target_validation.check_target", return_value=ready),
            patch("errander.execution.ssh.SSHConnectionManager.close_all", new_callable=AsyncMock),
            patch(
                "errander.execution.ssh.SSHConnectionManager.execute",
                new_callable=AsyncMock,
                side_effect=AssertionError("allowlist SSH called when service_restart disabled"),
            ),
        ):
            result = await run_check_targets(env_name="dev", inventory_path=inv)

        assert result == 0
