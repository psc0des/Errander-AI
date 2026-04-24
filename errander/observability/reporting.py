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

from errander.models.actions import ActionResult


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
