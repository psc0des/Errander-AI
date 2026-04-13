"""Dual-channel approval gate — Slack reactions + Web UI buttons.

Approval flow:
1. Post maintenance plan to #automaint-approvals (Slack) AND register with
   ApprovalManager (UI)
2. Race: first channel to decide wins
   - Slack: poll reactions every 30 s — ✅ approve, ❌ reject
   - UI:    operator clicks Approve / Reject button at /ui/approvals
3. Timeout after 30 minutes (configurable) → auto-REJECT on both channels

All Slack communication is outbound HTTPS only. No webhooks, no inbound traffic.

ApprovalManager is an in-memory store: it holds pending requests and signals
waiting coroutines via asyncio.Event when a decision is recorded (either from
the Slack poller or from the HTTP handler for the UI button).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from automaint.integrations.slack import APPROVE_REACTION, REJECT_REACTION, SlackClient, SlackError

logger = logging.getLogger(__name__)

#: How approval results are represented
ApprovalResult = tuple[bool, str | None]  # (approved, approver_user_id)


# ---------------------------------------------------------------------------
# request_approval / poll_approval (Slack-only, unchanged)
# ---------------------------------------------------------------------------

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
        f":robot_face: *AutoMaint Dry-Run Complete* — batch `{batch_id}`\n\n"
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
    deadline = datetime.now(tz=timezone.utc).timestamp() + timeout_seconds
    polls = 0

    while datetime.now(tz=timezone.utc).timestamp() < deadline:
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
            users: list[str] = list(reaction.get("users", []))  # type: ignore[arg-type]

            if name == REJECT_REACTION and users:
                logger.info(
                    "Batch rejected by %s (reaction: %s)", users[0], name,
                )
                return False, users[0]

        for reaction in reactions:
            name = str(reaction.get("name", ""))
            users = list(reaction.get("users", []))  # type: ignore[arg-type]

            if name == APPROVE_REACTION and users:
                logger.info(
                    "Batch approved by %s (reaction: %s)", users[0], name,
                )
                return True, users[0]

        remaining = int(deadline - datetime.now(tz=timezone.utc).timestamp())
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


# ---------------------------------------------------------------------------
# ApprovalManager — in-memory store for pending approvals
# ---------------------------------------------------------------------------

@dataclass
class PendingApproval:
    """An in-flight approval request waiting for a decision via Slack or UI.

    Fields:
        batch_id:          Batch being approved.
        report:            Dry-run report text shown to the operator.
        posted_at:         When the request was created (UTC).
        slack_message_ts:  Slack ts if the message was posted; None otherwise.
        approved:          True/False once decided; None while pending.
        decided_by:        User ID or "ui" / "timeout" once decided.
    """

    batch_id: str
    report: str
    posted_at: datetime
    slack_message_ts: str | None = None
    # Set by ApprovalManager.decide() — None while pending
    approved: bool | None = field(default=None, init=False)
    decided_by: str | None = field(default=None, init=False)
    # Signalled when a decision is recorded; used by wait_for_decision / await_dual_approval
    _event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    def is_decided(self) -> bool:
        """True once approve() or reject() has been called."""
        return self.approved is not None

    @property
    def result(self) -> ApprovalResult:
        """(approved, decided_by) — meaningful only after is_decided() is True."""
        return (bool(self.approved), self.decided_by)


class ApprovalManager:
    """Tracks pending approval requests and signals waiting coroutines.

    Thread-safety: designed for single-threaded asyncio use. The in-memory
    dict is not lock-protected — all callers should run in the same event loop.

    Usage::

        manager = ApprovalManager()

        # In the agent graph (one coroutine):
        approved, user = await await_dual_approval(
            manager, slack_client, batch_id, report,
        )

        # In the HTTP handler (another coroutine, same event loop):
        manager.decide(batch_id, approved=True, user_id="ui")
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._history: list[PendingApproval] = []

    def register(
        self,
        batch_id: str,
        report: str,
        slack_message_ts: str | None = None,
    ) -> PendingApproval:
        """Register a new pending approval.  Returns the PendingApproval object."""
        approval = PendingApproval(
            batch_id=batch_id,
            report=report,
            posted_at=datetime.now(tz=timezone.utc),
            slack_message_ts=slack_message_ts,
        )
        self._pending[batch_id] = approval
        logger.info("Approval request registered for batch %s", batch_id)
        return approval

    def decide(
        self,
        batch_id: str,
        approved: bool,
        user_id: str | None = None,
    ) -> None:
        """Record a decision.  Idempotent — safe to call twice for same batch.

        Moves the approval from _pending → _history and signals any coroutines
        waiting in wait_for_decision() or await_dual_approval().
        """
        approval = self._pending.pop(batch_id, None)
        if approval is None:
            return  # Already decided — no-op
        approval.approved = approved
        approval.decided_by = user_id
        approval._event.set()
        self._history.append(approval)
        logger.info(
            "Batch %s %s by %s",
            batch_id,
            "approved" if approved else "rejected",
            user_id or "timeout",
        )

    async def wait_for_decision(
        self,
        batch_id: str,
        timeout_seconds: int = 1800,
    ) -> ApprovalResult:
        """Wait for a decision on a registered batch.  Auto-rejects on timeout.

        Raises:
            KeyError: If batch_id was never registered.
        """
        if batch_id not in self._pending:
            raise KeyError(f"No pending approval for batch {batch_id!r}")
        approval = self._pending[batch_id]
        try:
            await asyncio.wait_for(
                approval._event.wait(),
                timeout=float(timeout_seconds),
            )
        except asyncio.TimeoutError:
            self.decide(batch_id, approved=False, user_id=None)
            return False, None
        return approval.result

    def get_pending(self) -> list[PendingApproval]:
        """All currently pending (undecided) approvals."""
        return list(self._pending.values())

    def get_history(self, limit: int = 20) -> list[PendingApproval]:
        """Recent decided approvals, newest first."""
        return list(reversed(self._history[-limit:]))


