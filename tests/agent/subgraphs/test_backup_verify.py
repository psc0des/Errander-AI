"""Tests for the backup verification sub-graph."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from errander.agent.subgraphs.backup_verify import (
    MANIFEST,
    BackupVerifyGraphState,
    assess_node,
    build_backup_verify_subgraph,
    route_after_validate,
    validate_node,
    verify_node,
)
from errander.execution.sandbox import SandboxExecutor
from errander.execution.ssh import SSHConnectionManager, SSHResult
from errander.models.actions import ActionStatus

# --- Helpers ---

def _make_result(stdout: str = "ok", exit_code: int = 0) -> SSHResult:
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr="", command="mocked")


def _base_state(**overrides: object) -> BackupVerifyGraphState:
    defaults: BackupVerifyGraphState = {
        "vm_id": "dev/web-01",
        "os_family": "ubuntu",
        "dry_run": True,
        "status": ActionStatus.PENDING.value,
        "error": None,
        "backup_paths": ["/backups/db.sql", "/backups/config.tar.gz"],
        "max_age_hours": 24,
        "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
        "username": "errander-ai",  # type: ignore[typeddict-item]
        "key_path": "/key",  # type: ignore[typeddict-item]
    }
    defaults.update(overrides)  # type: ignore[typeddict-item]
    return defaults


def _make_executor(dry_run: bool = True) -> SandboxExecutor:
    return SandboxExecutor(SSHConnectionManager(), dry_run=dry_run)


# --- Validate node tests ---

class TestValidateNode:
    def test_with_paths_passes(self) -> None:
        state = _base_state()
        result = validate_node(state)
        assert result["status"] == ActionStatus.PENDING.value

    def test_no_paths_skips(self) -> None:
        state = _base_state(backup_paths=[])
        result = validate_node(state)
        assert result["status"] == ActionStatus.SKIPPED.value


# --- Assess node tests ---

class TestAssessNode:
    async def test_collects_metadata(self) -> None:
        executor = _make_executor(dry_run=True)
        now_epoch = str(int(time.time()))
        stat_output = f"1048576 {now_epoch} /backups/db.sql"
        execute_mock = AsyncMock(return_value=_make_result(stat_output))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(backup_paths=["/backups/db.sql"])
            result = await assess_node(state, executor=executor)

        assert len(result["backup_metadata"]) == 1
        assert result["backup_metadata"][0]["exists"] == "true"
        assert result["backup_metadata"][0]["size"] == "1048576"

    async def test_handles_missing_file(self) -> None:
        executor = _make_executor(dry_run=True)
        execute_mock = AsyncMock(return_value=_make_result("MISSING /backups/gone.sql"))

        with patch.object(executor, "execute", execute_mock):
            state = _base_state(backup_paths=["/backups/gone.sql"])
            result = await assess_node(state, executor=executor)

        assert result["backup_metadata"][0]["exists"] == "false"


# --- Verify node tests ---

class TestVerifyNode:
    def test_healthy_backup_no_issues(self) -> None:
        now_epoch = str(int(time.time()) - 3600)  # 1 hour ago
        state = _base_state(
            backup_metadata=[{
                "path": "/backups/db.sql",
                "size": "1048576",
                "last_modified": now_epoch,
                "exists": "true",
            }],
            max_age_hours=24,
        )
        result = verify_node(state)
        assert result["issues"] == []
        assert result["status"] == ActionStatus.SUCCESS.value

    def test_flags_missing_backup(self) -> None:
        state = _base_state(
            backup_metadata=[{
                "path": "/backups/gone.sql",
                "size": "0",
                "last_modified": "0",
                "exists": "false",
            }],
        )
        result = verify_node(state)
        assert any("MISSING" in i for i in result["issues"])
        assert result["status"] == ActionStatus.NEEDS_MANUAL.value

    def test_flags_stale_backup(self) -> None:
        old_epoch = str(int(time.time()) - 48 * 3600)  # 48 hours ago
        state = _base_state(
            backup_metadata=[{
                "path": "/backups/db.sql",
                "size": "1048576",
                "last_modified": old_epoch,
                "exists": "true",
            }],
            max_age_hours=24,
        )
        result = verify_node(state)
        assert any("STALE" in i for i in result["issues"])

    def test_flags_empty_backup(self) -> None:
        now_epoch = str(int(time.time()))
        state = _base_state(
            backup_metadata=[{
                "path": "/backups/db.sql",
                "size": "0",
                "last_modified": now_epoch,
                "exists": "true",
            }],
        )
        result = verify_node(state)
        assert any("EMPTY" in i for i in result["issues"])


# --- Routing tests ---

class TestRouting:
    def test_route_after_validate_continues(self) -> None:
        state = _base_state(status=ActionStatus.PENDING.value)
        assert route_after_validate(state) == "assess"

    def test_route_after_validate_skips_when_no_paths(self) -> None:
        state = _base_state(status=ActionStatus.SKIPPED.value)
        assert route_after_validate(state) == "__end__"


# --- Graph builder tests ---

class TestBuildSubgraph:
    def test_graph_builds_without_error(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_backup_verify_subgraph(executor)
        assert graph is not None

    def test_graph_compiles(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_backup_verify_subgraph(executor)
        compiled = graph.compile()
        assert compiled is not None

    async def test_graph_skips_when_no_paths(self) -> None:
        executor = _make_executor(dry_run=True)
        graph = build_backup_verify_subgraph(executor)
        compiled = graph.compile()

        initial_state: BackupVerifyGraphState = {
            "vm_id": "dev/web-01",
            "os_family": "ubuntu",
            "dry_run": True,
            "backup_paths": [],
        }

        result = await compiled.ainvoke(initial_state)
        assert result["status"] == ActionStatus.SKIPPED.value

    async def test_graph_end_to_end(self) -> None:
        executor = _make_executor(dry_run=True)
        now_epoch = str(int(time.time()) - 3600)
        stat_output = f"1048576 {now_epoch} /backups/db.sql"
        execute_mock = AsyncMock(return_value=_make_result(stat_output))

        with patch.object(executor, "execute", execute_mock):
            graph = build_backup_verify_subgraph(executor)
            compiled = graph.compile()

            initial_state: BackupVerifyGraphState = {
                "vm_id": "dev/web-01",
                "os_family": "ubuntu",
                "dry_run": True,
                "backup_paths": ["/backups/db.sql"],
                "max_age_hours": 24,
                "hostname": "10.0.1.10",  # type: ignore[typeddict-item]
                "username": "errander-ai",  # type: ignore[typeddict-item]
                "key_path": "/key",  # type: ignore[typeddict-item]
            }

            result = await compiled.ainvoke(initial_state)

        assert result["status"] == ActionStatus.SUCCESS.value
        assert result["issues"] == []


# --- P2-2: manifest risk_tier consistency ---

class TestBackupVerifyManifest:
    """P2-2: MANIFEST risk_tier must match the docstring — both must say LOW."""

    def test_manifest_risk_tier_is_low(self) -> None:
        assert MANIFEST.risk_tier == "LOW", (
            "backup_verify is a read-only freshness check — risk_tier must be LOW, "
            "matching the docstring. A prior version incorrectly said 'High'."
        )

    def test_manifest_name_matches_action_type(self) -> None:
        assert MANIFEST.name == "backup_verify"
