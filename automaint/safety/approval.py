"""Slack polling-based approval gate.

Approval flow:
1. Post maintenance plan to #automaint-approvals channel
2. Poll for reactions every 30 seconds
3. Check for approved reaction or rejected reaction
4. Timeout after 30 minutes (configurable) → auto-REJECT

All communication is outbound HTTPS only. No webhooks, no inbound traffic.
"""

from __future__ import annotations


async def request_approval(
    batch_id: str,
    report: str,
    channel_id: str,
) -> str:
    """Post a plan to Slack and return the message timestamp for polling.

    Args:
        batch_id: Unique batch run identifier.
        report: Formatted report to post.
        channel_id: Slack channel ID for approvals.

    Returns:
        Slack message timestamp (ts) for reaction polling.
    """
    raise NotImplementedError("Approval request not yet implemented")


async def poll_approval(
    message_ts: str,
    channel_id: str,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 30,
) -> tuple[bool, str | None]:
    """Poll for approval reaction on a Slack message.

    Args:
        message_ts: Slack message timestamp to poll.
        channel_id: Slack channel ID.
        timeout_seconds: Max wait time before auto-reject (default 30 min).
        poll_interval_seconds: How often to check for reactions.

    Returns:
        Tuple of (approved, approver_user_id). None approver on timeout/reject.
    """
    raise NotImplementedError("Approval polling not yet implemented")
