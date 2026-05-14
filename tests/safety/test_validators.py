"""Tests for pre-execution validators."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.config.policies import BUILTIN_POLICIES
from errander.execution.commands import AptManager, DnfManager
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import Action, ActionType, RiskTier
from errander.safety.validators import (
    LockHolder,
    parse_lock_output,
    requires_approval,
    validate_action,
    validate_no_pkg_lock,
)


class TestValidateAction:
    """Tests for validate_action()."""

    @pytest.mark.asyncio
    async def test_critical_risk_tier_blocked(self) -> None:
        action = Action(action_type=ActionType.PATCHING, risk_tier=RiskTier.CRITICAL)
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert not valid
        assert "never automated" in reason.lower()

    @pytest.mark.asyncio
    async def test_kernel_packages_blocked(self) -> None:
        action = Action(
            action_type=ActionType.PATCHING,
            risk_tier=RiskTier.MEDIUM,
            params={"packages": ["linux-image-5.15.0-generic", "curl"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert not valid
        assert "kernel" in reason.lower()

    @pytest.mark.asyncio
    async def test_kernel_devel_blocked(self) -> None:
        action = Action(
            action_type=ActionType.PATCHING,
            risk_tier=RiskTier.MEDIUM,
            params={"packages": ["kernel-devel"]},
        )
        valid, reason = await validate_action(action, "vm-01", "rhel")
        assert not valid
        assert "kernel" in reason.lower()

    @pytest.mark.asyncio
    async def test_patching_without_kernel_allowed(self) -> None:
        action = Action(
            action_type=ActionType.PATCHING,
            risk_tier=RiskTier.MEDIUM,
            params={"packages": ["curl", "nginx"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid
        assert reason == ""

    @pytest.mark.asyncio
    async def test_disk_cleanup_whitelisted_paths_allowed(self) -> None:
        action = Action(
            action_type=ActionType.DISK_CLEANUP,
            risk_tier=RiskTier.LOW,
            params={"paths": ["/tmp", "apt-cache"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid

    @pytest.mark.asyncio
    async def test_disk_cleanup_non_whitelisted_blocked(self) -> None:
        action = Action(
            action_type=ActionType.DISK_CLEANUP,
            risk_tier=RiskTier.LOW,
            params={"paths": ["/tmp", "/var/lib/postgresql"]},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert not valid
        assert "/var/lib/postgresql" in reason

    @pytest.mark.asyncio
    async def test_disk_cleanup_no_explicit_paths_allowed(self) -> None:
        """When no paths in params, whitelist check is skipped (sub-graph uses defaults)."""
        action = Action(
            action_type=ActionType.DISK_CLEANUP,
            risk_tier=RiskTier.LOW,
            params={},
        )
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid

    @pytest.mark.asyncio
    async def test_low_risk_action_allowed(self) -> None:
        action = Action(action_type=ActionType.LOG_ROTATION, risk_tier=RiskTier.LOW)
        valid, reason = await validate_action(action, "vm-01", "debian")
        assert valid
        assert reason == ""

    @pytest.mark.asyncio
    async def test_high_risk_non_critical_allowed(self) -> None:
        """HIGH risk actions pass validation — approval is a separate concern."""
        action = Action(action_type=ActionType.BACKUP_VERIFY, risk_tier=RiskTier.HIGH)
        valid, reason = await validate_action(action, "vm-01", "ubuntu")
        assert valid


class TestParseLockOutput:
    """Unit tests for parse_lock_output() — pure parsing, no I/O."""

    def test_empty_string_no_lock(self) -> None:
        assert parse_lock_output("") is None

    def test_whitespace_only_no_lock(self) -> None:
        assert parse_lock_output("   \n  ") is None

    def test_lock_with_pid_and_cmd(self) -> None:
        holder = parse_lock_output("pid=1234 cmd=apt-get")
        assert holder is not None
        assert holder.pid == 1234
        assert holder.cmd == "apt-get"

    def test_lock_pid_only(self) -> None:
        holder = parse_lock_output("pid=456")
        assert holder is not None
        assert holder.pid == 456
        assert holder.cmd is None

    def test_lock_with_unknown_cmd(self) -> None:
        holder = parse_lock_output("pid=789 cmd=unknown")
        assert holder is not None
        assert holder.pid == 789
        assert holder.cmd == "unknown"

    def test_fuser_unavailable_returns_empty_treated_as_no_lock(self) -> None:
        # fuser not installed → script produces no output → no lock
        assert parse_lock_output("") is None

    def test_extra_whitespace_around_output(self) -> None:
        holder = parse_lock_output("\n  pid=100 cmd=dpkg  \n")
        assert holder is not None
        assert holder.pid == 100
        assert holder.cmd == "dpkg"

    def test_bad_pid_skipped(self) -> None:
        holder = parse_lock_output("pid=notanumber cmd=apt-get")
        assert holder is not None
        assert holder.pid is None
        assert holder.cmd == "apt-get"

    def test_lock_holder_is_frozen(self) -> None:
        h = LockHolder(pid=1, cmd="x")
        with pytest.raises(AttributeError):
            h.pid = 2  # type: ignore[misc]


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mock")


class TestValidateNoPkgLock:
    """Integration-level tests for validate_no_pkg_lock() — stubs SSH."""

    def _executor(self) -> SandboxExecutor:
        return SandboxExecutor(SSHConnectionManager(), dry_run=True)

    async def test_clear_when_empty_output(self) -> None:
        executor = self._executor()
        with patch.object(executor, "execute", AsyncMock(return_value=_make_result(""))):
            is_clear, holder = await validate_no_pkg_lock(
                executor, "dev/web-01", "host", "user", "/key", AptManager(),
            )
        assert is_clear is True
        assert holder is None

    async def test_locked_when_output_present(self) -> None:
        executor = self._executor()
        with patch.object(
            executor, "execute", AsyncMock(return_value=_make_result("pid=1234 cmd=apt-get")),
        ):
            is_clear, holder = await validate_no_pkg_lock(
                executor, "dev/web-01", "host", "user", "/key", AptManager(),
            )
        assert is_clear is False
        assert holder is not None
        assert holder.pid == 1234
        assert holder.cmd == "apt-get"

    async def test_dnf_locked_when_output_present(self) -> None:
        executor = self._executor()
        with patch.object(
            executor, "execute", AsyncMock(return_value=_make_result("pid=5678 cmd=dnf")),
        ):
            is_clear, holder = await validate_no_pkg_lock(
                executor, "prod/db-01", "host", "user", "/key", DnfManager(),
            )
        assert is_clear is False
        assert holder is not None
        assert holder.pid == 5678

    async def test_ssh_failure_live_mode_fail_closed(self) -> None:
        executor = self._executor()
        failed = SSHResult(exit_code=1, stdout="", stderr="connection refused", command="mock")
        with patch.object(executor, "execute", AsyncMock(return_value=failed)):
            is_clear, holder = await validate_no_pkg_lock(
                executor, "dev/web-01", "host", "user", "/key", AptManager(),
                dry_run=False,
            )
        assert is_clear is False
        assert holder is not None
        assert holder.cmd == "probe-failed"

    async def test_ssh_failure_dry_run_treated_as_clear(self) -> None:
        executor = self._executor()
        failed = SSHResult(exit_code=1, stdout="", stderr="connection refused", command="mock")
        with patch.object(executor, "execute", AsyncMock(return_value=failed)):
            is_clear, holder = await validate_no_pkg_lock(
                executor, "dev/web-01", "host", "user", "/key", AptManager(),
                dry_run=True,
            )
        assert is_clear is True
        assert holder is None

    async def test_uses_dry_run_false_for_probe(self) -> None:
        executor = self._executor()
        captured: list[bool] = []

        async def _mock_execute(*args: object, **kwargs: object) -> SSHResult:
            captured.append(bool(kwargs.get("dry_run")))
            return _make_result("")

        with patch.object(executor, "execute", _mock_execute):
            await validate_no_pkg_lock(
                executor, "dev/web-01", "host", "user", "/key", AptManager(),
            )
        assert captured == [False]


class TestRequiresApproval:
    """Tests for requires_approval()."""

    def test_low_tier_relaxed_policy_no_approval(self) -> None:
        policy = BUILTIN_POLICIES["relaxed"]
        assert not requires_approval(RiskTier.LOW, policy)

    def test_medium_tier_relaxed_policy_no_approval(self) -> None:
        policy = BUILTIN_POLICIES["relaxed"]
        assert not requires_approval(RiskTier.MEDIUM, policy)

    def test_high_tier_relaxed_policy_needs_approval(self) -> None:
        policy = BUILTIN_POLICIES["relaxed"]
        assert requires_approval(RiskTier.HIGH, policy)

    def test_low_tier_moderate_policy_no_approval(self) -> None:
        policy = BUILTIN_POLICIES["moderate"]
        assert not requires_approval(RiskTier.LOW, policy)

    def test_medium_tier_moderate_policy_needs_approval(self) -> None:
        policy = BUILTIN_POLICIES["moderate"]
        assert requires_approval(RiskTier.MEDIUM, policy)

    def test_low_tier_strict_policy_needs_approval(self) -> None:
        policy = BUILTIN_POLICIES["strict"]
        assert requires_approval(RiskTier.LOW, policy)

    def test_critical_always_needs_approval(self) -> None:
        for name, policy in BUILTIN_POLICIES.items():
            assert requires_approval(RiskTier.CRITICAL, policy), f"CRITICAL should need approval under {name}"
