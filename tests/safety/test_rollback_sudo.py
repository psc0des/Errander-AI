"""Assert that rollback commands use sudo -n — the SRE's missed finding."""
from __future__ import annotations

import pytest

from errander.execution.commands import AptManager, DnfManager


def test_apt_rollback_install_uses_sudo() -> None:
    cmd = AptManager().install_version("nginx", "1.18.0-0ubuntu1")
    assert "sudo -n" in cmd
    assert "apt-get install" in cmd
    assert "--allow-downgrades" in cmd


def test_dnf_rollback_downgrade_uses_sudo() -> None:
    cmd = DnfManager().install_version("nginx", "1.20.0")
    assert cmd.startswith("sudo -n /usr/bin/dnf downgrade")


def test_apt_rollback_preserves_debian_frontend() -> None:
    cmd = AptManager().install_version("nginx", "1.18.0-0ubuntu1")
    assert "DEBIAN_FRONTEND=noninteractive" in cmd
