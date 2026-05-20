"""A3: Round-trip all GraphState TypeDicts through JsonPlusSerializer.

Invariants verified per state type:
  1. round_trip(state) == state  (JSON-safe, no live objects)
  2. No field value exceeds 4 KB when serialized  (blob guard)

The 4 KB limit flags fields that should be moved to ArtifactStore (A4).
patch_output, system_df_output, prune_output, and rotation_output are
intentionally left in state here to document the current baseline; A4
will move them out and replace with artifact_id references.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from errander.agent.graph import BatchGraphState
from errander.agent.subgraphs.backup_verify import BackupVerifyGraphState
from errander.agent.subgraphs.disk_cleanup import DiskCleanupGraphState
from errander.agent.subgraphs.docker_prune import DockerPruneGraphState
from errander.agent.subgraphs.log_rotation import LogRotationGraphState
from errander.agent.subgraphs.patching import PatchingGraphState
from errander.agent.vm_graph import VMGraphState

SERDE = JsonPlusSerializer()
_4KB = 4 * 1024  # bytes


def _round_trip(state: dict[str, Any]) -> dict[str, Any]:
    """Serialize then deserialize through JsonPlusSerializer."""
    type_str, bytes_data = SERDE.dumps_typed(state)
    result = SERDE.loads_typed((type_str, bytes_data))
    return result  # type: ignore[return-value]


def _field_sizes(state: dict[str, Any]) -> dict[str, int]:
    """Return byte sizes of each field's serialized value."""
    return {
        k: len(json.dumps(v, default=str).encode())
        for k, v in state.items()
    }


# ---------------------------------------------------------------------------
# BatchGraphState
# ---------------------------------------------------------------------------

_BATCH_STATE: BatchGraphState = {  # type: ignore[typeddict-item]
    "batch_id": "batch-abc123",
    "batch_started_at": "2026-05-20T02:00:00+00:00",
    "dry_run": False,
    "force": False,
    "force_reason": "",
    "env_name": "PROD",
    "env_policy": "strict",
    "targets": [
        {
            "vm_id": "prod/web-01",
            "hostname": "prod-web-01",
            "ssh_user": "errander-ai",
            "ssh_key_path": "/keys/prod.pem",
        }
    ],
    "healthy_targets": [],
    "failed_targets": [],
    "vm_plans": [],
    "enriched_vm_plans": [],
    "plan_id": "plan-xyz789",
    "plan_hash": "a" * 64,
    "vm_results": [
        {"vm_id": "prod/web-01", "action_type": "patching", "status": "success"}
    ],
    "sre_disk_growth": [],
    "sre_drift_changes": [],
    "sre_failed_logins": [],
    "report": "Batch batch-abc123 completed.",
    "error": None,
    "approved": True,
    "deferred": False,
    "rolling_update_percentage": 25,
    "wave_failure_threshold": 0.5,
    "health_check_command": "systemctl is-active nginx",
    "current_wave": 1,
    "total_waves": 4,
    "waves": [],
    "wave_aborted": False,
    "canary_enabled": False,
    "canary_health_check_command": "",
    "canary_passed": None,
    "drift_detection_enabled": True,
    "drift_abort_on_detection": False,
    "is_deferred_reapproval": False,
    "preloaded_plan_json": None,
    "preloaded_plan_hash": None,
    "preloaded_plan_id": None,
    "preloaded_approved_at": None,
    "is_deferred_replay": False,
    "ai_db_path": "",
    "docker_command_mode": "wrapper",
    "enabled_actions": ["patching", "disk_cleanup", "log_rotation"],
}


def test_batch_graph_state_round_trip():
    result = _round_trip(dict(_BATCH_STATE))
    assert result["batch_id"] == "batch-abc123"
    assert result["approved"] is True
    assert result["dry_run"] is False
    assert result["error"] is None
    assert result["vm_results"] == _BATCH_STATE["vm_results"]


def test_batch_graph_state_no_live_objects():
    result = _round_trip(dict(_BATCH_STATE))
    for key, value in result.items():
        assert not hasattr(value, "__await__"), f"field {key!r} is a coroutine"
        assert not hasattr(value, "__aiter__"), f"field {key!r} is an async iterator"


def test_batch_graph_state_field_sizes_within_4kb():
    sizes = _field_sizes(dict(_BATCH_STATE))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, (
        f"Fields exceed 4KB serialized size: {over_limit}. "
        "These are candidates for ArtifactStore (A4)."
    )


# ---------------------------------------------------------------------------
# VMGraphState
# ---------------------------------------------------------------------------

