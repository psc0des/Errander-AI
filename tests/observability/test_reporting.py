"""Tests for report generation."""

from __future__ import annotations

from datetime import UTC, datetime

from errander.observability.reporting import format_reboot_required_section
from errander.safety.vm_state import VMState


def _vm_state(
    vm_id: str,
    needs_reboot: bool = True,
    reason: str | None = "packages require reboot",
    pkgs: tuple[str, ...] = (),
) -> VMState:
    return VMState(
        vm_id=vm_id,
        needs_reboot=needs_reboot,
        needs_reboot_reason=reason,
        needs_reboot_pkgs=pkgs,
        needs_reboot_detected_at=datetime.now(tz=UTC),
        last_uptime_seconds=None,
        updated_at=datetime.now(tz=UTC),
    )


class TestFormatRebootRequiredSection:
    def test_empty_list_returns_empty_string(self) -> None:
        assert format_reboot_required_section([]) == ""

    def test_single_vm_no_pkgs(self) -> None:
        result = format_reboot_required_section([_vm_state("dev/web-01")])
        assert "dev/web-01" in result
        assert "packages require reboot" in result

    def test_multiple_vms_listed(self) -> None:
        vms = [_vm_state("dev/web-01"), _vm_state("prod/db-01")]
        result = format_reboot_required_section(vms)
        assert "dev/web-01" in result
        assert "prod/db-01" in result

    def test_pkg_names_included(self) -> None:
        vms = [_vm_state("dev/web-01", pkgs=("libc6", "linux-base"))]
        result = format_reboot_required_section(vms)
        assert "libc6" in result
        assert "linux-base" in result

    def test_more_than_five_pkgs_truncated(self) -> None:
        pkgs = tuple(f"pkg-{i}" for i in range(8))
        vms = [_vm_state("dev/web-01", pkgs=pkgs)]
        result = format_reboot_required_section(vms)
        assert "+3 more" in result

    def test_exactly_five_pkgs_not_truncated(self) -> None:
        pkgs = tuple(f"pkg-{i}" for i in range(5))
        vms = [_vm_state("dev/web-01", pkgs=pkgs)]
        result = format_reboot_required_section(vms)
        assert "more" not in result

    def test_none_reason_falls_back(self) -> None:
        vms = [_vm_state("dev/web-01", reason=None)]
        result = format_reboot_required_section(vms)
        assert "dev/web-01" in result
        assert "reboot required" in result

    def test_section_header_present(self) -> None:
        result = format_reboot_required_section([_vm_state("dev/web-01")])
        assert "awaiting reboot" in result.lower()


class TestReporting:
    """Tests for plan and execution report generation."""

    def test_placeholder(self) -> None:
        """Placeholder."""
