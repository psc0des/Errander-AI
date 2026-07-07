"""Tests for the read-only investigation tools (fable-plan Phase 2).

Locks the guardrails: read-only, arg validation (injection rejected), output
caps, and unknown-tool / bad-args handling that never raises.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from errander.agent.investigation_tools import (
    ReadOnlyTool,
    ToolRegistry,
    build_readonly_tools,
)


def _stores() -> dict[str, AsyncMock]:
    audit = AsyncMock()
    audit.get_events.return_value = []
    disk = AsyncMock()
    disk.get_distinct_mountpoints.return_value = []
    vm_facts = AsyncMock()
    vm_facts.action_outcomes.return_value = []
    vm_facts.reboot_pattern.return_value = None
    return {"audit": audit, "disk": disk, "vm_facts": vm_facts}


def _registry(inventory: list[dict[str, str]] | None = None) -> ToolRegistry:
    s = _stores()
    return build_readonly_tools(
        audit_store=s["audit"],
        disk_history=s["disk"],
        vm_facts=s["vm_facts"],
        inventory_vms=inventory if inventory is not None else [],
    )


class TestDispatchSafety:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_not_raise(self) -> None:
        reg = _registry()
        out = await reg.dispatch("delete_everything", "{}")
        assert out.startswith("ERROR: unknown tool")

    @pytest.mark.asyncio
    async def test_bad_json_args_returns_error(self) -> None:
        reg = _registry()
        out = await reg.dispatch("get_audit_events", "{not json")
        assert "not valid JSON" in out

    @pytest.mark.asyncio
    async def test_tool_exception_becomes_error_string(self) -> None:
        async def boom(_args: dict[str, Any]) -> str:
            raise RuntimeError("kaboom")
        reg = ToolRegistry([ReadOnlyTool(
            name="t", description="d",
            parameters={"type": "object", "properties": {}}, run=boom,
        )])
        out = await reg.dispatch("t", "{}")
        assert "ERROR: tool t failed" in out

    @pytest.mark.asyncio
    async def test_result_is_capped(self) -> None:
        async def huge(_args: dict[str, Any]) -> str:
            return "x" * 10_000
        reg = ToolRegistry([ReadOnlyTool(
            name="t", description="d",
            parameters={"type": "object", "properties": {}}, run=huge,
        )])
        out = await reg.dispatch("t", "{}")
        assert len(out) <= 2000


class TestArgValidation:
    @pytest.mark.asyncio
    async def test_disk_trend_requires_valid_vm_id(self) -> None:
        reg = _registry()
        out = await reg.dispatch("get_disk_trend", '{"vm_id": "web 01; rm -rf /"}')
        assert "ERROR" in out and "vm_id" in out

    @pytest.mark.asyncio
    async def test_vm_facts_rejects_injection(self) -> None:
        reg = _registry()
        out = await reg.dispatch("get_vm_facts", '{"vm_id": "../etc/passwd"}')
        assert "ERROR" in out

    @pytest.mark.asyncio
    async def test_audit_limit_capped(self) -> None:
        s = _stores()
        reg = build_readonly_tools(
            audit_store=s["audit"], disk_history=s["disk"],
            vm_facts=s["vm_facts"], inventory_vms=[],
        )
        await reg.dispatch("get_audit_events", '{"limit": 99999}')
        # The store was called with the hard-capped limit, not 99999.
        _, kwargs = s["audit"].get_events.call_args
        assert kwargs["limit"] <= 200


class TestRegistryComposition:
    def test_prometheus_elk_included_only_when_configured(self) -> None:
        base = _registry()
        assert "get_vm_metrics" not in base.names
        assert "search_vm_errors" not in base.names

        s = _stores()
        with_ext = build_readonly_tools(
            audit_store=s["audit"], disk_history=s["disk"], vm_facts=s["vm_facts"],
            inventory_vms=[], prometheus_client=AsyncMock(), elk_client=AsyncMock(),
        )
        assert "get_vm_metrics" in with_ext.names
        assert "search_vm_errors" in with_ext.names

    def test_core_tools_always_present(self) -> None:
        reg = _registry()
        assert {"get_audit_events", "get_disk_trend", "get_vm_facts",
                "list_inventory"} <= set(reg.names)

    @pytest.mark.asyncio
    async def test_list_inventory_filters_by_env(self) -> None:
        reg = _registry([
            {"vm_id": "web-01", "env": "prod", "os_family": "ubuntu"},
            {"vm_id": "dev-01", "env": "dev", "os_family": "ubuntu"},
        ])
        out = await reg.dispatch("list_inventory", '{"env": "prod"}')
        assert "web-01" in out
        assert "dev-01" not in out
