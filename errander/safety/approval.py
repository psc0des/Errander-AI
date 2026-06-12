"""Slack approval notification — notify-and-link only (R2).

Slack carries **no decision authority**. The flow:

1. approval_gate_node persists a pending row in ApprovalRequestStore FIRST
   (durable-first: a crash after this point leaves a recoverable row).
2. :func:`request_approval` posts the plan summary to #errander-approvals
   with a link to the web approval page (best-effort — Slack failure never
   blocks the gate).
3. The only place a decision can be recorded is the authenticated Web UI
   (`/ui/approvals`), which writes into the same store; the graph coroutine
   waits in ApprovalRequestStore.wait_for_decision. Timeout transitions the
   row to 'timeout' (auto-REJECT).

All Slack communication is outbound HTTPS only. No webhooks, no inbound
traffic. The pre-R2 Slack reaction decision channel (poll_approval /
watch_slack_reactions) was removed: approval authority is authentication,
not channel membership.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from errander.integrations.slack import SlackClient

logger = logging.getLogger(__name__)


async def request_approval(
    slack_client: SlackClient,
    batch_id: str,
    report: str,
    *,
    web_base_url: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Post a plan summary + web-approval link to Slack (notify-and-link).

    The message carries no decision mechanism — operators follow the link
    and decide in the authenticated Web UI. The link itself grants no
    authority (the approvals page requires a logged-in session with the
    decide_approvals permission).

    Args:
        slack_client: Configured Slack client.
        batch_id: Unique batch run identifier (shown in the message).
        report: Formatted plan summary to post for review.
        web_base_url: Externally-reachable UI base URL — when set, the
            message ends with "Approval required → <url>/ui/approvals".
        timeout_seconds: Approval timeout shown in the message, if known.

    Returns:
        Slack message timestamp (ts), stored on the approval row for audit.

    Raises:
        SlackError: If posting fails.
    """
    lines = [
        f":robot_face: *Errander-AI — Approval Required* — batch `{batch_id}`",
        "",
        f"```\n{report[:2800]}\n```",  # Slack message limit ~4000 chars
        "",
    ]
    if web_base_url:
        lines.append(
            f"*Approval required* → {web_base_url.rstrip('/')}/ui/approvals"
        )
    else:
        lines.append(
            "*Approval required* — decide on the agent's web UI under /ui/approvals "
            "(set ERRANDER_WEB_BASE_URL to include a direct link here)."
        )
    if timeout_seconds:
        lines.append(f"_Auto-rejects in {timeout_seconds // 60} min if undecided._")

    ts = await slack_client.post_message("\n".join(lines))
    logger.info("Approval notification posted for batch %s (ts=%s)", batch_id, ts)
    return ts