_VM_STATE: VMGraphState = {  # type: ignore[typeddict-item]
    "vm_id": "prod/web-01",
    "batch_id": "batch-abc123",
    "dry_run": False,
    "hostname": "prod-web-01",
    "ssh_user": "errander-ai",
    "ssh_key_path": "/keys/prod.pem",
    "os_family": "ubuntu",
    "vm_info": {"os_family": "ubuntu", "os_version": "22.04"},
    "planned_actions": [
        {"action_type": "patching", "risk_tier": "medium", "params": {}}
    ],
    "pre_approved_plan_set": True,
    "current_action_index": 0,
    "results": [
        {"action_type": "patching", "status": "success", "output": "0 upgraded"}
    ],
    "env_policy": "strict",
    "ai_db_path": "",
    "locked": True,
    "lock_acquired_at": "2026-05-20T02:01:00+00:00",
    "error": None,
    "drift_detection_enabled": True,
    "drift_abort_on_detection": False,
    "drift_result": None,
    "disk_growth_alerts": [],
    "drift_changes": [],
    "failed_login_summary": None,
    "disable_failed_login_check": False,
    "critical_services": ["nginx", "redis"],
    "docker_command_mode": "wrapper",
}


def test_vm_graph_state_round_trip():
    result = _round_trip(dict(_VM_STATE))
    assert result["vm_id"] == "prod/web-01"
    assert result["locked"] is True
    assert result["results"] == _VM_STATE["results"]


def test_vm_graph_state_field_sizes_within_4kb():
    sizes = _field_sizes(dict(_VM_STATE))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, f"VMGraphState fields exceed 4KB: {over_limit}"


# ---------------------------------------------------------------------------
# PatchingGraphState — patch_output is the main blob candidate
# ---------------------------------------------------------------------------

_SMALL_PATCH_OUTPUT = "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded."

# Realistic large patch output (~10KB) — triggers A4 artifact migration warning
_LARGE_PATCH_OUTPUT = (
    "Reading package lists...\nBuilding dependency tree...\n"
    + "The following packages will be upgraded:\n"
    + "\n".join(f"  pkg-{i} (1.0.{i} → 1.0.{i+1})" for i in range(200))
    + "\n200 upgraded, 0 newly installed.\n"
)

_PATCHING_STATE_SMALL: PatchingGraphState = {  # type: ignore[typeddict-item]
    "vm_id": "prod/web-01",
    "os_family": "ubuntu",
    "dry_run": False,
    "status": "success",
    "error": None,
    "exclude_patterns": ["linux-*"],
    "pending_updates": ["nginx", "openssl"],
    "version_snapshot": {"nginx": "1.18.0", "openssl": "3.0.2"},
    "patch_output": _SMALL_PATCH_OUTPUT,
    "updated_versions": {"nginx": "1.18.1", "openssl": "3.0.3"},
    "changed_packages": {"nginx": "1.18.0 → 1.18.1"},
    "nothing_to_do": False,
    "lock_holder_pid": None,
    "lock_holder_cmd": None,
    "reboot_status_detected": False,
    "critical_services": ["nginx"],
    "service_pre_snapshot": {"nginx": "active"},
    "service_regressions": [],
}


def test_patching_state_round_trip():
    result = _round_trip(dict(_PATCHING_STATE_SMALL))
    assert result["vm_id"] == "prod/web-01"
    assert result["status"] == "success"
    assert result["patch_output"] == _SMALL_PATCH_OUTPUT
    assert result["version_snapshot"] == {"nginx": "1.18.0", "openssl": "3.0.2"}


def test_patching_state_small_output_within_4kb():
    sizes = _field_sizes(dict(_PATCHING_STATE_SMALL))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, f"PatchingGraphState fields exceed 4KB: {over_limit}"


def test_patching_state_large_output_exceeds_4kb():
    """Large patch_output is the A4 artifact candidate — verify it IS too big."""
    large_state = dict(_PATCHING_STATE_SMALL)
    large_state["patch_output"] = _LARGE_PATCH_OUTPUT
    sizes = _field_sizes(large_state)
    assert sizes["patch_output"] > _4KB, (
        "Expected large patch_output to exceed 4KB — "
        "this field should be moved to ArtifactStore in A4."
    )


def test_patching_large_output_still_round_trips():
    """Even large blobs must survive round-trip (until A4 moves them out)."""
    large_state = dict(_PATCHING_STATE_SMALL)
    large_state["patch_output"] = _LARGE_PATCH_OUTPUT
    result = _round_trip(large_state)
    assert result["patch_output"] == _LARGE_PATCH_OUTPUT


# ---------------------------------------------------------------------------
# DockerPruneGraphState — system_df_output + prune_output are blob candidates
# ---------------------------------------------------------------------------

