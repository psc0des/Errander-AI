"""Tests for --ask CLI flag and run_ask_query() in errander/main.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.main import _parse_args, run_ask_query
from errander.models.analysis import AssistantResponse


# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------


def test_ask_flag_parses() -> None:
    args = _parse_args(["--ask", "How is the fleet?"])
    assert args.ask == "How is the fleet?"


def test_ask_default_is_none() -> None:
    args = _parse_args([])
    assert args.ask is None


def test_ask_with_env_flag() -> None:
    args = _parse_args(["--ask", "Any disk issues?", "--env", "production"])
    assert args.ask == "Any disk issues?"
    assert args.env == "production"


def test_ask_env_defaults_to_none() -> None:
    args = _parse_args(["--ask", "q"])
    assert args.env is None


def test_ask_and_probe_now_are_independent_flags() -> None:
    args_ask = _parse_args(["--ask", "q"])
    args_probe = _parse_args(["--probe-now", "dev"])
    assert args_ask.ask == "q"
    assert args_ask.probe_now is None
    assert args_probe.probe_now == "dev"
    assert args_probe.ask is None


# ---------------------------------------------------------------------------
# run_ask_query — outcomes
# ---------------------------------------------------------------------------


def _fake_response(**kwargs: object) -> AssistantResponse:
    return AssistantResponse(
        summary=str(kwargs.get("summary", "Fleet looks healthy.")),
        findings=["No issues detected"],
        recommendations=["Continue monitoring"],
        risk_level="low",
    )


@pytest.mark.asyncio
async def test_run_ask_query_exits_0(tmp_path: Path) -> None:
    """run_ask_query always returns 0 — investigation always completes."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  dev:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: vm-dev-01\n"
        "        os_family: ubuntu\n"
    )
    with (
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=":memory:",
            audit_mode="best_effort",
            llm_base_url="",
            llm_api_key="",
            llm_model="",
            llm_temperature=0.1,
        )),
        patch("errander.safety.audit.AuditStore") as mock_audit_cls,
        patch("errander.safety.disk_history.VMDiskHistoryStore") as mock_disk_cls,
        patch("errander.safety.baselines.BaselineStore") as mock_base_cls,
        patch("errander.agent.operator_assistant.OperatorAssistant.investigate",
              new=AsyncMock(return_value=_fake_response())),
    ):
        _wire_stores(mock_audit_cls, mock_disk_cls, mock_base_cls)
        result = await run_ask_query("How is the fleet?", inv_yaml, env_name=None)

    assert result == 0


@pytest.mark.asyncio
async def test_run_ask_query_prints_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Summary, findings, and recommendations are printed to stdout."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  dev:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: vm-dev-01\n"
        "        os_family: ubuntu\n"
    )
    response = AssistantResponse(
        summary="Two VMs have elevated disk usage.",
        findings=["vm-01: /data at 88%"],
        recommendations=["Schedule disk_cleanup for vm-01"],
        risk_level="medium",
    )
    with (
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=":memory:",
            audit_mode="best_effort",
            llm_base_url="",
            llm_api_key="",
            llm_model="",
            llm_temperature=0.1,
        )),
        patch("errander.safety.audit.AuditStore") as mock_audit_cls,
        patch("errander.safety.disk_history.VMDiskHistoryStore") as mock_disk_cls,
        patch("errander.safety.baselines.BaselineStore") as mock_base_cls,
        patch("errander.agent.operator_assistant.OperatorAssistant.investigate",
              new=AsyncMock(return_value=response)),
    ):
        _wire_stores(mock_audit_cls, mock_disk_cls, mock_base_cls)
        await run_ask_query("Any disk issues?", inv_yaml, env_name=None)

    out = capsys.readouterr().out
    assert "MEDIUM RISK" in out
    assert "Two VMs have elevated disk usage" in out
    assert "vm-01: /data at 88%" in out
    assert "Schedule disk_cleanup" in out


