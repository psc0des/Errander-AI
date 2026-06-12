"""docker_hygiene approval surface — Slack notification formatter + manager facade.

This module mirrors the design of :mod:`errander.safety.approval` but for
the docker_hygiene action's object-level approval flow. Since R2 (web-only
approval), Slack is notify-and-link: the message renders the exact findings
plus the signed web-approval URL, and the **only** decision surface is the
authenticated web page (``/ui/docker-hygiene/approve``), which produces the
:class:`~errander.models.docker_hygiene.DockerHygieneApproval` artifact.
The pre-R2 Slack thread-reply decision channel ("approve images 1,3") was
removed; ``ApprovalSurface.SLACK_REPLY`` survives in the enum for audit-log
read-back only.

Since R3 (process separation), :class:`HygieneApprovalManager` is a thin
facade over :class:`~errander.safety.hygiene_store.HygieneApprovalStore`,
which persists requests in the ``hygiene_approval_requests`` DB table so the
web process can list and decide them without sharing in-process state with the
agent.

Pieces in this file:

* :func:`format_hygiene_approval_message` — renders an assessment into the
  Slack notification operators see (with the web approval link).
* :class:`HygieneApprovalManager` — registers requests and waits for decisions
  on the agent side (delegates to the durable store).

INVARIANT (per-object-parser / drop-unapproved): the web approval handlers
only accept checkbox selections that map back to findings in the registered
assessment snapshot — an unknown selection is ignored, never resolved to a
"nearest" object. See CLAUDE.md → Implementation Contracts (Contract B).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from errander.models.docker_hygiene import (
    ApprovalSurface,
    DockerHygieneApproval,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
)

if TYPE_CHECKING:
    from errander.safety.hygiene_store import HygieneApprovalRow, HygieneApprovalStore

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
# HygieneApprovalManager — DB-backed facade (R3: process separation)
# ---------------------------------------------------------------------------

class HygieneApprovalManager:
    """Agent-side facade over HygieneApprovalStore for docker_hygiene approvals.

    Since R3, requests are persisted in the ``hygiene_approval_requests`` DB
    table so the web process can list and decide them independently.
    Decisions written by the web process are picked up by the 2 s poll in
    :meth:`wait_for_decision`.

    Usage::

        manager = HygieneApprovalManager(store)

        # Agent side:
        await manager.register(batch_id, vm_id, assessment)
        approval = await manager.wait_for_decision(batch_id, vm_id, timeout_seconds=1800)

        # Web handler (uses store directly in ui.py — no manager reference needed):
        await store.decide(batch_id, vm_id, approved=True, decided_by="ui:alice", ...)
    """

    def __init__(self, store: HygieneApprovalStore) -> None:
        self._store = store

    async def register(
        self,
        batch_id: str,
        vm_id: str,
        assessment: DockerHygieneAssessment,
        *,
        slack_message_ts: str | None = None,
        timeout_seconds: int = 1800,
    ) -> None:
        """Persist a new pending hygiene approval request in the DB."""
        await self._store.create(
            batch_id, vm_id, assessment, signed_token="",
            timeout_seconds=timeout_seconds,
        )

    async def wait_for_decision(
        self,
        batch_id: str,
        vm_id: str,
        *,
        timeout_seconds: int = 1800,
    ) -> DockerHygieneApproval | None:
        """Wait for operator decision. Returns None on timeout.

        Wakes within 2 s of a cross-process decision via DB poll.
        Returns None on timeout — the caller treats None as auto-reject,
        distinct from an explicit rejection (approved_findings=()).
        """
        row = await self._store.wait_for_decision(
            batch_id, vm_id, timeout_seconds=timeout_seconds
        )
        if row is None:
            return None
        return _row_to_approval(row)

    async def get_pending(self) -> list[HygieneApprovalRow]:
        """Currently-pending approval rows (for approvals queue listing)."""
        return await self._store.list_pending()


# ---------------------------------------------------------------------------
# Reconstruction helper
# ---------------------------------------------------------------------------

def _row_to_approval(row: HygieneApprovalRow) -> DockerHygieneApproval:
    """Reconstruct a DockerHygieneApproval from a decided DB row.

    Re-joins approved_items_json against the stored assessment to recover
    full DockerHygieneFinding objects. Un-matched approved items (object
    removed from assessment between approval and here) are silently dropped
    — the execute_node's snapshot-hash gate will catch meaningful drift.
    """
    assessment = row.assessment()
    approved_items = row.approved_items()
    approved_set = {
        (item["resource_class"], item["identity"])
        for item in approved_items
    }
    approved_findings: tuple[DockerHygieneFinding, ...] = ()
    if approved_set:
        approved_findings = tuple(
            f for f in assessment.findings
            if (f.resource_class.value, f.identity) in approved_set
        )
    return DockerHygieneApproval(
        vm_id=row.vm_id,
        approved_findings=approved_findings,
        snapshot_hash=row.snapshot_hash or "",
        surface=ApprovalSurface.WEB_PAGE,
        operator_id=row.decided_by or "",
        approved_at=row.decided_at or datetime.now(tz=UTC),
    )
