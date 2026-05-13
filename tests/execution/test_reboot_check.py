"""Tests for reboot-required detection (PR-1.2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.reboot_check import (
    RebootStatus,
    detect_reboot_required,
    parse_reboot_status,
    reboot_required_command,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


# --- reboot_required_command ---

class TestRebootRequiredCommand:
    def test_debian_checks_flag_file(self) -> None:
        cmd = reboot_required_command("debian")
        assert "/var/run/reboot-required" in cmd
        assert "REBOOT=1" in cmd
        assert "REBOOT=0" in cmd

    def test_ubuntu_checks_flag_file(self) -> None:
        cmd = reboot_required_command("ubuntu")
        assert "/var/run/reboot-required" in cmd

    def test_ubuntu_cats_pkgs_file(self) -> None:
        cmd = reboot_required_command("ubuntu")
        assert "/var/run/reboot-required.pkgs" in cmd

    def test_rhel_uses_needs_restarting(self) -> None:
        cmd = reboot_required_command("rhel")
        assert "needs-restarting" in cmd
        assert "EXIT=$?" in cmd

    def test_rhel_handles_absent_binary(self) -> None:
        cmd = reboot_required_command("rhel")
        assert "EXIT=unknown" in cmd

    def test_centos_uses_rhel_path(self) -> None:
        cmd = reboot_required_command("centos")
        assert "needs-restarting" in cmd

    def test_command_always_exits_zero(self) -> None:
        # Both branches must exit 0 so SSH success check is never triggered by probe
        for os_family in ("ubuntu", "debian", "rhel"):
            cmd = reboot_required_command(os_family)
            assert "exit 1" not in cmd.lower()


# --- parse_reboot_status: Debian/Ubuntu ---

class TestParseRebootStatusDebian:
    def test_reboot_needed_no_pkgs(self) -> None:
        stdout = "REBOOT=1\n"
        status = parse_reboot_status(stdout, "ubuntu")
        assert status.needs_reboot is True
        assert status.pkgs_requiring == ()

    def test_reboot_needed_with_pkgs(self) -> None:
        stdout = "REBOOT=1\nlinux-base\nlibc6\n"
        status = parse_reboot_status(stdout, "ubuntu")
        assert status.needs_reboot is True
        assert "linux-base" in status.pkgs_requiring
        assert "libc6" in status.pkgs_requiring

    def test_no_reboot_needed(self) -> None:
        stdout = "REBOOT=0\n"
        status = parse_reboot_status(stdout, "debian")
        assert status.needs_reboot is False
        assert status.reason is None
        assert status.pkgs_requiring == ()

    def test_empty_output_no_reboot(self) -> None:
        status = parse_reboot_status("", "ubuntu")
        assert status.needs_reboot is False

    def test_whitespace_only_no_reboot(self) -> None:
        status = parse_reboot_status("   \n  \n", "ubuntu")
        assert status.needs_reboot is False

    def test_reason_set_when_reboot_needed(self) -> None:
        status = parse_reboot_status("REBOOT=1\n", "ubuntu")
        assert status.reason is not None
        assert len(status.reason) > 0

    def test_pkgs_stripped_of_whitespace(self) -> None:
        stdout = "REBOOT=1\n  linux-base  \n  libc6  \n"
        status = parse_reboot_status(stdout, "ubuntu")
        assert "linux-base" in status.pkgs_requiring
        assert "libc6" in status.pkgs_requiring

    def test_blank_pkg_lines_ignored(self) -> None:
        stdout = "REBOOT=1\nlinux-base\n\n\nlibc6\n"
        status = parse_reboot_status(stdout, "ubuntu")
        assert len(status.pkgs_requiring) == 2


# --- parse_reboot_status: RHEL ---

class TestParseRebootStatusRhel:
    def test_exit_1_means_reboot_needed(self) -> None:
        stdout = "EXIT=1\n"
        status = parse_reboot_status(stdout, "rhel")
        assert status.needs_reboot is True
        assert status.reason is not None

    def test_exit_0_means_no_reboot(self) -> None:
        stdout = "EXIT=0\n"
        status = parse_reboot_status(stdout, "rhel")
        assert status.needs_reboot is False

    def test_exit_unknown_treats_as_no_reboot(self) -> None:
        stdout = "EXIT=unknown\n"
        status = parse_reboot_status(stdout, "rhel")
        assert status.needs_reboot is False

    def test_absent_binary_path_no_raise(self) -> None:
        # needs-restarting not installed — EXIT=unknown must not raise or block
        status = parse_reboot_status("EXIT=unknown\n", "centos")
        assert isinstance(status, RebootStatus)

    def test_empty_output_no_reboot(self) -> None:
        status = parse_reboot_status("", "rhel")
        assert status.needs_reboot is False

    def test_noise_before_exit_line(self) -> None:
        stdout = "Some preamble text\nEXIT=1\n"
        status = parse_reboot_status(stdout, "rhel")
        assert status.needs_reboot is True

    def test_rhel_pkgs_tuple_always_empty(self) -> None:
        status = parse_reboot_status("EXIT=1\n", "rhel")
        assert status.pkgs_requiring == ()


# --- detect_reboot_required ---

class TestDetectRebootRequired:
    async def test_ubuntu_reboot_needed(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("REBOOT=1\nlibc6\n")

        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            status = await detect_reboot_required(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", "ubuntu",
            )

        assert status.needs_reboot is True
        assert "libc6" in status.pkgs_requiring

    async def test_rhel_no_reboot(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("EXIT=0\n")

        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            status = await detect_reboot_required(
                executor, "prod/db-01", "10.0.0.2", "user", "/key", "rhel",
            )

        assert status.needs_reboot is False

    async def test_ssh_failure_treated_as_no_reboot(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("", exit_code=1)

        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            status = await detect_reboot_required(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", "ubuntu",
            )

        assert status.needs_reboot is False
        assert status.reason is None

    async def test_probe_uses_dry_run_false(self) -> None:
        executor = _make_executor()
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("REBOOT=0\n")

        with patch.object(executor, "execute", side_effect=capture):
            await detect_reboot_required(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", "ubuntu",
            )

        assert calls[0]["dry_run"] is False

    async def test_returns_reboot_status_type(self) -> None:
        executor = _make_executor()

        with patch.object(executor, "execute", AsyncMock(return_value=_make_result("REBOOT=0\n"))):
            status = await detect_reboot_required(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", "ubuntu",
            )

        assert isinstance(status, RebootStatus)
