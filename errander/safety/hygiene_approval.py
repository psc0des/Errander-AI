"""docker_hygiene approval surface — Slack formatter, reply parser, manager.

This module mirrors the design of :mod:`errander.safety.approval` but for
the docker_hygiene action's object-level approval flow. The legacy module
handles binary (✅/❌) reactions; this module handles structured commands
("approve images 1,3 containers 1") and produces a rich
:class:`~errander.models.docker_hygiene.DockerHygieneApproval` artifact.

The :class:`HygieneApprovalManager` is the rendezvous point. Both the
Slack reply poller (Session 2b-i) and the web approval page (Session 2b-ii)
resolve pending requests through it; whichever channel decides first wins.

Pieces in this file:

* :func:`format_hygiene_approval_message` — renders an assessment into the
  Slack message that operators see and reply to.
* :func:`parse_hygiene_reply` — parses operator replies ("approve …" /
  "reject all") and produces a DockerHygieneApproval.
* :class:`PendingHygieneApproval` — state object held in the manager.
* :class:`HygieneApprovalManager` — registers requests, resolves them,
  signals waiting coroutines.

INVARIANT (per-object-parser / drop-unapproved) is enforced here too:
when the operator references an index that doesn't map to a finding, the
parser raises HygieneReplyError instead of silently approving the wrong
object. See CLAUDE.md → Implementation Contracts (Contract B).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from errander.models.docker_hygiene import (
    ApprovalSurface,
    DockerHygieneApproval,
    DockerHygieneAssessment,
    DockerHygieneFinding,
    DockerResourceClass,
    FindingClassification,
    compute_assessment_hash,
)

logger = logging.getLogger(__name__)


class HygieneReplyError(ValueError):
    """Raised when an operator's reply text cannot be parsed into an approval."""


# ---------------------------------------------------------------------------
# Slack message formatter
# ---------------------------------------------------------------------------

#: Slack soft message limit; we leave headroom for code-block fencing.
_SLACK_BUDGET_CHARS = 3600

#: Resource classes whose findings can be approved for removal in v1.1.
#: Volumes and build_cache are report-only (CLAUDE.md → docker_hygiene scope).
_EXECUTABLE_CLASSES: tuple[DockerResourceClass, ...] = (
    DockerResourceClass.IMAGE_DANGLING,
    DockerResourceClass.IMAGE_UNUSED,
    DockerResourceClass.CONTAINER_STOPPED,
)


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
) -> str:
    """Render an assessment into the Slack approval message body.

    Groups findings by resource class with index numbers, sizes, ages, and
    classifications. Includes structured-reply syntax instructions and the
    web approval URL (when provided). Output stays under Slack's effective
    message limit; if findings overflow, individual lines are truncated
    before whole classes are dropped.
    """
    lines: list[str] = [
        f":mag: *Errander-AI Docker hygiene assessment* — `{assessment.vm_id}`",
    ]
    if batch_id:
        lines.append(f"batch `{batch_id}`")
    lines.append("")

    by_class = assessment.by_class()
    class_index = 1  # global index for use in reply syntax: approve <class> <n>
    indexed_classes: dict[str, list[DockerHygieneFinding]] = {}

    for klass in _EXECUTABLE_CLASSES + (
        DockerResourceClass.VOLUME_UNREFERENCED,
        DockerResourceClass.BUILD_CACHE,
    ):
        items = by_class.get(klass, [])
        if not items:
            continue
        # Use the class.value as the operator-facing key in the reply syntax.
        # The reply parser uses the same key.
        short_key = _short_class_key(klass)
        indexed_classes[short_key] = items
        lines.append(f"*{klass.value}* ({len(items)}):")
        for i, f in enumerate(items, start=1):
            tag = f.last_tag or f.name or "—"
            size = _human_bytes(f.size_bytes)
            age = _format_age(f)
            executable = " ✓" if klass in _EXECUTABLE_CLASSES else " (report-only)"
            lines.append(
                f"  {short_key}.{i} `{f.identity[:24]}` {tag} · {size} · {age}"
                f" · {f.classification.value}{executable}"
            )
        lines.append("")
        class_index += 1

    if not indexed_classes:
        lines.append("_No findings to surface._")
        return "\n".join(lines)

    lines.append("*Reply with:*")
    lines.append("```")
    lines.append("approve <class> <indices>  (e.g. approve images 1,3 containers 1)")
    lines.append("approve all cleanup_candidate")
    lines.append("reject all")
    lines.append("```")
    if web_approval_url:
        lines.append(f"Or use the web approval page: {web_approval_url}")

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
    """Map enum value to the short key used in operator reply syntax."""
    return {
        DockerResourceClass.IMAGE_DANGLING: "dangling",
        DockerResourceClass.IMAGE_UNUSED: "images",
        DockerResourceClass.CONTAINER_STOPPED: "containers",
        DockerResourceClass.VOLUME_UNREFERENCED: "volumes",
        DockerResourceClass.BUILD_CACHE: "build_cache",
    }[klass]


