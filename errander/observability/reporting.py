"""Report generation — LLM-powered with template fallback.

Generates human-readable reports from batch results for Slack posting.
Uses LLM in /no_think mode for natural language summaries.
Falls back to Jinja2/string template on LLM failure.

Report types:
- Dry-run plan: What the agent would do (for approval)
- Execution summary: What the agent did (post-execution)
- Critical alert: Rollback failure or unexpected error
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.models.actions import ActionResult
    from errander.safety.vm_state import VMState


async def generate_plan_report(
    batch_id: str,
    vm_results: list[ActionResult],
) -> str:
    """Generate a dry-run plan report for Slack approval.

    Args:
        batch_id: Batch run identifier.
        vm_results: Dry-run results from all VMs.

    Returns:
        Formatted report string.
    """
    raise NotImplementedError("Plan report generation not yet implemented")


async def generate_execution_report(
    batch_id: str,
    vm_results: list[ActionResult],
) -> str:
    """Generate a post-execution summary report.

    Args:
        batch_id: Batch run identifier.
        vm_results: Execution results from all VMs.

    Returns:
        Formatted report string.
    """
    raise NotImplementedError("Execution report generation not yet implemented")


def format_reboot_required_section(vms: list[VMState]) -> str:
    """Format a 'VMs awaiting reboot' section for inclusion in batch reports.

    Args:
        vms: VMState records where needs_reboot is True.

    Returns:
        Human-readable section string.  Empty string when vms is empty.
    """
    if not vms:
        return ""

    lines = ["*VMs awaiting reboot after patching:*"]
    for vm in vms:
        pkg_detail = ""
        if vm.needs_reboot_pkgs:
            pkg_detail = f" ({', '.join(vm.needs_reboot_pkgs[:5])})"
            if len(vm.needs_reboot_pkgs) > 5:
                pkg_detail += f" +{len(vm.needs_reboot_pkgs) - 5} more"
        reason = vm.needs_reboot_reason or "reboot required"
        lines.append(f"  • `{vm.vm_id}` — {reason}{pkg_detail}")
    return "\n".join(lines)