@pytest.mark.asyncio
async def test_run_ask_query_bad_inventory_returns_1(tmp_path: Path) -> None:
    """Returns exit code 1 when inventory file is invalid."""
    bad_inv = tmp_path / "bad.yaml"
    bad_inv.write_text("not: valid: inventory: yaml: [")
    result = await run_ask_query("q", bad_inv, env_name=None)
    assert result == 1


@pytest.mark.asyncio
async def test_run_ask_query_scopes_env_to_investigate(tmp_path: Path) -> None:
    """env_name is passed through to OperatorAssistant.investigate."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  prod:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: prod-vm-01\n"
        "        os_family: ubuntu\n"
    )
    captured_env: list[str | None] = []

    async def _capture_investigate(self, question, *, env_name=None, **kwargs):  # type: ignore[no-untyped-def]
        captured_env.append(env_name)
        return _fake_response()

    with (
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=":memory:",
            audit_mode="best_effort",
            llm_base_url="",
            llm_api_key="",
            llm_model="",
            llm_temperature=0.1,
        )),
        patch("errander.safety.audit.AuditStore") as mock_audit_cls,
        patch("errander.safety.disk_history.VMDiskHistoryStore") as mock_disk_cls,
        patch("errander.safety.baselines.BaselineStore") as mock_base_cls,
        patch("errander.agent.operator_assistant.OperatorAssistant.investigate", _capture_investigate),
    ):
        _wire_stores(mock_audit_cls, mock_disk_cls, mock_base_cls)
        await run_ask_query("Any issues?", inv_yaml, env_name="prod")

    assert captured_env == ["prod"]


@pytest.mark.asyncio
async def test_run_ask_query_no_llm_when_base_url_empty(tmp_path: Path) -> None:
    """When llm_base_url is empty, LLMClient is not created (llm_client=None)."""
    inv_yaml = tmp_path / "inventory.yaml"
    inv_yaml.write_text(
        "environments:\n"
        "  dev:\n"
        "    targets:\n"
        "      - host: 10.0.0.1\n"
        "        name: vm-01\n"
        "        os_family: ubuntu\n"
    )
    captured_llm: list[object] = []

    async def _capture_investigate(self, question, *, llm_client=None, **kwargs):  # type: ignore[no-untyped-def]
        captured_llm.append(llm_client)
        return _fake_response()

    with (
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=":memory:",
            audit_mode="best_effort",
            llm_base_url="",  # empty → no LLM
            llm_api_key="",
            llm_model="",
            llm_temperature=0.1,
        )),
        patch("errander.safety.audit.AuditStore") as mock_audit_cls,
        patch("errander.safety.disk_history.VMDiskHistoryStore") as mock_disk_cls,
        patch("errander.safety.baselines.BaselineStore") as mock_base_cls,
        patch("errander.agent.operator_assistant.OperatorAssistant.investigate", _capture_investigate),
    ):
        _wire_stores(mock_audit_cls, mock_disk_cls, mock_base_cls)
        await run_ask_query("q", inv_yaml, env_name=None)

    assert captured_llm == [None]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _wire_stores(
    mock_audit_cls: MagicMock,
    mock_disk_cls: MagicMock,
    mock_base_cls: MagicMock,
) -> None:
    """Wire store mocks with async context manager + initialize support."""
    audit = MagicMock()
    audit.__aenter__ = AsyncMock(return_value=audit)
    audit.__aexit__ = AsyncMock(return_value=False)
    audit.get_recent_batches = AsyncMock(return_value=[])
    audit.get_events = AsyncMock(return_value=[])
    mock_audit_cls.return_value = audit

    disk = MagicMock()
    disk.initialize = AsyncMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=[])
    mock_disk_cls.return_value = disk

    base = MagicMock()
    base.initialize = AsyncMock()
    base.latest = AsyncMock(return_value=None)
    mock_base_cls.return_value = base
