"""docker_hygiene approval surface — Slack notification formatter + manager.

This module mirrors the design of :mod:`errander.safety.approval` but for
the docker_hygiene action's object-level approval flow. Since R2 (web-only
approval), Slack is notify-and-link: the message renders the exact findings
plus the signed web-approval URL, and the **only** decision surface is the
authenticated web page (``/ui/docker-hygiene/approve``), which produces the
:class:`~errander.models.docker_hygiene.DockerHygieneApproval` artifact.
The pre-R2 Slack thread-reply decision channel ("approve images 1,3") was
removed; ``ApprovalSurface.SLACK_REPLY`` survives in the enum for audit-log
read-back only.

Pieces in this file:

* :func:`format_hygiene_approval_message` — renders an assessment into the
  Slack notification operators see (with the web approval link).
* :class:`PendingHygieneApproval` — state object held in the manager.
* :class:`HygieneApprovalManager` — registers requests, resolves them
  (web handler calls :meth:`~HygieneApprovalManager.resolve`), signals
  waiting coroutines.

INVARIANT (per-object-parser / drop-unapproved): the web approval handlers
only accept checkbox selections that map back to findings in the registered
assessment snapshot — an unknown selection is ignored, never resolved to a
"nearest" object. See CLAUDE.md → Implementation Contracts (Contract B).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from errander.models.docker_hygiene import (
    DockerHygieneApproval,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Slack message formatter
# ---------------------------------------------------------------------------

#: Slack soft message limit; we leave headroom for code-block fencing.
_SLACK_BUDGET_CHARS = 3600

#: Resource classes whose findings can be approved for removal.
#: Within executable classes, only CLEANUP_CANDIDATE findings are selectable —
#: report_only findings (e.g. image_unused younger than the age threshold) are
#: surfaced for visibility but cannot be approved.
_EXECUTABLE_CLASSES: tuple[DockerResourceClass, ...] = (
    DockerResourceClass.IMAGE_DANGLING,
    DockerResourceClass.IMAGE_UNUSED,
    DockerResourceClass.CONTAINER_STOPPED,
    DockerResourceClass.VOLUME_UNREFERENCED,
    DockerResourceClass.BUILD_CACHE,
)

#: Classes surfaced for visibility but not approvable in the v1 web form.
#: Volumes are irreversible and higher-blast-radius than images; the web
#: approval page intentionally renders them report-only (the pre-R2 Slack
#: reply channel was the only way to approve them; R2 removed it — fail
#: closed rather than make volume removal one checkbox away).
_REPORT_ONLY_IN_WEB: frozenset[DockerResourceClass] = frozenset({
    DockerResourceClass.VOLUME_UNREFERENCED,
})


def _human_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f}PB"


def format_hygiene_approval_message(
    assessment: DockerHygieneAssessment,
    *,
    web_approval_url: str | None = None,
    batch_id: str | None = None,
    backup_verify_passed: bool | None = None,
) -> str:
    """Render an assessment into the Slack notification body (notify-and-link).

    Groups findings by resource class with index numbers, sizes, ages, and
    classifications, and points the operator at the web approval page (the
    only decision surface — R2). Output stays under Slack's effective
    message limit; if findings overflow, individual lines are truncated
    before whole classes are dropped.
    """
    lines: list[str] = [
        f":mag: *Errander-AI Docker hygiene assessment* — `{assessment.vm_id}`",
    ]
    if batch_id:
        lines.append(f"batch `{batch_id}`")
    lines.append("")

    # Backup verify context — soft signal shown when volume candidates are present.
    volume_candidates = [
        f for f in assessment.findings
        if f.resource_class == DockerResourceClass.VOLUME_UNREFERENCED
        and f.classification == FindingClassification.CLEANUP_CANDIDATE
    ]
    if volume_candidates and backup_verify_passed is not None:
        if backup_verify_passed:
            lines.append(":white_check_mark: Backup status: Verified — backup ran successfully.")
        else:
            lines.append(":warning: Backup verify: not run or failed — volumes still await your approval.")
        lines.append("")

    by_class = assessment.by_class()
    class_index = 1  # per-class display index (mirrors the web form ordering)
    indexed_classes: dict[str, list[DockerHygieneFinding]] = {}

    for klass in _EXECUTABLE_CLASSES:
        items = by_class.get(klass, [])
        if not items:
            continue
        # Short class key shown next to each index — the web form renders
        # the same key in its checkbox field names.
        short_key = _short_class_key(klass)
        indexed_classes[short_key] = items
        lines.append(f"*{klass.value}* ({len(items)}):")
        for i, f in enumerate(items, start=1):
            tag = f.last_tag or f.name or "—"
            size = _human_bytes(f.size_bytes)
            age = _format_age(f)
            if f.classification == FindingClassification.CLEANUP_CANDIDATE and klass in _EXECUTABLE_CLASSES:
                executable = " ⚠ report-only in web UI (v1)" if klass in _REPORT_ONLY_IN_WEB else " ✓"
            elif f.classification == FindingClassification.INVESTIGATE:
                executable = " ⚠ investigate"
            else:
                executable = " (report-only)"
            lines.append(
                f"  {short_key}.{i} `{f.identity[:24]}` {tag} · {size} · {age}"
                f" · {f.classification.value}{executable}"
            )
        lines.append("")
        class_index += 1

    if not indexed_classes:
        lines.append("_No findings to surface._")
        return "\n".join(lines)

    if web_approval_url:
        lines.append(f"*Approval required* → {web_approval_url}")
    else:
        lines.append(
            "*Approval required* — open the pending item on the agent web UI "
            "under /ui/approvals (set ERRANDER_WEB_BASE_URL for a direct link)."
        )

    body = "\n".join(lines)
    if len(body) > _SLACK_BUDGET_CHARS:
        body = body[: _SLACK_BUDGET_CHARS - 80] + "\n…\n_(message truncated; use web approval page)_"
    return body


def _format_age(f: DockerHygieneFinding) -> str:
    if f.age_days is not None:
        return f"{f.age_days}d"
    if f.stopped_age_hours is not None:
        return f"{f.stopped_age_hours}h"
    if f.last_mount_days is not None:
        return f"{f.last_mount_days}d"
    return "—"


def _short_class_key(klass: DockerResourceClass) -> str:
    """Map enum value to the short key used in message lines + web form field names."""
    return {
        DockerResourceClass.IMAGE_DANGLING: "dangling",
        DockerResourceClass.IMAGE_UNUSED: "images",
        DockerResourceClass.CONTAINER_STOPPED: "containers",
        DockerResourceClass.VOLUME_UNREFERENCED: "volumes",
        DockerResourceClass.BUILD_CACHE: "build_cache",
    }[klass]


# ---------------------------------------------------------------------------
# HygieneApprovalManager — in-memory rendezvous
# ---------------------------------------------------------------------------

@dataclass
class PendingHygieneApproval:
    """An in-flight docker_hygiene approval awaiting a decision.

    The web approval handler resolves it via
    :meth:`HygieneApprovalManager.resolve` (sole decision surface — R2);
    resolve() is idempotent, first writer wins.

    Fields:
        batch_id, vm_id: identifier pair (one pending per VM per batch).
        assessment: the assessment snapshot operators see.
        posted_at: when the request was registered.
        slack_message_ts: Slack ts if the message was posted; None otherwise.
        approval: set by resolve() — None while pending.
    """

    batch_id: str
    vm_id: str
    assessment: DockerHygieneAssessment
    posted_at: datetime
    slack_message_ts: str | None = None
    approval: DockerHygieneApproval | None = field(default=None, init=False)
    _event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)

    @property
    def key(self) -> tuple[str, str]:
        """Composite key used by the manager."""
        return (self.batch_id, self.vm_id)

    def is_decided(self) -> bool:
        return self.approval is not None


class HygieneApprovalManager:
    """Tracks pending docker_hygiene approval requests for the web surface.

    Single-threaded asyncio use. Each pending request is keyed by
    ``(batch_id, vm_id)`` since a batch can have multiple VMs each needing
    their own docker_hygiene approval.

    Usage::

        manager = HygieneApprovalManager()
        pending = manager.register(batch_id, vm_id, assessment, slack_ts=ts)

        # Web approval handler:
        manager.resolve(batch_id, vm_id, approval)

        # Batch coroutine waits:
        approval = await manager.wait_for_decision(batch_id, vm_id, timeout=1800)
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], PendingHygieneApproval] = {}
        self._history: list[PendingHygieneApproval] = []

    def register(
        self,
        batch_id: str,
        vm_id: str,
        assessment: DockerHygieneAssessment,
        *,
        slack_message_ts: str | None = None,
    ) -> PendingHygieneApproval:
        """Register a new pending hygiene approval."""
        pending = PendingHygieneApproval(
            batch_id=batch_id,
            vm_id=vm_id,
            assessment=assessment,
            posted_at=datetime.now(tz=UTC),
            slack_message_ts=slack_message_ts,
        )
        self._pending[pending.key] = pending
        logger.info(
            "Hygiene approval registered for batch=%s vm=%s (%d findings)",
            batch_id, vm_id, len(assessment.findings),
        )
        return pending

    def resolve(
        self,
        batch_id: str,
        vm_id: str,
        approval: DockerHygieneApproval,
    ) -> None:
        """Record the operator's decision. Idempotent — first wins."""
        key = (batch_id, vm_id)
        pending = self._pending.pop(key, None)
        if pending is None:
            return  # Already resolved or never registered
        pending.approval = approval
        pending._event.set()
        self._history.append(pending)
        logger.info(
            "Hygiene approval resolved for batch=%s vm=%s (%d objects, surface=%s)",
            batch_id, vm_id,
            len(approval.approved_findings),
            approval.surface.value,
        )

    async def wait_for_decision(
        self,
        batch_id: str,
        vm_id: str,
        *,
        timeout_seconds: int = 1800,
    ) -> DockerHygieneApproval | None:
        """Wait for a decision. Returns None on timeout (no auto-rejection).

        The caller treats None as a timeout — it's *not* the same as an
        empty-approval rejection. A rejection produces a
        DockerHygieneApproval with approved_findings=().

        Raises:
            KeyError: When (batch_id, vm_id) is not currently pending.
        """
        key = (batch_id, vm_id)
        pending = self._pending.get(key)
        if pending is None:
            msg = f"No pending hygiene approval for batch={batch_id!r} vm={vm_id!r}"
            raise KeyError(msg)
        try:
            await asyncio.wait_for(pending._event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            # Drop from pending so a late resolve() doesn't fire stale.
            self._pending.pop(key, None)
            self._history.append(pending)
            logger.warning(
                "Hygiene approval timed out for batch=%s vm=%s after %ds",
                batch_id, vm_id, timeout_seconds,
            )
            return None
        return pending.approval

    def get_pending(self) -> list[PendingHygieneApproval]:
        """Snapshot of currently-pending approvals (for /ui/approvals page)."""
        return list(self._pending.values())

    def get_history(self) -> list[PendingHygieneApproval]:
        """Snapshot of resolved approvals (for audit / debug)."""
        return list(self._history)
