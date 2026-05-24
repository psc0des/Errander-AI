"""Tests for run_env_batch() inventory merge logic.

Verifies that YAML targets + DB overrides (disable/add) are merged correctly
before being passed to the batch graph.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from errander.config.schema import EnvironmentSchema, TargetSchema
from errander.safety.overrides import OverridesStore


def _make_env(targets: list[dict[str, str]]) -> EnvironmentSchema:
    """Build a minimal EnvironmentSchema with the given targets."""
    return EnvironmentSchema(
        ssh_user="ubuntu",
        ssh_key_path="~/.ssh/key.pem",
        targets=[
            TargetSchema(
                name=t["name"],
                host=t["host"],
                os_family=t.get("os_family", "ubuntu"),
            )
            for t in targets
        ],
    )


def _make_overrides_store(overrides: list[dict[str, Any]]) -> AsyncMock:
    """Build a mock OverridesStore that returns the given inventory override rows."""
    mock = AsyncMock(spec=OverridesStore)
    mock.get_inventory_overrides.return_value = overrides
    return mock


async def _call_run_env_batch(
    env_name: str,
    env_schema: EnvironmentSchema,
    overrides_store=None,
) -> dict[str, Any]:
    """Call run_env_batch with mocked dependencies; returns last ainvoke() call kwargs."""
    from errander.config.settings import Settings, SRESignalSettings
    from errander.execution.sandbox import SandboxExecutor
    from errander.execution.ssh import SSHConnectionManager
    from errander.main import run_env_batch
    from errander.safety.audit import AuditStore
    from errander.safety.locking import FileLocker
    settings = MagicMock(spec=Settings)
    settings.audit_db_url = ":memory:"
    settings.sre_signals = SRESignalSettings()

    captured: dict[str, Any] = {}

    compiled_graph = AsyncMock()
    async def _ainvoke(state, *a, **kw):
        captured["state"] = state
        return {"batch_id": "test-batch", "vm_results": [], "report": None, "error": None}

    compiled_graph.ainvoke.side_effect = _ainvoke

    graph_mock = MagicMock()
    graph_mock.compile.return_value = compiled_graph

    with patch("errander.agent.graph.build_batch_graph", return_value=graph_mock), \
         patch("errander.main._build_maintenance_window", return_value=None):
        await run_env_batch(
            env_name=env_name,
            env_schema=env_schema,
            settings=settings,
            executor=MagicMock(spec=SandboxExecutor),
            locker=MagicMock(spec=FileLocker),
            ssh_manager=MagicMock(spec=SSHConnectionManager),
            audit_store=MagicMock(spec=AuditStore),
            overrides_store=overrides_store,
        )

    return captured.get("state", {})


class TestInventoryMerge:
    async def test_yaml_only_no_overrides(self):
        env = _make_env([
            {"name": "web-01", "host": "10.0.1.1"},
            {"name": "db-01", "host": "10.0.1.2"},
        ])
        state = await _call_run_env_batch("production", env, overrides_store=None)
        targets = state["targets"]
        assert len(targets) == 2
        vm_ids = {t["vm_id"] for t in targets}
        assert "production/web-01" in vm_ids
        assert "production/db-01" in vm_ids

    async def test_yaml_override_disables_vm(self):
        env = _make_env([
            {"name": "web-01", "host": "10.0.1.1"},
            {"name": "db-01", "host": "10.0.1.2"},
        ])
        overrides_store = _make_overrides_store([
            {
                "vm_name": "web-01",
                "source": "yaml_override",
                "disabled": True,
                "host": None, "ssh_user": None, "ssh_key_path": None, "os_family": None,
            }
        ])
        state = await _call_run_env_batch("production", env, overrides_store=overrides_store)
        targets = state["targets"]
        assert len(targets) == 1
        assert targets[0]["vm_id"] == "production/db-01"

    async def test_yaml_override_enabled_keeps_vm(self):
        env = _make_env([{"name": "web-01", "host": "10.0.1.1"}])
        overrides_store = _make_overrides_store([
            {
                "vm_name": "web-01",
                "source": "yaml_override",
                "disabled": False,
                "host": None, "ssh_user": None, "ssh_key_path": None, "os_family": None,
            }
        ])
        state = await _call_run_env_batch("production", env, overrides_store=overrides_store)
        assert len(state["targets"]) == 1

    async def test_db_addition_appended(self):
        env = _make_env([{"name": "web-01", "host": "10.0.1.1"}])
        overrides_store = _make_overrides_store([
            {
                "vm_name": "temp-worker",
                "source": "db_addition",
                "disabled": False,
                "host": "10.0.1.99",
                "ssh_user": "admin",
                "ssh_key_path": "/keys/temp.pem",
                "os_family": "ubuntu",
            }
        ])
        state = await _call_run_env_batch("production", env, overrides_store=overrides_store)
        targets = state["targets"]
        assert len(targets) == 2
        vm_ids = {t["vm_id"] for t in targets}
        assert "production/temp-worker" in vm_ids

    async def test_disabled_db_addition_excluded(self):
        env = _make_env([{"name": "web-01", "host": "10.0.1.1"}])
        overrides_store = _make_overrides_store([
            {
                "vm_name": "temp-worker",
                "source": "db_addition",
                "disabled": True,
                "host": "10.0.1.99",
                "ssh_user": None, "ssh_key_path": None, "os_family": None,
            }
        ])
        state = await _call_run_env_batch("production", env, overrides_store=overrides_store)
        assert len(state["targets"]) == 1
        assert state["targets"][0]["vm_id"] == "production/web-01"

    async def test_disable_all_yaml_targets(self):
        env = _make_env([
            {"name": "web-01", "host": "10.0.1.1"},
            {"name": "web-02", "host": "10.0.1.2"},
        ])
        overrides_store = _make_overrides_store([
            {"vm_name": "web-01", "source": "yaml_override", "disabled": True,
             "host": None, "ssh_user": None, "ssh_key_path": None, "os_family": None},
            {"vm_name": "web-02", "source": "yaml_override", "disabled": True,
             "host": None, "ssh_user": None, "ssh_key_path": None, "os_family": None},
        ])
        state = await _call_run_env_batch("production", env, overrides_store=overrides_store)
        assert state["targets"] == []

    async def test_db_addition_uses_env_ssh_defaults_when_null(self):
        env = _make_env([{"name": "web-01", "host": "10.0.1.1"}])
        overrides_store = _make_overrides_store([
            {
                "vm_name": "temp-worker",
                "source": "db_addition",
                "disabled": False,
                "host": "10.0.1.99",
                "ssh_user": None,
                "ssh_key_path": None,
                "os_family": None,
            }
        ])
        state = await _call_run_env_batch("production", env, overrides_store=overrides_store)
        temp = next(t for t in state["targets"] if "temp-worker" in t["vm_id"])
        assert temp["ssh_user"] == "ubuntu"   # env_schema.ssh_user
        assert temp["ssh_key_path"] == "~/.ssh/key.pem"  # env_schema.ssh_key_path
        assert temp["os_family"] == "ubuntu"  # default fallback

    async def test_target_name_field_stripped_from_output(self):
        env = _make_env([{"name": "web-01", "host": "10.0.1.1"}])
        state = await _call_run_env_batch("production", env, overrides_store=None)
        for t in state["targets"]:
            assert "_name" not in t

    async def test_no_overrides_store_passes_all_yaml(self):
        env = _make_env([
            {"name": "a", "host": "10.0.0.1"},
            {"name": "b", "host": "10.0.0.2"},
            {"name": "c", "host": "10.0.0.3"},
        ])
        state = await _call_run_env_batch("staging", env, overrides_store=None)
        assert len(state["targets"]) == 3
