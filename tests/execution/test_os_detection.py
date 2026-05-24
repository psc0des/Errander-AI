"""Tests for OS detection and verification (fully mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from errander.execution.os_detection import (
    detect_os,
    parse_disk_usage,
    parse_os_release,
    parse_uptime,
    verify_os_match,
)
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.vm import OSFamily


class TestParseOSRelease:
    """Tests for /etc/os-release parsing."""

    def test_ubuntu(self) -> None:
        content = '''NAME="Ubuntu"
VERSION="22.04.3 LTS (Jammy Jellyfish)"
ID=ubuntu
VERSION_ID="22.04"
PRETTY_NAME="Ubuntu 22.04.3 LTS"
'''
        family, version = parse_os_release(content)
        assert family == OSFamily.UBUNTU
        assert "Ubuntu 22.04" in version

    def test_rhel(self) -> None:
        content = '''NAME="Red Hat Enterprise Linux"
ID="rhel"
VERSION_ID="9.3"
PRETTY_NAME="Red Hat Enterprise Linux 9.3 (Plow)"
'''
        family, version = parse_os_release(content)
        assert family == OSFamily.RHEL

    def test_debian(self) -> None:
        content = '''NAME="Debian GNU/Linux"
ID=debian
VERSION_ID="12"
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
'''
        family, version = parse_os_release(content)
        assert family == OSFamily.DEBIAN

    def test_rocky_maps_to_rhel(self) -> None:
        content = 'ID="rocky"\nVERSION_ID="9.3"\nPRETTY_NAME="Rocky Linux 9.3"'
        family, _ = parse_os_release(content)
        assert family == OSFamily.RHEL

    def test_centos_maps_to_rhel(self) -> None:
        content = 'ID="centos"\nVERSION_ID="8"\nPRETTY_NAME="CentOS 8"'
        family, _ = parse_os_release(content)
        assert family == OSFamily.RHEL

    def test_unsupported_os_raises(self) -> None:
        content = 'ID="freebsd"\nVERSION_ID="14"\nPRETTY_NAME="FreeBSD 14"'
        with pytest.raises(ValueError, match="Unsupported OS"):
            parse_os_release(content)

    def test_empty_content_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported OS"):
            parse_os_release("")

    def test_missing_version_uses_unknown(self) -> None:
        content = 'ID=ubuntu\nPRETTY_NAME="Ubuntu"'
        family, version = parse_os_release(content)
        assert family == OSFamily.UBUNTU
        assert "Ubuntu" in version

    def test_comments_and_blank_lines_ignored(self) -> None:
        content = '''# This is a comment
ID=debian

VERSION_ID="12"
# Another comment
PRETTY_NAME="Debian 12"
'''
        family, _ = parse_os_release(content)
        assert family == OSFamily.DEBIAN

    def test_single_quoted_values(self) -> None:
        content = "ID='ubuntu'\nVERSION_ID='22.04'\nPRETTY_NAME='Ubuntu 22.04'"
        family, _ = parse_os_release(content)
        assert family == OSFamily.UBUNTU


class TestParseDiskUsage:
    """Tests for df -h output parsing."""

    def test_typical_output(self) -> None:
        output = """Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        50G   30G   20G  60% /
