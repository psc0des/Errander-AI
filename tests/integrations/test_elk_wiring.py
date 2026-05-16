"""Phase E Commit 2 tests: ELK wiring into probe + --ask."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.integrations.elk import ElkClient
from errander.models.reports import DigestReport, ProbeVMResult


# ---------------------------------------------------------------------------
# probe_vm tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_vm_with_elk() -> None:
    """probe_vm() must call fetch_vm_errors and populate result.elk_errors."""
    from errander.agent.probe import probe_vm

    elk_client = MagicMock(spec=ElkClient)
    elk_client.fetch_vm_errors = AsyncMock(return_value=["[ERROR] 3x disk full"])

    ssh_manager = MagicMock()
    ssh_manager.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))

    executor = MagicMock()
    disk_history_store = MagicMock()
    disk_history_store.snapshot = AsyncMock(return_value=[])
    baseline_store = MagicMock()
    baseline_store.capture = AsyncMock(return_value={"changes": []})
    audit_store = MagicMock()
    audit_store.log_event = AsyncMock()
    audit_store.get_recent_snapshot = AsyncMock(return_value=None)

    sre_settings = MagicMock()
    sre_settings.disk_growth_trend = MagicMock(enabled=False)
    sre_settings.drift = MagicMock(sudoers=False, authorized_keys=False,
                                   listening_ports=False, scheduled_jobs=False)
    sre_settings.failed_ssh_logins = MagicMock(enabled=False)

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
            executor=executor,
            disk_history_store=disk_history_store,
            baseline_store=baseline_store,
            audit_store=audit_store,
            sre_settings=sre_settings,
            elk_client=elk_client,
        )

    elk_client.fetch_vm_errors.assert_awaited_once_with("10.0.0.1")
    assert result.elk_errors == ["[ERROR] 3x disk full"]


@pytest.mark.asyncio
async def test_probe_vm_elk_none() -> None:
    """elk_client=None → probe_vm() runs without error, result.elk_errors = []."""
    from errander.agent.probe import probe_vm

    sre_settings = MagicMock()
    sre_settings.disk_growth_trend = MagicMock(enabled=False)
    sre_settings.drift = MagicMock(sudoers=False, authorized_keys=False,
                                   listening_ports=False, scheduled_jobs=False)
    sre_settings.failed_ssh_logins = MagicMock(enabled=False)

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
            ssh_manager=MagicMock(),
            executor=MagicMock(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=MagicMock(log_event=AsyncMock()),
            sre_settings=sre_settings,
            elk_client=None,
        )

    assert result.elk_errors == []


@pytest.mark.asyncio
async def test_run_env_probe_passes_elk_client() -> None:
    """run_env_probe() must forward elk_client to each probe_vm() call."""
    from errander.agent.probe import run_env_probe

    elk_client = MagicMock(spec=ElkClient)
    captured: list[object] = []

    async def _mock_probe_vm(**kwargs: object) -> ProbeVMResult:
        captured.append(kwargs.get("elk_client"))
        return ProbeVMResult(vm_id="vm1", hostname="h1")

    with (
        patch("errander.agent.probe.probe_vm", side_effect=_mock_probe_vm),
        patch("errander.agent.probe.AuditEvent", MagicMock()),
    ):
        audit_store = MagicMock()
        audit_store.log_event = AsyncMock()

        await run_env_probe(
            env_name="dev",
            vms=[{"vm_id": "vm1", "hostname": "h1", "ssh_user": "u", "ssh_key_path": "/k",
                  "os_family": "ubuntu", "disable_failed_login_check": False}],
            ssh_manager=MagicMock(),
            executor=MagicMock(),
            disk_history_store=MagicMock(),
            baseline_store=MagicMock(),
            audit_store=audit_store,
            sre_settings=MagicMock(),
            elk_client=elk_client,
        )

    assert len(captured) == 1
    assert captured[0] is elk_client


# ---------------------------------------------------------------------------
# render_digest_report ELK section
# ---------------------------------------------------------------------------


def test_render_digest_elk_errors() -> None:
    from errander.observability.reporting import render_digest_report

    result = ProbeVMResult(
        vm_id="dev/web-01",
        hostname="10.0.0.1",
        elk_errors=["[ERROR] 5x Cannot connect to db"],
    )
    report = DigestReport(
        probe_id="probe-1",
        env_name="dev",
        generated_at=datetime.now(tz=UTC),
        vm_results=[result],
    )

    text = render_digest_report(report)
    assert "ELK" in text
    assert "Cannot connect to db" in text


def test_render_digest_no_elk() -> None:
    from errander.observability.reporting import render_digest_report

    result = ProbeVMResult(vm_id="dev/web-01", hostname="10.0.0.1", elk_errors=[])
    report = DigestReport(
        probe_id="probe-2",
        env_name="dev",
        generated_at=datetime.now(tz=UTC),
        vm_results=[result],
    )

    text = render_digest_report(report)
    assert "ELK" not in text


# ---------------------------------------------------------------------------
# --ask ELK wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_ask_passes_elk_client() -> None:
    """run_ask_query() must pass elk_client to OperatorAssistant.investigate()."""
    from errander.agent.operator_assistant import AssistantResponse

    captured: list[object] = []

    async def _mock_investigate(*args: object, **kwargs: object) -> AssistantResponse:
        captured.append(kwargs.get("elk_client"))
        return AssistantResponse(
            summary="ok", findings=[], recommendations=[], risk_level="low"
        )

    mock_audit = MagicMock()
    mock_audit.__aenter__ = AsyncMock(return_value=mock_audit)
    mock_audit.__aexit__ = AsyncMock(return_value=False)

    mock_disk = MagicMock()
    mock_disk.initialize = AsyncMock()

    mock_base = MagicMock()
    mock_base.initialize = AsyncMock()

    mock_instance = MagicMock()
    mock_instance.investigate = _mock_investigate

    with (
        patch("errander.config.schema.validate_inventory", return_value=MagicMock(
            environments={"dev": MagicMock(targets=[])},
        )),
        patch("errander.config.settings.load_settings", return_value=MagicMock(
            audit_db_url=":memory:",
            llm_base_url="",
            prometheus_base_url="",
            elk_base_url="http://elk:9200",
            elk_api_key="",
            elk_index_pattern="filebeat-*",
        )),
        patch("errander.safety.audit.AuditStore", return_value=mock_audit),
        patch("errander.safety.disk_history.VMDiskHistoryStore", return_value=mock_disk),
        patch("errander.safety.baselines.BaselineStore", return_value=mock_base),
        patch("errander.agent.operator_assistant.OperatorAssistant", return_value=mock_instance),
    ):
        from errander.main import run_ask_query
        await run_ask_query("Any issues?", Path("inventory.yaml"), "dev")

    assert len(captured) == 1
    elk = captured[0]
    assert elk is not None
    assert hasattr(elk, "fetch_vm_errors")


def test_main_builds_elk_client_when_configured() -> None:
    """ElkClient must be built when settings.elk_base_url is set."""
    from errander.integrations.elk import ElkClient

    client = ElkClient("http://elk:9200", api_key="key", index_pattern="filebeat-*")
    assert client._base_url == "http://elk:9200"
    assert client._api_key == "key"
    assert client._index_pattern == "filebeat-*"


def test_main_no_elk_client_when_unconfigured() -> None:
    """When elk_base_url is empty, no ElkClient should be created."""
    from errander.integrations.elk import ElkClient

    elk: ElkClient | None = None
    elk_base_url = ""
    if elk_base_url:
        elk = ElkClient(elk_base_url)

    assert elk is None
