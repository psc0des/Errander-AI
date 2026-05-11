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


def build_cmd(parts: list[str]) -> str:
    """Shell-quote and join a list of command parts into a single string.

    Each part is independently quoted. Use for simple commands with
    known-safe structure. For complex pipelines, assemble with
    pre-validated literals and call safe_path()/safe_pkg() on any
    untrusted inputs.
    """
    return " ".join(shlex.quote(p) for p in parts)
