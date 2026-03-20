"""Agent state definitions for all graph levels.

Three levels of state matching the Option C architecture:
- BatchState: top-level orchestrator state for multi-VM fan-out
- VMMaintenanceState: per-VM state passed via Send()
- Per-action states: specialized state for each action sub-graph
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from langgraph.graph import add_messages

from automaint.models.actions import Action, ActionResult, ActionStatus
from automaint.models.vm import VMInfo, VMTarget


def _merge_results(
    existing: list[ActionResult], new: list[ActionResult]
) -> list[ActionResult]:
    """Reducer that appends new action results to existing list."""
    return [*existing, *new]


@dataclass
class BatchState:
    """Top-level state for the batch orchestrator graph.

    Manages the full fleet-wide maintenance run. Uses Send() to fan out
    to per-VM maintenance graphs.

    Attributes:
        batch_id: Unique identifier for this maintenance run.
        targets: VM inventory to process.
        dry_run: If True, simulate actions without executing.
        force: If True, bypass maintenance window check (requires reason).
        force_reason: Mandatory reason when force=True.
        vm_results: Aggregated results from all VMs (append-only via reducer).
        healthy_targets: Targets that passed SSH/OS validation.
        failed_targets: Targets that failed validation.
        report: Final generated report text.
        approved: Whether the plan was approved for live execution.
    """

    batch_id: str = ""
    targets: list[VMTarget] = field(default_factory=list)
    dry_run: bool = True
    force: bool = False
    force_reason: str = ""
    vm_results: Annotated[list[ActionResult], _merge_results] = field(
        default_factory=list
    )
    healthy_targets: list[VMTarget] = field(default_factory=list)
    failed_targets: list[VMTarget] = field(default_factory=list)
    report: str = ""
    approved: bool | None = None


@dataclass
class VMMaintenanceState:
    """Per-VM state for the maintenance graph.

    Passed to per-VM sub-graph instances via Send(). Each VM gets
    its own independent copy.

    Attributes:
        vm_id: Target VM identifier.
        target: Full VM target configuration.
        dry_run: Inherited from BatchState.
        vm_info: Runtime-discovered system information (populated by discover node).
        planned_actions: Ordered actions to perform (populated by plan node).
        current_action_index: Loop cursor for sequential action dispatch.
        results: Per-VM action results (append-only via reducer).
        locked: Whether VM lock was acquired.
        error: Fatal error that stopped processing this VM.
    """

    vm_id: str = ""
    target: VMTarget | None = None
    dry_run: bool = True
    vm_info: VMInfo | None = None
    planned_actions: list[Action] = field(default_factory=list)
    current_action_index: int = 0
    results: Annotated[list[ActionResult], _merge_results] = field(
        default_factory=list
    )
    locked: bool = False
    error: str | None = None


@dataclass
class ActionSubgraphState:
    """Base state shared by all action sub-graphs.

    Each action type extends this with action-specific fields.

    Attributes:
        vm_id: Target VM identifier.
        os_family: Detected OS family (determines command strategy).
        dry_run: Whether to simulate or execute.
        status: Current action status.
        error: Error message if failed.
        rollback_detail: Description of rollback action taken.
        pre_snapshot: Pre-execution state snapshot for rollback.
    """

    vm_id: str = ""
    os_family: str = ""
    dry_run: bool = True
    status: ActionStatus = ActionStatus.PENDING
    error: str | None = None
    rollback_detail: str | None = None
    pre_snapshot: dict[str, object] = field(default_factory=dict)


@dataclass
class PatchingState(ActionSubgraphState):
    """State for the patching sub-graph.

    Attributes:
        available_patches: Packages with available updates.
        excluded_patterns: Patterns to exclude (e.g., kernel packages).
        installed_versions: Pre-patch version snapshot for rollback.
        patch_output: Raw output from package manager.
    """

    available_patches: list[str] = field(default_factory=list)
    excluded_patterns: list[str] = field(default_factory=lambda: ["linux-*", "kernel-*"])
    installed_versions: dict[str, str] = field(default_factory=dict)
    patch_output: str = ""


@dataclass
class DockerPruneState(ActionSubgraphState):
    """State for the Docker prune sub-graph.

    Attributes:
        images_to_prune: Dangling image IDs.
        containers_to_prune: Stopped container IDs.
        space_reclaimable: Estimated space to reclaim in bytes.
        space_reclaimed: Actual space reclaimed in bytes.
    """

    images_to_prune: list[str] = field(default_factory=list)
    containers_to_prune: list[str] = field(default_factory=list)
    space_reclaimable: int = 0
    space_reclaimed: int = 0


@dataclass
class LogRotationState(ActionSubgraphState):
    """State for the log rotation sub-graph.

    Attributes:
        oversized_logs: Log files exceeding size threshold.
        size_threshold_mb: Threshold above which logs are rotated.
        rotated_files: Files that were successfully rotated.
    """

    oversized_logs: list[str] = field(default_factory=list)
    size_threshold_mb: int = 100
    rotated_files: list[str] = field(default_factory=list)


@dataclass
class DiskCleanupState(ActionSubgraphState):
    """State for the disk cleanup sub-graph.

    Only touches paths on the approved whitelist:
    - /tmp (files older than threshold)
    - apt/yum package cache
    - Old journal logs
    - Orphaned package dependencies

    Attributes:
        whitelist_paths: Approved paths for cleanup.
        files_to_clean: Specific files/dirs identified for removal.
        space_reclaimable: Estimated space to reclaim in bytes.
        space_reclaimed: Actual space reclaimed in bytes.
        tmp_age_days: Minimum age for /tmp file cleanup.
    """

    whitelist_paths: list[str] = field(
        default_factory=lambda: ["/tmp", "apt-cache", "yum-cache", "journal", "orphaned-deps"]
    )
    files_to_clean: list[str] = field(default_factory=list)
    space_reclaimable: int = 0
    space_reclaimed: int = 0
    tmp_age_days: int = 7


@dataclass
class BackupVerifyState(ActionSubgraphState):
    """State for the backup verification sub-graph.

    Attributes:
        backup_paths: Expected backup file locations.
        max_age_hours: Maximum acceptable age for backups.
        verified_backups: Backups that passed verification.
        stale_backups: Backups that are too old.
        missing_backups: Expected backups that don't exist.
    """

    backup_paths: list[str] = field(default_factory=list)
    max_age_hours: int = 24
    verified_backups: list[str] = field(default_factory=list)
    stale_backups: list[str] = field(default_factory=list)
    missing_backups: list[str] = field(default_factory=list)
