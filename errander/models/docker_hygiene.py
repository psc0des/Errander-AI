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

from dataclasses import dataclass, field
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