def _class_from_key(key: str) -> DockerResourceClass | None:
    """Reverse mapping. Returns None for unknown keys."""
    return {
        "dangling": DockerResourceClass.IMAGE_DANGLING,
        "images": DockerResourceClass.IMAGE_UNUSED,
        "containers": DockerResourceClass.CONTAINER_STOPPED,
        "volumes": DockerResourceClass.VOLUME_UNREFERENCED,
        "build_cache": DockerResourceClass.BUILD_CACHE,
    }.get(key.lower())


# ---------------------------------------------------------------------------
# Slack reply parser
# ---------------------------------------------------------------------------

# Accepts forms like:
#   approve dangling 1,2 containers 1
#   approve images 1-3
#   approve all cleanup_candidate
#   approve all
#   reject all
#   reject
_INDEX_TOKEN = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_hygiene_reply(
    text: str,
    assessment: DockerHygieneAssessment,
    *,
    operator_id: str,
    surface: ApprovalSurface = ApprovalSurface.SLACK_REPLY,
) -> DockerHygieneApproval:
    """Parse an operator reply into a DockerHygieneApproval.

    Raises:
        HygieneReplyError: malformed reply or references a non-existent index.

    Empty approvals (rejections) are returned as DockerHygieneApproval with
    ``approved_findings=()``. The execute path's nothing_approved() handles
    them as a skip.

    Operator-supplied indices that don't map to a finding are an error —
    we never silently approve a different finding from "nearest match" or
    similar heuristics. (Contract B / drop-unapproved analog at parse time.)
    """
    raw = text.strip().lower()
    if not raw:
        raise HygieneReplyError("empty reply")

    snapshot_hash = compute_assessment_hash(assessment)

    if raw.startswith("reject"):
        return DockerHygieneApproval(
            vm_id=assessment.vm_id,
            approved_findings=(),
            snapshot_hash=snapshot_hash,
            surface=surface,
            operator_id=operator_id,
        )

    if not raw.startswith("approve"):
        raise HygieneReplyError(
            "reply must start with 'approve' or 'reject' (got: "
            f"{text.strip()[:40]!r})"
        )

    # Strip the leading "approve" verb and tokenize the rest.
    body = raw[len("approve"):].strip()
    if not body:
        raise HygieneReplyError("'approve' must be followed by a target")

    # "all" or "all <classification>" path
    if body.startswith("all"):
        rest = body[len("all"):].strip()
        if not rest:
            target_class_filter: FindingClassification | None = None
        else:
            try:
                target_class_filter = FindingClassification(rest)
            except ValueError as exc:
                raise HygieneReplyError(
                    f"unknown classification after 'approve all': {rest!r}"
                ) from exc
        findings = _select_all(assessment, target_class_filter)
        return DockerHygieneApproval(
            vm_id=assessment.vm_id,
            approved_findings=findings,
            snapshot_hash=snapshot_hash,
            surface=surface,
            operator_id=operator_id,
        )

    # Explicit-index path: "<class> <indices> [<class> <indices>] ..."
    findings = _parse_explicit_indices(body, assessment)
    return DockerHygieneApproval(
        vm_id=assessment.vm_id,
        approved_findings=findings,
        snapshot_hash=snapshot_hash,
        surface=surface,
        operator_id=operator_id,
    )


