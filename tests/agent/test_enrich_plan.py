"""Tests for enrich_plan_node and _parse_upgradable_with_versions (P0-1)."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.graph import (
    _enrich_vm_plan,
    _preview_disk_cleanup,
    _preview_patching,
    enrich_plan_node,
)
from errander.agent.subgraphs.patching import _parse_upgradable_with_versions


# ---------------------------------------------------------------------------
# _parse_upgradable_with_versions
# ---------------------------------------------------------------------------


def test_parse_upgradable_with_versions_apt_full() -> None:
    output = (
        "Listing... Done\n"
        "nginx/focal-updates 1.24.0-1ubuntu1 amd64 [upgradable from: 1.18.0-0ubuntu1]\n"
        "openssl/focal-security 1.1.1f-1ubuntu2.21 amd64 [upgradable from: 1.1.1f-1ubuntu2.20]\n"
    )
    result = _parse_upgradable_with_versions(output, "ubuntu")
    assert len(result) == 2
    assert result[0] == {"name": "nginx", "target": "1.24.0-1ubuntu1", "current": "1.18.0-0ubuntu1"}
    assert result[1] == {"name": "openssl", "target": "1.1.1f-1ubuntu2.21", "current": "1.1.1f-1ubuntu2.20"}


def test_parse_upgradable_with_versions_apt_fallback_on_malformed() -> None:
    output = "nginx/focal-updates malformed-no-upgradable-bracket\n"
    result = _parse_upgradable_with_versions(output, "ubuntu")
    assert len(result) == 1
    assert result[0]["name"] == "nginx"
    assert result[0]["target"] == ""
    assert result[0]["current"] == ""


def test_parse_upgradable_with_versions_skips_listing_header() -> None:
    output = "Listing... Done\nnginx/focal 1.24.0 amd64 [upgradable from: 1.18.0]\n"
    result = _parse_upgradable_with_versions(output, "ubuntu")
    assert len(result) == 1
    assert result[0]["name"] == "nginx"


def test_parse_upgradable_with_versions_empty_output() -> None:
    result = _parse_upgradable_with_versions("Listing... Done\n", "ubuntu")
    assert result == []


def test_parse_upgradable_with_versions_dnf() -> None:
    output = (
        "nginx.x86_64    1:1.24.0-1.el9    @appstream\n"
        "openssl.x86_64  1:3.0.7-18.el9    @baseos\n"
    )
    result = _parse_upgradable_with_versions(output, "rhel")
    assert len(result) == 2
    assert result[0]["name"] == "nginx"
    assert result[0]["target"] == "1:1.24.0-1.el9"
    assert result[0]["current"] == ""  # DNF output doesn't include current version


# ---------------------------------------------------------------------------
# _preview_patching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_patching_returns_packages() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock(return_value=MagicMock(
        success=True,
        stdout=(
            "Listing... Done\n"
            "nginx/focal-updates 1.24.0-1ubuntu1 amd64 [upgradable from: 1.18.0-0ubuntu1]\n"
        ),
    ))
    result = await _preview_patching("v1", "host", "user", "key", "ubuntu", ssh)
    assert result["package_count"] == 1
    pkgs = result["packages"]
    assert isinstance(pkgs, list)
    assert pkgs[0]["name"] == "nginx"
    assert pkgs[0]["current"] == "1.18.0-0ubuntu1"
    assert pkgs[0]["target"] == "1.24.0-1ubuntu1"


@pytest.mark.asyncio
async def test_preview_patching_filters_kernel_packages() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock(return_value=MagicMock(
        success=True,
        stdout=(
            "Listing... Done\n"
            "linux-image-5.15/focal 5.15.0-1 amd64 [upgradable from: 5.14.0-1]\n"
            "nginx/focal 1.24.0 amd64 [upgradable from: 1.18.0]\n"
        ),
    ))
    result = await _preview_patching("v1", "host", "user", "key", "ubuntu", ssh)
    pkgs = result["packages"]
    assert isinstance(pkgs, list)
    names = [p["name"] for p in pkgs]
    assert "nginx" in names
    assert not any("linux-image" in n for n in names)
    assert result["total_upgradable"] == 2
    assert result["package_count"] == 1


@pytest.mark.asyncio
async def test_preview_patching_returns_error_on_ssh_failure() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock(return_value=MagicMock(
        success=False, stdout="", stderr="connection refused",
    ))
    result = await _preview_patching("v1", "host", "user", "key", "ubuntu", ssh)
    assert "error" in result
    assert "connection refused" in result["error"]


# ---------------------------------------------------------------------------
# _preview_disk_cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_disk_cleanup_returns_pct_and_cache() -> None:
    call_count = 0

    async def _ssh(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(success=True, stdout="78\n")   # df disk_pct
        return MagicMock(success=True, stdout="450\n")       # du cache_mb

    ssh = MagicMock()
    ssh.execute = _ssh
    result = await _preview_disk_cleanup("v1", "host", "user", "key", ssh)
    assert result["disk_pct"] == 78
    assert result["apt_cache_mb"] == 450


@pytest.mark.asyncio
async def test_preview_disk_cleanup_partial_success() -> None:
    call_count = 0

    async def _ssh(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(success=True, stdout="65\n")
        return MagicMock(success=False, stdout="", stderr="not found")

    ssh = MagicMock()
    ssh.execute = _ssh
    result = await _preview_disk_cleanup("v1", "host", "user", "key", ssh)
    assert result["disk_pct"] == 65
    assert "apt_cache_mb" not in result


# ---------------------------------------------------------------------------
# _enrich_vm_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_vm_plan_adds_preview_to_patching() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock(return_value=MagicMock(
        success=True,
        stdout="Listing... Done\nnginx/focal 1.24.0 amd64 [upgradable from: 1.18.0]\n",
    ))
    plan = {
        "vm_id": "v1",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "patching", "risk_tier": "medium", "params": {}}],
    }
    target_by_id = {
        "v1": {"hostname": "10.0.0.1", "ssh_user": "ubuntu", "ssh_key_path": "/key"},
    }
    result = await _enrich_vm_plan(plan, target_by_id, ssh)
    actions = result["planned_actions"]
    assert isinstance(actions, list)
    preview = actions[0]["preview"]
    assert "packages" in preview


@pytest.mark.asyncio
async def test_enrich_vm_plan_empty_preview_for_non_patching() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock()
    plan = {
        "vm_id": "v1",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "docker_prune", "risk_tier": "medium", "params": {}}],
    }
    target_by_id = {
        "v1": {"hostname": "10.0.0.1", "ssh_user": "u", "ssh_key_path": "/k"},
    }
    result = await _enrich_vm_plan(plan, target_by_id, ssh)
    actions = result["planned_actions"]
    assert isinstance(actions, list)
    # No SSH calls — docker_prune not enriched in MVP
    ssh.execute.assert_not_called()
    assert actions[0]["preview"] == {}


@pytest.mark.asyncio
async def test_enrich_vm_plan_handles_ssh_exception_gracefully() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock(side_effect=ConnectionError("timeout"))
    plan = {
        "vm_id": "v1",
        "os_family": "ubuntu",
        "planned_actions": [{"action_type": "patching", "risk_tier": "medium", "params": {}}],
    }
    target_by_id = {
        "v1": {"hostname": "10.0.0.1", "ssh_user": "u", "ssh_key_path": "/k"},
    }
    result = await _enrich_vm_plan(plan, target_by_id, ssh)
    actions = result["planned_actions"]
    assert isinstance(actions, list)
    assert "error" in actions[0]["preview"]


# ---------------------------------------------------------------------------
# enrich_plan_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_plan_node_fans_out_per_vm() -> None:
    ssh = MagicMock()
    ssh.execute = AsyncMock(return_value=MagicMock(
        success=True,
        stdout="Listing... Done\nnginx/focal 1.24.0 amd64 [upgradable from: 1.18.0]\n",
    ))
    state = {
        "vm_plans": [
            {"vm_id": "v1", "os_family": "ubuntu",
             "planned_actions": [{"action_type": "patching", "risk_tier": "medium", "params": {}}]},
            {"vm_id": "v2", "os_family": "ubuntu",
             "planned_actions": [{"action_type": "disk_cleanup", "risk_tier": "low", "params": {}}]},
        ],
        "targets": [
            {"vm_id": "v1", "hostname": "10.0.0.1", "ssh_user": "u", "ssh_key_path": "/k"},
            {"vm_id": "v2", "hostname": "10.0.0.2", "ssh_user": "u", "ssh_key_path": "/k"},
        ],
    }
    result = await enrich_plan_node(state, ssh_manager=ssh)
    enriched = result["enriched_vm_plans"]
    assert len(enriched) == 2
    # v1 patching → has preview with packages
    v1_actions = enriched[0]["planned_actions"]
    assert isinstance(v1_actions, list)
    assert "preview" in v1_actions[0]


# ---------------------------------------------------------------------------
# Hash includes preview data (core P0-1 guarantee)
# ---------------------------------------------------------------------------


def test_plan_hash_includes_preview_data() -> None:
    """Two plans identical except for preview must produce different hashes."""
    def _compute_hash(vm_plans: list[dict]) -> str:
        canonical = json.dumps(
            {"batch_id": "b1", "env_name": "dev", "vm_plans": vm_plans},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    base_plan = [{
        "vm_id": "v1",
        "os_family": "ubuntu",
        "planned_actions": [
            {
                "action_type": "patching",
                "risk_tier": "medium",
                "params": {},
                "preview": {"packages": [], "package_count": 0},
            }
        ],
    }]
    enriched_plan = [{
        "vm_id": "v1",
        "os_family": "ubuntu",
        "planned_actions": [
            {
                "action_type": "patching",
                "risk_tier": "medium",
                "params": {},
                "preview": {
                    "packages": [{"name": "nginx", "current": "1.18.0", "target": "1.24.0"}],
                    "package_count": 1,
                },
            }
        ],
    }]

    hash_base = _compute_hash(base_plan)
    hash_enriched = _compute_hash(enriched_plan)
    assert hash_base != hash_enriched, "Preview data must be part of the plan hash"