tmpfs           7.8G     0  7.8G   0% /dev/shm
/dev/sdb1       200G  150G   50G  75% /data
"""
        usage = parse_disk_usage(output)
        assert usage["/"] == 60.0
        assert usage["/dev/shm"] == 0.0
        assert usage["/data"] == 75.0

    def test_empty_output(self) -> None:
        assert parse_disk_usage("") == {}

    def test_header_only(self) -> None:
        output = "Filesystem      Size  Used Avail Use% Mounted on\n"
        assert parse_disk_usage(output) == {}


class TestParseUptime:
    """Tests for /proc/uptime parsing."""

    def test_typical_output(self) -> None:
        assert parse_uptime("12345.67 98765.43") == 12345.67

    def test_empty_returns_zero(self) -> None:
        assert parse_uptime("") == 0.0

    def test_garbage_returns_zero(self) -> None:
        assert parse_uptime("not-a-number") == 0.0


class TestDetectOS:
    """Tests for the full detect_os flow (SSH mocked)."""

    def _make_ssh_result(self, stdout: str = "", exit_code: int = 0) -> SSHResult:
        return SSHResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr="" if exit_code == 0 else "error",
            command="mocked",
        )

    async def test_detect_ubuntu(self) -> None:
        os_release = 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"'
        df_output = "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 50G 30G 20G 60% /"
        uptime_output = "12345.67 98765.43"

        mgr = SSHConnectionManager()
        execute_mock = AsyncMock(side_effect=[
            self._make_ssh_result(os_release),          # cat /etc/os-release
            self._make_ssh_result(df_output),            # df -h
            self._make_ssh_result("no"),                 # docker info
            self._make_ssh_result("5"),                  # pending packages
            self._make_ssh_result(uptime_output),        # uptime
        ])

        with patch.object(mgr, "execute", execute_mock):
            info = await detect_os("vm-1", "10.0.1.10", "errander-ai", "/key", mgr)

        assert info.os_family == OSFamily.UBUNTU
        assert "Ubuntu 22.04" in info.os_version
        assert info.disk_usage["/"] == 60.0
        assert info.docker_available is False
        assert info.pending_packages == 5
        assert info.uptime_seconds == 12345.67

    async def test_detect_with_docker(self) -> None:
        os_release = 'ID=rhel\nVERSION_ID="9"\nPRETTY_NAME="RHEL 9"'

        mgr = SSHConnectionManager()
        execute_mock = AsyncMock(side_effect=[
            self._make_ssh_result(os_release),
            self._make_ssh_result(""),  # df
            self._make_ssh_result("yes"),  # docker available
            self._make_ssh_result("0"),  # pending packages
            self._make_ssh_result("100.0 50.0"),  # uptime
        ])

        with patch.object(mgr, "execute", execute_mock):
            info = await detect_os("vm-1", "10.0.1.10", "errander-ai", "/key", mgr)

        assert info.docker_available is True

    async def test_os_release_failure_raises(self) -> None:
        mgr = SSHConnectionManager()
        execute_mock = AsyncMock(
            return_value=self._make_ssh_result("", exit_code=1),
        )

        with patch.object(mgr, "execute", execute_mock), pytest.raises(ValueError, match="Failed to read"):
            await detect_os("vm-1", "10.0.1.10", "errander-ai", "/key", mgr)

    async def test_graceful_degradation_on_optional_failures(self) -> None:
        """If df, docker, etc. fail, we still get a VMInfo with defaults."""
        os_release = 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian 12"'

        mgr = SSHConnectionManager()
        execute_mock = AsyncMock(side_effect=[
            self._make_ssh_result(os_release),           # os-release OK
            self._make_ssh_result("", exit_code=1),      # df failed
            self._make_ssh_result("", exit_code=1),      # docker failed
            self._make_ssh_result("not-a-number"),       # pkg count garbage
            self._make_ssh_result("", exit_code=1),      # uptime failed
        ])

        with patch.object(mgr, "execute", execute_mock):
            info = await detect_os("vm-1", "10.0.1.10", "errander-ai", "/key", mgr)

        assert info.os_family == OSFamily.DEBIAN
        assert info.disk_usage == {}
        assert info.docker_available is False
        assert info.pending_packages == 0
        assert info.uptime_seconds == 0.0


class TestVerifyOSMatch:
    """Tests for OS match verification."""

    def test_match(self) -> None:
        assert verify_os_match(OSFamily.UBUNTU, OSFamily.UBUNTU)

    def test_mismatch(self) -> None:
        assert not verify_os_match(OSFamily.UBUNTU, OSFamily.RHEL)
        assert not verify_os_match(OSFamily.DEBIAN, OSFamily.UBUNTU)
