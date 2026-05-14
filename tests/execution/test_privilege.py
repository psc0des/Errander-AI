"""Tests for the privilege escalation helper."""
from __future__ import annotations

import pytest

from errander.execution.privilege import (
    parse_capability_check,
    privileged,
    sudo_capability_check,
)


def test_privileged_adds_sudo_n() -> None:
    assert privileged("cmd") == "sudo -n cmd"


def test_privileged_preserves_absolute_path() -> None:
    assert privileged("/usr/bin/apt-get upgrade -y") == "sudo -n /usr/bin/apt-get upgrade -y"


def test_privileged_preserves_flags() -> None:
    cmd = "/usr/sbin/logrotate --force /etc/logrotate.conf 2>&1"
    assert privileged(cmd) == f"sudo -n {cmd}"


def test_sudo_capability_check_empty_returns_true() -> None:
    assert sudo_capability_check([]) == "true"


def test_sudo_capability_check_single_binary() -> None:
    result = sudo_capability_check(["/usr/bin/apt-get"])
    assert "sudo -n /usr/bin/apt-get --version" in result
    assert "SUDO_OK /usr/bin/apt-get" in result
    assert "SUDO_FAIL /usr/bin/apt-get" in result


def test_sudo_capability_check_docker_uses_version_subcommand() -> None:
    result = sudo_capability_check(["/usr/bin/docker"])
    assert "sudo -n /usr/bin/docker version" in result
    assert "--version" not in result


def test_sudo_capability_check_multiple_binaries() -> None:
    result = sudo_capability_check(["/usr/bin/apt-get", "/usr/sbin/logrotate"])
    assert "/usr/bin/apt-get" in result
    assert "/usr/sbin/logrotate" in result


def test_parse_capability_check_all_ok() -> None:
    output = "SUDO_OK /usr/bin/apt-get\nSUDO_OK /usr/sbin/logrotate"
    ok, failed = parse_capability_check(output)
    assert ok == ["/usr/bin/apt-get", "/usr/sbin/logrotate"]
    assert failed == []


def test_parse_capability_check_all_fail() -> None:
    output = "SUDO_FAIL /usr/bin/apt-get\nSUDO_FAIL /usr/bin/docker"
    ok, failed = parse_capability_check(output)
    assert ok == []
    assert failed == ["/usr/bin/apt-get", "/usr/bin/docker"]


def test_parse_capability_check_mixed() -> None:
    output = "SUDO_OK /usr/bin/apt-get\nSUDO_FAIL /usr/bin/docker"
    ok, failed = parse_capability_check(output)
    assert ok == ["/usr/bin/apt-get"]
    assert failed == ["/usr/bin/docker"]


def test_parse_capability_check_empty_output() -> None:
    ok, failed = parse_capability_check("")
    assert ok == []
    assert failed == []