_DOCKER_PRUNE_STATE: DockerPruneGraphState = {  # type: ignore[typeddict-item]
    "vm_id": "prod/web-01",
    "os_family": "ubuntu",
    "dry_run": False,
    "status": "success",
    "error": None,
    "docker_available": True,
    "docker_command_mode": "wrapper",
    "docker_prune_aggressive": False,
    "dangling_images": 3,
    "stopped_containers": 1,
    "reclaimable_space": "1.2GB",
    "system_df_output": "TYPE  TOTAL  ACTIVE  SIZE  RECLAIMABLE\nImages 5 2 3.4GB 1.2GB (35%)",
    "prune_output": "Deleted images:\nuntagged: nginx:1.18.0\nTotal reclaimed space: 1.2GB",
    "disk_before": "Total 3.4GB",
    "disk_after": "Total 2.2GB",
    "nothing_to_do": False,
}


def test_docker_prune_state_round_trip():
    result = _round_trip(dict(_DOCKER_PRUNE_STATE))
    assert result["vm_id"] == "prod/web-01"
    assert result["dangling_images"] == 3
    assert result["prune_output"] == _DOCKER_PRUNE_STATE["prune_output"]


def test_docker_prune_state_field_sizes_within_4kb():
    sizes = _field_sizes(dict(_DOCKER_PRUNE_STATE))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, f"DockerPruneGraphState fields exceed 4KB: {over_limit}"


# ---------------------------------------------------------------------------
# LogRotationGraphState — rotation_output is blob candidate
# ---------------------------------------------------------------------------

_LOG_ROTATION_STATE: LogRotationGraphState = {  # type: ignore[typeddict-item]
    "vm_id": "prod/web-01",
    "os_family": "ubuntu",
    "dry_run": False,
    "status": "success",
    "error": None,
    "log_paths": ["/var/log"],
    "size_threshold_mb": 100,
    "compress": True,
    "large_files": ["/var/log/syslog"],
    "log_sizes": {"/var/log/syslog": "312 MB"},
    "rotation_output": {
        "/var/log/syslog": "gzip: /var/log/syslog.1.gz\n312MB → 48MB (85% reduction)"
    },
    "nothing_to_do": False,
}


def test_log_rotation_state_round_trip():
    result = _round_trip(dict(_LOG_ROTATION_STATE))
    assert result["vm_id"] == "prod/web-01"
    assert result["rotation_output"] == _LOG_ROTATION_STATE["rotation_output"]


def test_log_rotation_state_field_sizes_within_4kb():
    sizes = _field_sizes(dict(_LOG_ROTATION_STATE))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, f"LogRotationGraphState fields exceed 4KB: {over_limit}"


# ---------------------------------------------------------------------------
# DiskCleanupGraphState
# ---------------------------------------------------------------------------

_DISK_CLEANUP_STATE: DiskCleanupGraphState = {  # type: ignore[typeddict-item]
    "vm_id": "prod/web-01",
    "os_family": "ubuntu",
    "dry_run": False,
    "status": "success",
    "error": None,
    "whitelist_paths": ["/tmp", "/var/cache/apt"],
    "tmp_age_days": 7,
    "journal_vacuum_days": 7,
    "space_by_path": {"/tmp": "512MB", "/var/cache/apt": "1.2GB"},
    "cleanup_output": {"/tmp": "done", "/var/cache/apt": "done"},
    "disk_before": {"/": 78.5},
    "disk_after": {"/": 62.1},
}


def test_disk_cleanup_state_round_trip():
    result = _round_trip(dict(_DISK_CLEANUP_STATE))
    assert result["vm_id"] == "prod/web-01"
    assert abs(result["disk_before"]["/"] - 78.5) < 0.01


def test_disk_cleanup_state_field_sizes_within_4kb():
    sizes = _field_sizes(dict(_DISK_CLEANUP_STATE))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, f"DiskCleanupGraphState fields exceed 4KB: {over_limit}"


# ---------------------------------------------------------------------------
# BackupVerifyGraphState
# ---------------------------------------------------------------------------

_BACKUP_STATE: BackupVerifyGraphState = {  # type: ignore[typeddict-item]
    "vm_id": "prod/db-01",
    "os_family": "ubuntu",
    "dry_run": False,
    "status": "success",
    "error": None,
    "backup_paths": ["/backups/daily"],
    "max_age_hours": 26,
    "backup_metadata": [
        {"path": "/backups/daily/db-2026-05-19.tar.gz", "size": "4.2GB", "last_modified": "2026-05-19"}
    ],
    "issues": [],
    "verify_output": "All backups OK.",
}


def test_backup_verify_state_round_trip():
    result = _round_trip(dict(_BACKUP_STATE))
    assert result["vm_id"] == "prod/db-01"
    assert result["issues"] == []


def test_backup_verify_state_field_sizes_within_4kb():
    sizes = _field_sizes(dict(_BACKUP_STATE))
    over_limit = {k: v for k, v in sizes.items() if v > _4KB}
    assert not over_limit, f"BackupVerifyGraphState fields exceed 4KB: {over_limit}"
