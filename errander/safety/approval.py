"""Slack approval channel — outbound posting + reaction polling.

Approval flow (R3 — durable approvals):
1. approval_gate_node persists a pending row in ApprovalRequestStore FIRST
   (durable-first: a crash after this point leaves a recoverable row).
2. The plan is posted to #errander-approvals (best-effort).
3. watch_slack_reactions polls the message every 30 s and writes a genuine
   reaction decision (✅ approve / ❌ reject) into the store — the same store
   the web UI handler writes to. First writer wins atomically.
4. The graph coroutine waits in ApprovalRequestStore.wait_for_decision;
   timeout transitions the row to 'timeout' (auto-REJECT).

All Slack communication is outbound HTTPS only. No webhooks, no inbound
traffic. The reaction watcher is transitional — R2 (web-only approval with
RBAC) deletes the Slack decision channel and keeps Slack notify-only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from errander.integrations.slack import APPROVE_REACTION, REJECT_REACTION, SlackClient, SlackError

if TYPE_CHECKING:
    from errander.safety.approval_store import ApprovalRequestStore

logger = logging.getLogger(__name__)

#: How approval results are represented
ApprovalResult = tuple[bool, str | None]  # (approved, approver_user_id)


async def request_approval(
    slack_client: SlackClient,
    batch_id: str,
    report: str,
) -> str:
    """Post a dry-run plan to Slack and return the message ts for polling.

    Formats the report as a Slack message with clear approval instructions.

    Args:
        slack_client: Configured Slack client.
        batch_id: Unique batch run identifier (shown in the message).
        report: Formatted dry-run report to post for review.

    Returns:
        Slack message timestamp (ts) to use with poll_approval.

    Raises:
        SlackError: If posting fails.
    """
    text = (
        f":robot_face: *Errander-AI Dry-Run Complete* — batch `{batch_id}`\n\n"
        f"React with :white_check_mark: to *approve* live execution.\n"
        f"React with :x: to *reject*.\n\n"
        f"```\n{report[:2800]}\n```"  # Slack message limit ~4000 chars
    )
    ts = await slack_client.post_message(text)
    logger.info("Approval request posted for batch %s (ts=%s)", batch_id, ts)
    return ts


async def poll_approval(
    slack_client: SlackClient,
    message_ts: str,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 30,
) -> ApprovalResult:
    """Poll for approval/rejection reaction on a Slack message.

    Checks reactions every `poll_interval_seconds`. Returns immediately
    when a decision reaction is found. Auto-rejects on timeout.

    Reaction semantics:
    - ✅ (white_check_mark): approved — returns (True, user_id)
    - ❌ (x): rejected — returns (False, user_id)
    - Timeout: returns (False, None)

    If multiple users react, the first matching reaction found wins.

    Args:
        slack_client: Configured Slack client.
        message_ts: Slack message timestamp to poll.
        timeout_seconds: Max seconds to wait (default 30 min).
        poll_interval_seconds: Seconds between reaction checks.

    Returns:
        (approved: bool, approver_user_id: str | None)
    """
    deadline = datetime.now(tz=UTC).timestamp() + timeout_seconds
    polls = 0

    while datetime.now(tz=UTC).timestamp() < deadline:
        polls += 1
        try:
            reactions = await slack_client.get_reactions(message_ts)
        except SlackError as exc:
            logger.warning("Failed to fetch reactions (poll %d): %s", polls, exc)
            await asyncio.sleep(poll_interval_seconds)
            continue

        # Check for reject first — explicit rejection takes priority
        for reaction in reactions:
            name = str(reaction.get("name", ""))
            users_raw = reaction.get("users")
            users: list[str] = [str(u) for u in users_raw] if isinstance(users_raw, list) else []
            if name == REJECT_REACTION and users:
                logger.info(
                    "Batch rejected by %s (reaction: %s)", users[0], name,
                )
                return False, users[0]

        for reaction in reactions:
            name = str(reaction.get("name", ""))
            users_raw = reaction.get("users")
            users = [str(u) for u in users_raw] if isinstance(users_raw, list) else []
            if name == APPROVE_REACTION and users:
                logger.info(
                    "Batch approved by %s (reaction: %s)", users[0], name,
                )
                return True, users[0]

        remaining = int(deadline - datetime.now(tz=UTC).timestamp())
        logger.debug(
            "Poll %d: no decision yet — waiting %ds (%ds remaining)",
            polls, poll_interval_seconds, remaining,
        )
        await asyncio.sleep(poll_interval_seconds)

    logger.warning(
        "Approval timeout after %ds (%d polls) — auto-rejecting",
        timeout_seconds, polls,
    )
    return False, None


async def watch_slack_reactions(
    slack_client: SlackClient,
    message_ts: str,
    store: ApprovalRequestStore,
    batch_id: str,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 30,
) -> None:
    """Poll Slack reactions and write a genuine decision into the store.

    Transitional R3 channel: runs as a background task next to the store's
    wait_for_decision. Only an actual reaction (user_id present) is written —
    poll timeout writes nothing, because timeout is owned by
    wait_for_decision / the reconciler (single source of truth).

    The store's atomic decide() settles the race against the web UI: if the
    UI decided first, this write is a logged no-op.
    """
    approved, user_id = await poll_approval(
        slack_client, message_ts, timeout_seconds, poll_interval_seconds,
    )
    if user_id is None:
        return  # poll timeout — not a decision
    await store.decide(
        batch_id,
        approved=approved,
        decided_by=f"slack:{user_id}",
    )