# ---------------------------------------------------------------------------
# await_dual_approval — races Slack polling vs UI button
# ---------------------------------------------------------------------------

async def await_dual_approval(
    manager: ApprovalManager,
    slack_client: SlackClient | None,
    batch_id: str,
    report: str,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 30,
) -> ApprovalResult:
    """Race Slack reaction polling against a UI button click.

    Steps:
    1. Post the report to Slack (if client provided).
    2. Register with ApprovalManager so the UI can show the pending request.
    3. Launch two concurrent tasks: Slack poller + UI event waiter.
    4. Return as soon as either decides (or timeout → auto-reject).

    The winning channel's decision is recorded in the manager (idempotent if
    the UI handler already called manager.decide()).

    Args:
        manager:               ApprovalManager singleton shared with the HTTP server.
        slack_client:          Optional Slack client.  When None, only UI approval works.
        batch_id:              Batch being approved.
        report:                Dry-run report to show operators.
        timeout_seconds:       Max wait before auto-reject (default 30 min).
        poll_interval_seconds: Slack reaction poll cadence (default 30 s).

    Returns:
        (approved: bool, approver_user_id: str | None)
    """
    # --- Step 1: post to Slack ---
    slack_ts: str | None = None
    if slack_client is not None:
        try:
            slack_ts = await request_approval(slack_client, batch_id, report)
        except SlackError as exc:
            logger.warning(
                "Slack approval post failed for batch %s: %s — UI-only mode",
                batch_id,
                exc,
            )

    # --- Step 2: register with manager ---
    pending = manager.register(batch_id, report, slack_message_ts=slack_ts)

    # --- Step 3: concurrent tasks ---

    async def _poll_slack() -> ApprovalResult:
        if slack_client is None or slack_ts is None:
            # No Slack — block until the UI decides (via timeout wake-up)
            await asyncio.sleep(float(timeout_seconds) + 1)
            return False, None
        return await poll_approval(
            slack_client, slack_ts, timeout_seconds, poll_interval_seconds,
        )

    async def _wait_ui() -> ApprovalResult:
        try:
            await asyncio.wait_for(
                pending._event.wait(),
                timeout=float(timeout_seconds),
            )
        except asyncio.TimeoutError:
            return False, None
        return pending.result

    slack_task: asyncio.Task[ApprovalResult] = asyncio.create_task(_poll_slack())
    ui_task: asyncio.Task[ApprovalResult] = asyncio.create_task(_wait_ui())

    done, running = await asyncio.wait(
        {slack_task, ui_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel the slower channel
    for t in running:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    winner = next(iter(done))
    approved, user_id = winner.result()

    # Record the decision (idempotent — UI handler may have already called decide())
    manager.decide(batch_id, approved, user_id)

    return approved, user_id