def _select_all(
    assessment: DockerHygieneAssessment,
    classification_filter: FindingClassification | None,
) -> tuple[DockerHygieneFinding, ...]:
    """Return all findings in executable classes, optionally filtered by classification."""
    selected: list[DockerHygieneFinding] = []
    by_class = assessment.by_class()
    for klass in _EXECUTABLE_CLASSES:
        for f in by_class.get(klass, []):
            if classification_filter is None or f.classification == classification_filter:
                selected.append(f)
    return tuple(selected)


def _parse_explicit_indices(
    body: str,
    assessment: DockerHygieneAssessment,
) -> tuple[DockerHygieneFinding, ...]:
    """Parse 'dangling 1,2 containers 1' style → tuple of findings.

    Tokens after each class key are comma-separated index expressions, which
    may be either a single integer or a range "M-N" (inclusive). The index
    space is 1-based (matching what the operator sees in the Slack message).
    """
    by_class = assessment.by_class()
    tokens = body.split()
    selected: list[DockerHygieneFinding] = []
    seen_identities: set[tuple[str, str]] = set()

    i = 0
    while i < len(tokens):
        class_key = tokens[i].rstrip(":")
        klass = _class_from_key(class_key)
        if klass is None:
            raise HygieneReplyError(
                f"unknown class key {class_key!r} "
                f"(expected one of: dangling, images, containers, volumes, build_cache)"
            )
        if klass not in _EXECUTABLE_CLASSES:
            raise HygieneReplyError(
                f"class {class_key!r} is report-only and cannot be approved for removal in v1.1"
            )
        if i + 1 >= len(tokens):
            raise HygieneReplyError(
                f"class {class_key!r} must be followed by index expressions (e.g. 1,3 or 1-3)"
            )
        index_expr = tokens[i + 1]
        items = by_class.get(klass, [])
        for idx in _expand_index_expr(index_expr, class_key, len(items)):
            f = items[idx - 1]  # 1-based → 0-based
            key = (f.resource_class.value, f.identity)
            if key in seen_identities:
                continue
            seen_identities.add(key)
            selected.append(f)
        i += 2

    return tuple(selected)


def _expand_index_expr(expr: str, class_key: str, total: int) -> list[int]:
    """Expand '1,3-5,7' into [1, 3, 4, 5, 7]. Bounds-check against total.

    Raises HygieneReplyError on out-of-range or malformed inputs.
    """
    if total == 0:
        raise HygieneReplyError(
            f"class {class_key!r} has no findings to select from"
        )
    out: list[int] = []
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        m = _INDEX_TOKEN.match(part)
        if not m:
            raise HygieneReplyError(
                f"malformed index expression for {class_key!r}: {part!r}"
            )
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start < 1 or end < 1 or start > total or end > total:
            raise HygieneReplyError(
                f"index out of range for {class_key!r}: "
                f"{start}{'-' + str(end) if m.group(2) else ''} "
                f"(valid: 1..{total})"
            )
        if end < start:
            raise HygieneReplyError(
                f"reversed range for {class_key!r}: {start}-{end}"
            )
        out.extend(range(start, end + 1))
    return out


# ---------------------------------------------------------------------------
# HygieneApprovalManager — in-memory rendezvous
# ---------------------------------------------------------------------------

@dataclass
class PendingHygieneApproval:
    """An in-flight docker_hygiene approval awaiting a decision.

    Both the Slack reply poller and the web approval handler write to the
    same PendingHygieneApproval via :meth:`HygieneApprovalManager.resolve`;
    whichever channel decides first wins.

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
    """Tracks pending docker_hygiene approval requests across Slack + web surfaces.

    Single-threaded asyncio use. Each pending request is keyed by
    ``(batch_id, vm_id)`` since a batch can have multiple VMs each needing
    their own docker_hygiene approval.

    Usage::

        manager = HygieneApprovalManager()
        pending = manager.register(batch_id, vm_id, assessment, slack_ts=ts)

        # Slack poller / web handler (concurrent):
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
