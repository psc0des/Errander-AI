"""Docker hygiene finding models.

Structured types for the rich-assessment output of the docker_hygiene
sub-graph. Each finding represents one Docker object (image, container,
volume, or build-cache total) with its classification.

These models drive both the audit-log payload and the operator approval
artifact — the wrapper at execution time re-validates each (resource_class,
identity) pair against current state, so the model fields must round-trip
cleanly through YAML/JSON.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class DockerResourceClass(StrEnum):
    """The five Docker resource classes surfaced by docker_hygiene assessment."""

    IMAGE_DANGLING = "image_dangling"
    IMAGE_UNUSED = "image_unused"
    CONTAINER_STOPPED = "container_stopped"
    VOLUME_UNREFERENCED = "volume_unreferenced"
    BUILD_CACHE = "build_cache"


class FindingClassification(StrEnum):
    """How the agent classifies a finding before presenting it to the operator.

    - ``cleanup_candidate``: safe to remove given current execution-scope rules.
    - ``investigate``: signal of a possible problem (OOM, SIGSEGV, restart loop).
      Surfaced to the operator but never auto-marked for removal.
    - ``report_only``: shown for visibility but out of scope for v1.1 removal
      (volumes, build cache, recent or mid-aged images).
    """

    CLEANUP_CANDIDATE = "cleanup_candidate"
    INVESTIGATE = "investigate"
    REPORT_ONLY = "report_only"


@dataclass(frozen=True)
class DockerHygieneFinding:
    """A single Docker object surfaced by assessment.

    Field presence depends on ``resource_class``:
    - IMAGE_DANGLING / IMAGE_UNUSED: ``object_id``, ``size_bytes``, ``age_days``,
      optional ``last_tag``.
    - CONTAINER_STOPPED: ``object_id``, ``name``, ``exit_code``,
      ``stopped_age_hours``.
    - VOLUME_UNREFERENCED: ``name``, ``size_bytes``, ``last_mount_days``.
    - BUILD_CACHE: ``reclaimable_bytes`` only (single finding per VM).
    """

    resource_class: DockerResourceClass
    classification: FindingClassification

    object_id: str | None = None
    name: str | None = None
    size_bytes: int | None = None
    age_days: int | None = None
    last_tag: str | None = None
    exit_code: int | None = None
    stopped_age_hours: int | None = None
    last_mount_days: int | None = None
    reclaimable_bytes: int | None = None

    @property
    def identity(self) -> str:
        """Stable identity string for approval and audit (id or name)."""
        if self.object_id:
            return self.object_id
        if self.name:
            return self.name
        return f"<{self.resource_class.value}>"


@dataclass(frozen=True)
class DockerHygieneAssessment:
    """Aggregated assessment output for one VM."""

    vm_id: str
    findings: tuple[DockerHygieneFinding, ...] = field(default_factory=tuple)
    raw_output: str = ""
    reachable: bool = True
    error: str | None = None

    def by_class(self) -> dict[DockerResourceClass, list[DockerHygieneFinding]]:
        """Group findings by resource class. Empty classes are omitted."""
        out: dict[DockerResourceClass, list[DockerHygieneFinding]] = {}
        for f in self.findings:
            out.setdefault(f.resource_class, []).append(f)
        return out

    def cleanup_candidates(self) -> tuple[DockerHygieneFinding, ...]:
        """Findings eligible for removal under current execution-scope rules."""
        return tuple(
            f for f in self.findings
            if f.classification == FindingClassification.CLEANUP_CANDIDATE
        )

    def investigate(self) -> tuple[DockerHygieneFinding, ...]:
        """Findings flagged as possible problems (operator review needed)."""
        return tuple(
            f for f in self.findings
            if f.classification == FindingClassification.INVESTIGATE
        )

    def nothing_to_surface(self) -> bool:
        """True when assessment found no objects worth showing the operator."""
        return not self.findings


class ApprovalSurface(StrEnum):
    """Which surface the operator used to record an approval.

    Both surfaces produce the same DockerHygieneApproval artifact — the
    execute path doesn't branch on this field, but the audit log records
    it for traceability.
    """

    SLACK_REPLY = "slack_reply"
    WEB_PAGE = "web_page"
    TEST_INJECT = "test_inject"  # for tests bypassing both real surfaces


class RemovalStatus(StrEnum):
    """Per-object outcome from the remove wrapper."""

    REMOVED = "removed"
    DRIFT_SKIPPED = "drift_skipped"
    FAILED = "failed"
    SKIPPED_NOT_FOUND = "skipped_not_found"


@dataclass(frozen=True)
class DockerHygieneApproval:
    """The operator's approval artifact for a docker_hygiene execution.

    Names exact objects (by resource_class + identity), pins the assessment
    snapshot via a hash, and records who/which surface created the artifact.
    The remove wrapper re-validates each (class, id, expected_classification)
    against current state at execution time — if any object's state has
    drifted, that object is skipped (not silently removed).

    Attributes:
        vm_id: VM the approval applies to.
        approved_findings: Tuple of findings the operator approved. Each
            finding carries its classification *at approval time* — the
            wrapper re-checks that classification still holds.
        snapshot_hash: SHA-256 (first 16 hex chars) of the assessment that
            generated these findings. Used to detect drift across the full
            assessment, not just per-object.
        surface: Which channel the approval came from.
        operator_id: Slack user ID or web session identifier.
        approved_at: When the operator submitted approval.
    """

    vm_id: str
    approved_findings: tuple[DockerHygieneFinding, ...]
    snapshot_hash: str
    surface: ApprovalSurface
    operator_id: str
    approved_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def nothing_approved(self) -> bool:
        """True when the artifact records a rejection (no objects approved)."""
        return not self.approved_findings


@dataclass(frozen=True)
class DockerHygieneRemovalResult:
    """Per-object outcome from one invocation of the remove wrapper.

    One DockerHygieneRemovalResult corresponds to one approved finding.
    The audit log gets exactly one row per result (per the Exact-Object
    Approval invariant — one audit row per object, not per batch).

    Attributes:
        finding: The original finding the operator approved.
        status: Outcome (removed / drift_skipped / failed / skipped_not_found).
        drift_reason: When status == DRIFT_SKIPPED, why the object was
            skipped (e.g. ``image_re_tagged``, ``container_restarted``,
            ``volume_now_referenced``). None for other statuses.
        error: Stderr message when status == FAILED. None otherwise.
    """

    finding: DockerHygieneFinding
    status: RemovalStatus
    drift_reason: str | None = None
    error: str | None = None


# INVARIANT (layered-drift-gates / volatile-field-exclusion):
# Hash payload MUST exclude fields that fluctuate between probes without
# indicating meaningful drift (size_bytes, age_days, exit_code, etc.).
# Including them would trigger false drift refusals; excluding load-bearing
# fields (identity, classification) would let real drift slip through.
# See CLAUDE.md → AI Safety Invariant → Implementation Contracts (Contract A).
def compute_assessment_hash(assessment: DockerHygieneAssessment) -> str:
    """Compute a stable hash of an assessment's findings.

    Used to pin the approval artifact to a specific assessment snapshot.
    If the assessment is re-run between approval and execution, the hashes
    will differ — surfaces should refuse to honor approvals against stale
    snapshots.

    The hash covers (resource_class, identity, classification) for every
    finding, sorted for determinism. Fields like size_bytes are NOT in the
    hash because they can fluctuate between probes without indicating
    meaningful drift.
    """
    payload = sorted(
        (f.resource_class.value, f.identity, f.classification.value)
        for f in assessment.findings
    )
    blob = json.dumps(payload, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
