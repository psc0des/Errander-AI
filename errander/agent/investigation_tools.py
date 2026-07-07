"""Read-only tools for the Layer A investigation agent (fable-plan Phase 2).

Each tool wraps an EXISTING read path (audit DB, disk history, VM facts,
inventory, and — when configured — Prometheus/ELK) behind a strict JSON
schema. Every tool is **read-only**: it never opens an SSH exec, writes a
store, posts to Slack, or touches Layer B. Tool *results are untrusted input*
(logs/labels can carry attacker-influenced text), so each result is size-
capped here and redacted by the agent before it re-enters the model.

Layer A contract (mirrors operator_assistant.py): this module must not import
SandboxExecutor, FileLocker, ApprovalRequestStore, ProposalStore, SSH
execution, or any agent.subgraphs / agent.graph path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from errander.integrations.elk import ElkClient
    from errander.integrations.prometheus import PrometheusClient
    from errander.safety.audit import AuditStore
    from errander.safety.disk_history import VMDiskHistoryStore
    from errander.safety.vm_facts import VMFactsStore

logger = logging.getLogger(__name__)

#: Reject identifier args that carry shell metacharacters / path traversal.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

#: Hard cap on any single tool result (chars) — bounds context blowup and cost.
_TOOL_RESULT_MAX_CHARS = 2000

#: Hard ceiling on row/event limits a tool will honor, regardless of args.
_MAX_LIMIT = 200


def _cap(text: str) -> str:
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        return text[: _TOOL_RESULT_MAX_CHARS - 1] + "…"
    return text


def _valid_identifier(value: object) -> str | None:
    """Return the value if it is a safe identifier, else None."""
    if isinstance(value, str) and _IDENTIFIER_RE.match(value):
        return value
    return None


@dataclass
class ReadOnlyTool:
    """One read-only tool: a JSON-schema'd wrapper over an existing read path."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]

    def schema(self) -> dict[str, Any]:
        """OpenAI-compatible function-tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """A set of read-only tools plus dispatch, for the investigation loop."""

    def __init__(self, tools: list[ReadOnlyTool]) -> None:
        self._tools = {t.name: t for t in tools}

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    async def dispatch(self, name: str, arguments: str) -> str:
        """Run a tool by name with raw JSON arguments. Never raises.

        Returns a capped string result, or a structured error string the model
        can read and recover from (unknown tool, bad args, tool failure).
        """
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("Investigation agent requested unknown tool %r", name)
            return f"ERROR: unknown tool {name!r}. Available: {', '.join(self.names)}"
        try:
            args = json.loads(arguments) if arguments.strip() else {}
            if not isinstance(args, dict):
                return "ERROR: tool arguments must be a JSON object"
        except json.JSONDecodeError:
            return "ERROR: tool arguments were not valid JSON"
        try:
            return _cap(await tool.run(args))
        except Exception as exc:  # noqa: BLE001 — a tool failure must not kill the loop
            logger.warning("Tool %s failed: %s", name, exc)
            return f"ERROR: tool {name} failed: {exc}"


# ---------------------------------------------------------------------------
# Tool implementations (each closes over an existing read-only store handle)
# ---------------------------------------------------------------------------


def _audit_events_tool(audit_store: AuditStore) -> ReadOnlyTool:
    async def run(args: dict[str, Any]) -> str:
        vm_id = _valid_identifier(args.get("vm_id")) if args.get("vm_id") else None
        action_type = (
            _valid_identifier(args.get("action_type")) if args.get("action_type") else None
        )
        limit = min(int(args.get("limit", 20) or 20), _MAX_LIMIT)
        events = await audit_store.get_events(
            vm_id=vm_id, action_type=action_type, limit=limit,
        )
        if not events:
            return "No matching audit events."
        lines = [
            f"{e.timestamp:%Y-%m-%d %H:%M} {e.event_type} "
            f"vm={e.vm_id or '-'} action={e.action_type or '-'} {e.detail}"
            for e in events
        ]
        return "\n".join(lines)

    return ReadOnlyTool(
        name="get_audit_events",
        description=(
            "Read recent audit-trail events (actions, approvals, drift, probes). "
            "Optionally filter by vm_id and/or action_type."
        ),
        parameters={
            "type": "object",
            "properties": {
                "vm_id": {"type": "string", "description": "Filter by VM id"},
                "action_type": {
                    "type": "string",
                    "description": "Filter by action (e.g. disk_cleanup, patching)",
                },
                "limit": {"type": "integer", "description": "Max events (<=200)"},
            },
        },
        run=run,
    )


def _disk_trend_tool(disk_history: VMDiskHistoryStore) -> ReadOnlyTool:
    async def run(args: dict[str, Any]) -> str:
        vm_id = _valid_identifier(args.get("vm_id"))
        if vm_id is None:
            return "ERROR: vm_id is required and must be a valid identifier"
        window_days = min(int(args.get("window_days", 7) or 7), 90)
        mountpoints = await disk_history.get_distinct_mountpoints(vm_id)
        if not mountpoints:
            return f"No disk history for {vm_id}."
        out: list[str] = []
        for mp in mountpoints[:10]:
            points = await disk_history.get_window(vm_id, mp, window_days)
            if len(points) < 2:
                continue
            first, last = points[0], points[-1]
            out.append(
                f"{mp}: {first.used_pct:.0f}% → {last.used_pct:.0f}% "
                f"over {window_days}d ({len(points)} samples)"
            )
        return "\n".join(out) if out else f"No trend data for {vm_id} in {window_days}d."

    return ReadOnlyTool(
        name="get_disk_trend",
        description="Read disk-usage trend per mountpoint for a VM over a window.",
        parameters={
            "type": "object",
            "properties": {
                "vm_id": {"type": "string", "description": "VM id (required)"},
                "window_days": {"type": "integer", "description": "Window (<=90)"},
            },
            "required": ["vm_id"],
        },
        run=run,
    )


def _vm_facts_tool(vm_facts: VMFactsStore) -> ReadOnlyTool:
    async def run(args: dict[str, Any]) -> str:
        vm_id = _valid_identifier(args.get("vm_id"))
        if vm_id is None:
            return "ERROR: vm_id is required and must be a valid identifier"
        outcomes = await vm_facts.action_outcomes(vm_id)
        reboot = await vm_facts.reboot_pattern(vm_id)
        parts: list[str] = []
        if outcomes:
            parts.append(
                "action outcomes: "
                + ", ".join(
                    f"{o.action_type}={o.success_rate:.0%} over {o.sample_size} runs"
                    f" ({o.confidence})"
                    for o in outcomes
                )
            )
        if reboot is not None and reboot.sample_size > 0:
            parts.append(
                f"reboots after patching: {reboot.reboots_required_after_patching}"
                f"/{reboot.sample_size}"
            )
        return "; ".join(parts) if parts else f"No learned facts for {vm_id}."

    return ReadOnlyTool(
        name="get_vm_facts",
        description=(
            "Read learned facts about a VM: historical action success/failure "
            "rates and reboot patterns."
        ),
        parameters={
            "type": "object",
            "properties": {"vm_id": {"type": "string", "description": "VM id (required)"}},
            "required": ["vm_id"],
        },
        run=run,
    )


def _inventory_tool(vms: list[dict[str, str]]) -> ReadOnlyTool:
    async def run(args: dict[str, Any]) -> str:
        env = _valid_identifier(args.get("env")) if args.get("env") else None
        rows = [v for v in vms if env is None or v.get("env") == env]
        if not rows:
            return "No matching inventory."
        return "\n".join(
            f"{v.get('vm_id')} (env={v.get('env')}, os={v.get('os_family')})" for v in rows
        )

    return ReadOnlyTool(
        name="list_inventory",
        description="List fleet VMs (ids, env, OS family). Optionally filter by env.",
        parameters={
            "type": "object",
            "properties": {"env": {"type": "string", "description": "Filter by env"}},
        },
        run=run,
    )


def _prometheus_tool(client: PrometheusClient) -> ReadOnlyTool:
    async def run(args: dict[str, Any]) -> str:
        host = _valid_identifier(args.get("host"))
        if host is None:
            return "ERROR: host is required and must be a valid identifier"
        metrics = await client.fetch_vm_metrics(host)
        return "\n".join(metrics) if metrics else f"No Prometheus metrics for {host}."

    return ReadOnlyTool(
        name="get_vm_metrics",
        description="Read current CPU/mem/disk metrics for a host from Prometheus.",
        parameters={
            "type": "object",
            "properties": {"host": {"type": "string", "description": "Hostname (required)"}},
            "required": ["host"],
        },
        run=run,
    )


def _elk_tool(client: ElkClient) -> ReadOnlyTool:
    async def run(args: dict[str, Any]) -> str:
        host = _valid_identifier(args.get("host"))
        if host is None:
            return "ERROR: host is required and must be a valid identifier"
        errors = await client.fetch_vm_errors(host)
        return "\n".join(errors) if errors else f"No recent ELK errors for {host}."

    return ReadOnlyTool(
        name="search_vm_errors",
        description="Read recent error-level log lines for a host from ELK.",
        parameters={
            "type": "object",
            "properties": {"host": {"type": "string", "description": "Hostname (required)"}},
            "required": ["host"],
        },
        run=run,
    )


def build_readonly_tools(
    *,
    audit_store: AuditStore,
    disk_history: VMDiskHistoryStore,
    vm_facts: VMFactsStore,
    inventory_vms: list[dict[str, str]],
    prometheus_client: PrometheusClient | None = None,
    elk_client: ElkClient | None = None,
) -> ToolRegistry:
    """Assemble the read-only tool registry for the investigation agent.

    Prometheus/ELK tools are included only when their clients are configured
    (the agent works fine on the store-backed tools alone).
    """
    tools = [
        _audit_events_tool(audit_store),
        _disk_trend_tool(disk_history),
        _vm_facts_tool(vm_facts),
        _inventory_tool(inventory_vms),
    ]
    if prometheus_client is not None:
        tools.append(_prometheus_tool(prometheus_client))
    if elk_client is not None:
        tools.append(_elk_tool(elk_client))
    return ToolRegistry(tools)
