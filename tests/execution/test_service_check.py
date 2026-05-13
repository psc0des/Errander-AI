"""Tests for service health check probing (PR-1.3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from errander.execution.sandbox import SandboxExecutor
from errander.execution.service_check import (
    ServiceStatus,
    check_services,
    find_regressions,
    parse_service_statuses,
    service_status_command,
)
from errander.execution.ssh import SSHConnectionManager, SSHResult


def _make_result(stdout: str = "", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _make_executor() -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=False)


# --- service_status_command ---

class TestServiceStatusCommand:
    def test_empty_services_returns_true(self) -> None:
        cmd = service_status_command(())
        assert cmd == "true"

    def test_single_service_in_command(self) -> None:
        cmd = service_status_command(("nginx",))
        assert "nginx" in cmd

    def test_multiple_services_in_command(self) -> None:
        cmd = service_status_command(("nginx", "postgresql", "sshd"))
        assert "nginx" in cmd
        assert "postgresql" in cmd
        assert "sshd" in cmd

    def test_uses_systemctl_is_active(self) -> None:
        cmd = service_status_command(("nginx",))
        assert "systemctl is-active" in cmd

    def test_outputs_name_equals_state(self) -> None:
        cmd = service_status_command(("nginx",))
        assert "$svc=$state" in cmd or "echo" in cmd

    def test_handles_absent_systemctl(self) -> None:
        # unknown fallback for absent systemctl
        cmd = service_status_command(("nginx",))
        assert "unknown" in cmd

    def test_command_exits_zero(self) -> None:
        # No unconditional exit 1
        cmd = service_status_command(("nginx",))
        assert "exit 1" not in cmd.lower()

    def test_no_shell_injection_separator(self) -> None:
        # Service names are in a for-loop — output echoed safely
        cmd = service_status_command(("nginx", "sshd"))
        assert "for svc in" in cmd


# --- parse_service_statuses ---

class TestParseServiceStatuses:
    def test_active_service(self) -> None:
        statuses = parse_service_statuses("nginx=active\n", ("nginx",))
        assert statuses["nginx"].active is True
        assert statuses["nginx"].state == "active"

    def test_inactive_service(self) -> None:
        statuses = parse_service_statuses("nginx=inactive\n", ("nginx",))
        assert statuses["nginx"].active is False
        assert statuses["nginx"].state == "inactive"

    def test_failed_service(self) -> None:
        statuses = parse_service_statuses("postgresql=failed\n", ("postgresql",))
        assert statuses["postgresql"].active is False
        assert statuses["postgresql"].state == "failed"

    def test_unknown_service(self) -> None:
        statuses = parse_service_statuses("sshd=unknown\n", ("sshd",))
        assert statuses["sshd"].active is False

    def test_multiple_services(self) -> None:
        stdout = "nginx=active\npostgresql=inactive\nsshd=active\n"
        statuses = parse_service_statuses(stdout, ("nginx", "postgresql", "sshd"))
        assert statuses["nginx"].active is True
        assert statuses["postgresql"].active is False
        assert statuses["sshd"].active is True

    def test_missing_service_filled_as_unknown(self) -> None:
        # nginx missing from output → unknown
        statuses = parse_service_statuses("sshd=active\n", ("nginx", "sshd"))
        assert statuses["nginx"].state == "unknown"
        assert statuses["nginx"].active is False

    def test_empty_output_all_unknown(self) -> None:
        statuses = parse_service_statuses("", ("nginx", "sshd"))
        assert all(s.state == "unknown" for s in statuses.values())

    def test_strips_whitespace(self) -> None:
        statuses = parse_service_statuses("  nginx = active  \n", ("nginx",))
        assert statuses["nginx"].active is True

    def test_ignores_lines_without_equals(self) -> None:
        statuses = parse_service_statuses("junk line\nnginx=active\n", ("nginx",))
        assert statuses["nginx"].active is True

    def test_returns_service_status_type(self) -> None:
        statuses = parse_service_statuses("nginx=active\n", ("nginx",))
        assert isinstance(statuses["nginx"], ServiceStatus)


# --- find_regressions ---

class TestFindRegressions:
    def test_active_to_inactive_is_regression(self) -> None:
        pre = {"nginx": ServiceStatus("nginx", active=True, state="active")}
        post = {"nginx": ServiceStatus("nginx", active=False, state="inactive")}
        assert find_regressions(pre, post) == ["nginx"]

    def test_active_stays_active_no_regression(self) -> None:
        pre = {"nginx": ServiceStatus("nginx", active=True, state="active")}
        post = {"nginx": ServiceStatus("nginx", active=True, state="active")}
        assert find_regressions(pre, post) == []

    def test_inactive_stays_inactive_no_regression(self) -> None:
        pre = {"nginx": ServiceStatus("nginx", active=False, state="inactive")}
        post = {"nginx": ServiceStatus("nginx", active=False, state="inactive")}
        assert find_regressions(pre, post) == []

    def test_inactive_to_active_no_regression(self) -> None:
        # Service came up — that's good, not a regression
        pre = {"nginx": ServiceStatus("nginx", active=False, state="inactive")}
        post = {"nginx": ServiceStatus("nginx", active=True, state="active")}
        assert find_regressions(pre, post) == []

    def test_active_to_failed_is_regression(self) -> None:
        pre = {"postgresql": ServiceStatus("postgresql", active=True, state="active")}
        post = {"postgresql": ServiceStatus("postgresql", active=False, state="failed")}
        assert "postgresql" in find_regressions(pre, post)

    def test_multiple_services_one_regression(self) -> None:
        pre = {
            "nginx": ServiceStatus("nginx", active=True, state="active"),
            "sshd": ServiceStatus("sshd", active=True, state="active"),
        }
        post = {
            "nginx": ServiceStatus("nginx", active=False, state="inactive"),
            "sshd": ServiceStatus("sshd", active=True, state="active"),
        }
        assert find_regressions(pre, post) == ["nginx"]

    def test_service_missing_from_post_not_regression_if_pre_inactive(self) -> None:
        pre = {"nginx": ServiceStatus("nginx", active=False, state="inactive")}
        post: dict[str, ServiceStatus] = {}
        assert find_regressions(pre, post) == []

    def test_service_missing_from_post_no_regression(self) -> None:
        # Missing from post (e.g. SSH failure) → treated as pre_status (no data = no regression).
        # This prevents false regressions when the post probe itself fails.
        pre = {"nginx": ServiceStatus("nginx", active=True, state="active")}
        post: dict[str, ServiceStatus] = {}
        assert find_regressions(pre, post) == []

    def test_empty_pre_no_regressions(self) -> None:
        pre: dict[str, ServiceStatus] = {}
        post = {"nginx": ServiceStatus("nginx", active=False, state="inactive")}
        assert find_regressions(pre, post) == []


# --- check_services ---

class TestCheckServices:
    async def test_returns_parsed_statuses(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("nginx=active\npostgresql=inactive\n")

        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            result = await check_services(
                executor, "dev/web-01", "10.0.0.1", "user", "/key",
                ("nginx", "postgresql"),
            )

        assert result["nginx"].active is True
        assert result["postgresql"].active is False

    async def test_empty_services_returns_empty_dict(self) -> None:
        executor = _make_executor()
        result = await check_services(
            executor, "dev/web-01", "10.0.0.1", "user", "/key", (),
        )
        assert result == {}

    async def test_ssh_failure_returns_empty_dict(self) -> None:
        executor = _make_executor()
        mock_result = _make_result("", exit_code=1)

        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            result = await check_services(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", ("nginx",),
            )

        assert result == {}

    async def test_uses_dry_run_false(self) -> None:
        executor = _make_executor()
        calls: list[dict[str, object]] = []

        async def capture(*args: object, **kwargs: object) -> SSHResult:
            calls.append(dict(kwargs))
            return _make_result("nginx=active\n")

        with patch.object(executor, "execute", side_effect=capture):
            await check_services(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", ("nginx",),
            )

        assert calls[0]["dry_run"] is False

    async def test_absent_systemctl_returns_unknown(self) -> None:
        executor = _make_executor()
        # systemctl absent → empty stdout (or empty state)
        mock_result = _make_result("nginx=unknown\n")

        with patch.object(executor, "execute", AsyncMock(return_value=mock_result)):
            result = await check_services(
                executor, "dev/web-01", "10.0.0.1", "user", "/key", ("nginx",),
            )

        assert result["nginx"].state == "unknown"
        assert result["nginx"].active is False
