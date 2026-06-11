"""Tests for --probe-now CLI and signals_cron scheduler wiring."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.main import _parse_args, run_env_probe_main
from tests.conftest import TEST_DB_URL

# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------


def test_probe_now_flag_parses() -> None:
    args = _parse_args(["--probe-now", "dev"])
    assert args.probe_now == "dev"


def test_probe_now_default_is_none() -> None:
    args = _parse_args([])
    assert args.probe_now is None


def test_probe_now_and_check_targets_are_independent_flags() -> None:
    args_probe = _parse_args(["--probe-now", "staging"])
    args_check = _parse_args(["--check-targets", "staging"])
    assert args_probe.probe_now == "staging"
    assert args_probe.check_targets is None
    assert args_check.check_targets == "staging"
    assert args_check.probe_now is None


# ---------------------------------------------------------------------------
# run_env_probe_main — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_env_probe_main_unknown_env(tmp_path: Path) -> None:
    """Returns exit code 1 when the environment is not in inventory."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  dev:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: vm-dev-01\n"
        "        os_family: ubuntu\n"
    )
    result = await run_env_probe_main(env_name="nonexistent", inventory_path=inv_yaml)
    assert result == 1


@pytest.mark.asyncio
async def test_run_env_probe_main_calls_probe_runner(tmp_path: Path) -> None:
    """run_env_probe_main exits 0 when probe succeeds and no Slack configured."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  dev:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: vm-dev-01\n"
        "        os_family: ubuntu\n"
    )

    from datetime import datetime

    from errander.models.reports import DigestReport

    fake_report = DigestReport(
        probe_id="probe-dev-20260515T060000",
        env_name="dev",
        generated_at=datetime(2026, 5, 15, 6, 0, tzinfo=UTC),
    )

    # Patch at the source modules (deferred imports inside run_env_probe_main)
    with (
        patch("errander.agent.probe.run_env_probe", new=AsyncMock(return_value=fake_report)),
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=TEST_DB_URL,
            audit_mode="best_effort",
            slack_bot_token="",
            slack_channel_id="",
            sre_signals=MagicMock(),
        )),
        patch("errander.safety.audit.AuditStore") as mock_audit_cls,
        patch("errander.safety.disk_history.VMDiskHistoryStore") as mock_disk_cls,
        patch("errander.safety.baselines.BaselineStore") as mock_base_cls,
        patch("errander.execution.ssh.SSHConnectionManager") as mock_ssh_cls,
        patch("errander.execution.sandbox.SandboxExecutor"),
    ):
        mock_audit = MagicMock()
        mock_audit.__aenter__ = AsyncMock(return_value=mock_audit)
        mock_audit.__aexit__ = AsyncMock(return_value=False)
        mock_audit_cls.return_value = mock_audit
        mock_disk = MagicMock()
        mock_disk.initialize = AsyncMock()
        mock_disk_cls.return_value = mock_disk
        mock_base = MagicMock()
        mock_base.initialize = AsyncMock()
        mock_base_cls.return_value = mock_base
        mock_ssh = MagicMock()
        mock_ssh.close_all = AsyncMock()
        mock_ssh_cls.return_value = mock_ssh

        result = await run_env_probe_main(env_name="dev", inventory_path=inv_yaml)

    assert result == 0


@pytest.mark.asyncio
async def test_run_env_probe_main_posts_to_slack_when_configured(tmp_path: Path) -> None:
    """When Slack is configured, post_digest is called with rendered text."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  dev:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: vm-dev-01\n"
        "        os_family: ubuntu\n"
    )

    from datetime import datetime

    from errander.models.reports import DigestReport

    fake_report = DigestReport(
        probe_id="probe-dev-20260515T060000",
        env_name="dev",
        generated_at=datetime(2026, 5, 15, 6, 0, tzinfo=UTC),
    )

    mock_slack = MagicMock()
    mock_slack.post_digest = AsyncMock()

    with (
        patch("errander.agent.probe.run_env_probe", new=AsyncMock(return_value=fake_report)),
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=TEST_DB_URL,
            audit_mode="best_effort",
            slack_bot_token="xoxb-test",
            slack_channel_id="C123",
            sre_signals=MagicMock(),
        )),
        patch("errander.safety.audit.AuditStore") as mock_audit_cls,
        patch("errander.safety.disk_history.VMDiskHistoryStore") as mock_disk_cls,
        patch("errander.safety.baselines.BaselineStore") as mock_base_cls,
        patch("errander.execution.ssh.SSHConnectionManager") as mock_ssh_cls,
        patch("errander.execution.sandbox.SandboxExecutor"),
        patch("errander.integrations.slack.SlackClient", return_value=mock_slack),
    ):
        mock_audit = MagicMock()
        mock_audit.__aenter__ = AsyncMock(return_value=mock_audit)
        mock_audit.__aexit__ = AsyncMock(return_value=False)
        mock_audit_cls.return_value = mock_audit
        mock_disk = MagicMock()
        mock_disk.initialize = AsyncMock()
        mock_disk_cls.return_value = mock_disk
        mock_base = MagicMock()
        mock_base.initialize = AsyncMock()
        mock_base_cls.return_value = mock_base
        mock_ssh = MagicMock()
        mock_ssh.close_all = AsyncMock()
        mock_ssh_cls.return_value = mock_ssh

        await run_env_probe_main(env_name="dev", inventory_path=inv_yaml)

    mock_slack.post_digest.assert_awaited_once()
    posted_text = mock_slack.post_digest.await_args.args[0]
    assert "dev" in posted_text


# ---------------------------------------------------------------------------
# ScheduleSchema — signals field
# ---------------------------------------------------------------------------


def test_schedule_schema_signals_field_defaults_none() -> None:
    from errander.config.schema import ScheduleSchema

    schema = ScheduleSchema()
    assert schema.signals is None


def test_schedule_schema_signals_field_accepts_cron() -> None:
    from errander.config.schema import ScheduleSchema

    schema = ScheduleSchema(signals="0 6 * * *")
    assert schema.signals == "0 6 * * *"


def test_schedule_schema_signals_and_maintenance_independent() -> None:
    from errander.config.schema import ScheduleSchema

    schema = ScheduleSchema(maintenance="0 2 * * 0", signals="0 6 * * *")
    assert schema.maintenance == "0 2 * * 0"
    assert schema.signals == "0 6 * * *"
