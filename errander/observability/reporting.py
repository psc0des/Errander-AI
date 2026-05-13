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
    from errander.models.reports import BatchReport
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


def render_batch_report(report: BatchReport) -> str:
    """Render a BatchReport to a Slack-formatted human-readable string.

    Sections are emitted only when non-empty, in priority order:
    action results → preflight blocks → service regressions → reboot required
    → drift changes → disk growth → failed logins.

    Args:
        report: Fully populated BatchReport.

    Returns:
        Formatted multi-line string suitable for Slack posting.
    """
    lines: list[str] = []

    ts = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"*Errander Batch Report* — `{report.batch_id}`")
    lines.append(f"Generated: {ts}")

    # --- Action results ---
    results = report.vm_action_results
    if results:
        lines.append("")
        lines.append("*Action Results*")
        succeeded = sum(1 for r in results if str(r.get("status", "")) == "succeeded")
        failed = sum(1 for r in results if str(r.get("status", "")) == "failed")
        skipped = len(results) - succeeded - failed
        lines.append(
            f"  {len(results)} actions — "
            f"✓ {succeeded} succeeded, ✗ {failed} failed, ⊘ {skipped} skipped"
        )
        for r in results:
            vm = r.get("vm_id", "?")
            action = r.get("action_type", "?")
            status = r.get("status", "?")
            detail = r.get("detail", "")
            err = r.get("error")
            icon = "✓" if status == "succeeded" else "✗" if status == "failed" else "⊘"
            line = f"  {icon} `{vm}` {action}"
            if detail:
                line += f" — {detail}"
            if err:
                line += f" [error: {err}]"
            lines.append(line)

    # --- Preflight blocks ---
    blocks = report.preflight_blocks
    if blocks:
        lines.append("")
        lines.append(f"*Preflight Blocks* ({len(blocks)})")
        for b in blocks:
            holder = ""
            if b.holder_cmd:
                holder = f" (held by: {b.holder_cmd})"
            lines.append(f"  ⊘ `{b.vm_id}` {b.action_type} — {b.reason}{holder}")

    # --- Service health regressions ---
    regressions = report.service_health_regressions
    if regressions:
        lines.append("")
        lines.append(f"*:rotating_light: Service Regressions* ({len(regressions)})")
        for reg in regressions:
            lines.append(
                f"  • `{reg.vm_id}` {reg.service_name}: "
                f"{reg.state_before} → {reg.state_after}"
            )

    # --- Reboot required ---
    reboot_vms = report.reboot_required
    if reboot_vms:
        lines.append("")
        lines.append(f"*Reboot Required* ({len(reboot_vms)} VMs)")
        for vm in reboot_vms:
            pkgs = vm.pkgs_requiring
            pkg_detail = ""
            if pkgs:
                shown = ", ".join(pkgs[:5])
                pkg_detail = f" ({shown}"
                if len(pkgs) > 5:
                    pkg_detail += f" +{len(pkgs) - 5} more"
                pkg_detail += ")"
            reason = vm.reason or "reboot required"
            lines.append(f"  • `{vm.vm_id}` — {reason}{pkg_detail}")

    # --- Drift changes (grouped by kind) ---
    drift = report.drift_changes
    if drift:
        lines.append("")
        lines.append(f"*Configuration Drift* ({len(drift)} change(s))")
        by_kind: dict[str, list] = {}
        for d in drift:
            by_kind.setdefault(d.kind, []).append(d)
        for kind, changes in sorted(by_kind.items()):
            lines.append(f"  _{kind}_")
            for c in changes:
                scope = f" [{c.scope_key}]" if c.scope_key else ""
                lines.append(f"    • `{c.vm_id}`{scope}")
                for diff_line in c.unified_diff.splitlines()[:6]:
                    lines.append(f"      {diff_line}")

    # --- Disk growth alerts ---
    disk = report.disk_growth_alerts
    if disk:
        lines.append("")
        lines.append(f"*Disk Growth Alerts* ({len(disk)})")
        for g in disk:
            lines.append(
                f"  • `{g.vm_id}` {g.mountpoint}: "
                f"{g.used_pct_start:.1f}% → {g.used_pct_end:.1f}% "
                f"(+{g.delta_pct:.1f}%) over {g.window_label}"
            )

    # --- Failed logins ---
    logins = report.failed_logins
    if logins:
        lines.append("")
        lines.append(f"*Failed SSH Logins* ({len(logins)} VM(s))")
        for fl in logins:
            lines.append(
                f"  • `{fl.vm_id}` — {fl.total_count} attempts "
                f"in {fl.window_hours}h"
            )
            if fl.top_users:
                top = ", ".join(f"{u}×{c}" for u, c in fl.top_users[:3])
                lines.append(f"    top users: {top}")
            if fl.top_source_ips:
                top_ips = ", ".join(f"{ip}×{c}" for ip, c in fl.top_source_ips[:3])
                lines.append(f"    top IPs: {top_ips}")

    return "\n".join(lines)
