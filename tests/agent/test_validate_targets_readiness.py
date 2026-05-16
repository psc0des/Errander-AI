"""Phase F Commit 2 tests: validate_targets_node includes sudo/wrapper readiness check."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.graph import validate_targets_node
from errander.execution.target_validation import TargetReadiness
from errander.models.events import EventType  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audit_store() -> MagicMock:
    store = MagicMock()
    store.log_event = AsyncMock()
    return store


def _make_ssh_manager(os_release_stdout: str = "ID=ubuntu\nVERSION_ID=22.04\n") -> MagicMock:
    mgr = MagicMock()
    mgr.execute = AsyncMock(
        return_value=MagicMock(success=True, stdout=os_release_stdout, stderr="")
    )
    return mgr


def _target(vm_id: str = "vm-1", os_family: str = "ubuntu") -> dict[str, object]:
    return {
        "vm_id": vm_id,
        "hostname": f"10.0.0.1",
        "ssh_user": "ubuntu",
        "ssh_key_path": "/key",
        "os_family": os_family,
    }


def _readiness(verdict: str, issues: list[str] | None = None) -> TargetReadiness:
    r = TargetReadiness(vm_id="vm-1", hostname="10.0.0.1")
    r.verdict = verdict  # type: ignore[assignment]
    r.issues = issues or []
    return r


def _batch_state(targets: list[dict[str, object]]) -> dict[str, object]:
    return {
        "batch_id": "b-1",
        "targets": targets,
        "healthy_targets": [],
        "failed_targets": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_targets_blocked_vm_removed() -> None:
    """A VM with readiness verdict 'blocked' must go to failed_targets, not healthy."""
    r = _readiness("blocked", ["sudo -n not configured for /usr/bin/apt-get"])
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        result = await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    assert len(result["healthy_targets"]) == 0
    assert len(result["failed_targets"]) == 1
    assert "readiness blocked" in str(result["failed_targets"][0].get("error", ""))


@pytest.mark.asyncio
async def test_validate_targets_warning_vm_kept() -> None:
    """A VM with verdict 'warnings' stays in healthy_targets with readiness_warnings set."""
    r = _readiness("warnings", ["wrapper /usr/local/sbin/errander-docker-prune-safe missing"])
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        result = await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    assert len(result["healthy_targets"]) == 1
    assert len(result["failed_targets"]) == 0
    assert "readiness_warnings" in result["healthy_targets"][0]


@pytest.mark.asyncio
async def test_validate_targets_ready_vm_unaffected() -> None:
    """A fully ready VM has no readiness_warnings and goes to healthy_targets."""
    r = _readiness("ready")
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        result = await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    assert len(result["healthy_targets"]) == 1
    assert "readiness_warnings" not in result["healthy_targets"][0]


@pytest.mark.asyncio
async def test_validate_targets_readiness_exception_non_fatal() -> None:
    """check_target raising an exception must not remove the VM from healthy_targets."""
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(side_effect=ConnectionError("SSH timeout"))):
        result = await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    assert len(result["healthy_targets"]) == 1
    assert len(result["failed_targets"]) == 0


@pytest.mark.asyncio
async def test_validate_targets_blocked_emits_audit_event() -> None:
    """PREFLIGHT_FAILED event must be logged when a VM is readiness-blocked."""
    audit = _make_audit_store()
    r = _readiness("blocked", ["sudo -n not configured"])
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=audit,
        )

    event_types = [call.args[0].event_type for call in audit.log_event.await_args_list]
    assert EventType.TARGET_READINESS_BLOCKED in event_types


@pytest.mark.asyncio
async def test_validate_targets_all_blocked_results_in_empty_healthy() -> None:
    """All VMs blocked → healthy_targets is empty."""
    r = _readiness("blocked", ["missing sudo"])
    targets = [_target("vm-1"), _target("vm-2"), _target("vm-3")]
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        result = await validate_targets_node(
            _batch_state(targets),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    assert result["healthy_targets"] == []
    assert len(result["failed_targets"]) == 3


@pytest.mark.asyncio
async def test_validate_targets_sudo_issue_is_blocked() -> None:
    """Missing sudo access returns blocked verdict and VM goes to failed_targets."""
    r = _readiness("blocked", ["sudo -n /usr/bin/apt-get returned exit 1"])
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        result = await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    assert len(result["failed_targets"]) == 1


@pytest.mark.asyncio
async def test_validate_targets_wrapper_warning_in_metadata() -> None:
    """Docker wrapper warning issues appear in readiness_warnings on the target dict."""
    r = _readiness("warnings", ["errander-docker-prune-safe not found"])
    with patch("errander.execution.target_validation.check_target", new=AsyncMock(return_value=r)):
        result = await validate_targets_node(
            _batch_state([_target()]),
            ssh_manager=_make_ssh_manager(),
            audit_store=_make_audit_store(),
        )

    warnings = result["healthy_targets"][0].get("readiness_warnings", [])
    assert "errander-docker-prune-safe not found" in warnings
