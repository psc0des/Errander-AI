"""Safe shell command construction helpers (finding #10).

All command strings passed to the SSH executor must go through this module.
No raw f-strings with untrusted input are permitted in subgraphs or command
strategies — this is enforced by the pre-commit grep hook.

Rules:
- safe_path()  — shlex.quote + path sanity check (no shell metacharacters)
- safe_pkg()   — package name allow-list (alphanumeric, -, _, .)
- build_cmd()  — join pre-validated parts into a single command string
"""

from __future__ import annotations

import re
import shlex

# Allow-list: package names are alphanumeric with hyphens, underscores, dots,
# colons (epoch separator in dpkg), plus (for C++ packages), and tildes (Debian
# version epoch). Reject anything else before it reaches the shell.
_SAFE_PKG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9+\-.:~_]*$")

# Allow-list: version strings from dpkg/rpm. Epoch:version-release format.
_SAFE_VER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9+\-.:~_]*$")

# Disallow shell metacharacters in paths even after quoting — defence in depth.
_PATH_METACHAR_RE = re.compile(r"[;&|`$(){}\\!<>]")


class CommandBuildError(ValueError):
    """Raised when unsafe input is detected during command construction."""


def safe_path(path: str) -> str:
    """Validate and shell-quote a filesystem path.

    Raises:
        CommandBuildError: If the path contains shell metacharacters or
            looks like it was constructed to escape quoting.
    """
    if not path:
        raise CommandBuildError("path must not be empty")
    if _PATH_METACHAR_RE.search(path):
        raise CommandBuildError(
            f"path contains shell metacharacter: {path!r}"
        )
    return shlex.quote(path)


def safe_pkg(name: str) -> str:
    """Validate a package name.

    Raises:
        CommandBuildError: If the name does not match the safe package
            name pattern (alphanumeric + [-+.:~_]).
    """
    if not _SAFE_PKG_RE.match(name):
        raise CommandBuildError(
            f"unsafe package name rejected: {name!r}"
        )
    return name


def safe_ver(version: str) -> str:
    """Validate a package version string.

    Raises:
        CommandBuildError: If the version does not match the safe version
            pattern (alphanumeric + [-+.:~_]).
    """
    if not _SAFE_VER_RE.match(version):
        raise CommandBuildError(
            f"unsafe package version rejected: {version!r}"
        )
    return version


def pkg_version_spec(pkg: str, ver: str) -> str:
    """Return a shell-safe 'pkg=ver' install spec for apt.

    Both package name and version are validated before assembly.
    """
    return f"{safe_pkg(pkg)}={safe_ver(ver)}"


# systemd unit names: alphanumeric, @, :, _, ., - plus a mandatory type suffix.
# See `man systemd.unit` for the authoritative grammar.
_SAFE_UNIT_RE = re.compile(
    r"^[a-zA-Z0-9@:_.\-]+"
    r"\.(service|socket|timer|target|mount|path|slice|scope|swap|automount|device)$"
)


def safe_systemd_unit_name(unit_name: str) -> str:
    """Validate a systemd unit name.

    Raises:
        CommandBuildError: If the name contains shell metacharacters or
            does not match the systemd unit naming grammar.
    """
    if not unit_name:
        raise CommandBuildError("unit_name must not be empty")
    if _PATH_METACHAR_RE.search(unit_name):
        raise CommandBuildError(
            f"unit_name contains shell metacharacter: {unit_name!r}"
        )
    if not _SAFE_UNIT_RE.match(unit_name):
        raise CommandBuildError(
            f"unit_name does not match systemd unit grammar: {unit_name!r}. "
            "Expected format: name.type (e.g. nginx.service, cron.timer)"
        )
    return unit_name


def build_cmd(parts: list[str]) -> str:
    """Shell-quote and join a list of command parts into a single string.

    Each part is independently quoted. Use for simple commands with
    known-safe structure. For complex pipelines, assemble with
    pre-validated literals and call safe_path()/safe_pkg() on any
    untrusted inputs.
    """
    return " ".join(shlex.quote(p) for p in parts)
