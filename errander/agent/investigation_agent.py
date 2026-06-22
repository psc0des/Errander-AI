"""Layer A agentic investigation — bounded read-only tool-calling loop.

Upgrades the open-ended `--ask` investigation path: instead of a single
fixed-context LLM call (operator_assistant.OperatorAssistant.investigate()),
the model is given a small set of read-only tools and a budget, and decides
which queries to run, observes results, and iterates (ReAct-style) until it
has enough evidence to answer.

Layer A contract — identical to operator_assistant.py:
  - Read-only: every tool wraps an existing read method; no tool writes to
    any store, opens an SSH exec, or triggers Layer B.
  - No SandboxExecutor, no FileLocker, no approval store.
  - LLM synthesizes; humans decide; Layer B executes.
  - Never raises to the caller and never blocks on LLM availability — any
    failure (unsupported tool-calling, LLM unreachable, budget exhausted,
    unparseable final answer) falls back to the existing deterministic
    OperatorAssistant.investigate() path.

This module deliberately does NOT touch operator_assistant.py's
deterministic path or decisions.prioritize_actions() — both remain
byte-for-byte unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from errander.agent.operator_assistant import OperatorAssistant
from errander.integrations.llm import _parse_response
from errander.models.analysis import AssistantResponse
from errander.models.events import EventType
from errander.observability.metrics import (
    INVESTIGATION_FALLBACK_TOTAL,
    INVESTIGATION_TOOL_CALLS_TOTAL,
)
from errander.safety.context_redactor import ContextRedactor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from errander.config.schema import InventoryConfig
    from errander.integrations.elk import ElkClient
    from errander.integrations.llm import LLMClient
    from errander.integrations.prometheus import PrometheusClient
    from errander.safety.ai_audit import AIDecisionStore
    from errander.safety.audit import AuditStore
    from errander.safety.baselines import BaselineStore
    from errander.safety.disk_history import VMDiskHistoryStore
    from errander.safety.vm_facts import VMFactsStore

logger = logging.getLogger(__name__)

_REDACTOR = ContextRedactor()

#: Defensive defaults — used when settings pass a non-positive value
#: (delta: protects against a misconfigured env var silently producing a
#: zero-iteration loop instead of a clearly-logged fallback).
_DEFAULT_MAX_TOOL_CALLS = 8
_DEFAULT_TIMEOUT_SECONDS = 180
#: Floor for the shrinking per-call timeout — never ask the LLM client for
#: less than this, even when the overall loop deadline has nearly elapsed.
_MIN_CALL_TIMEOUT_SECONDS = 10
#: Caps a single tool result's length before it re-enters the model —
#: matches ContextBudgeter's default max_chars_per_field.
_MAX_TOOL_RESULT_CHARS = 500
#: Hard cap on get_audit_events' limit regardless of requested value.
_MAX_AUDIT_EVENTS_LIMIT = 200
_MAX_DISK_WINDOW_DAYS = 30

_SYSTEM_PROMPT = (
    "You are Errander-AI, a supervised agentic AI SRE assistant operating in "
    "Layer A — you investigate and recommend, you NEVER execute commands and "
    "NEVER suggest the operator run a specific shell command. Use the "
    "available read-only tools to gather evidence before answering; call as "
    "few tools as needed to support a confident answer.\n\n"
    "Every tool result begins with a bracketed source id, e.g. "
    "[source_id=query_prometheus#1]. When you cite evidence for a finding, "
    "you MUST use the exact source_id string from a tool result you actually "
    "received this conversation — never invent one, never cite a tool you "
    "did not call.\n\n"
    "When you have enough evidence (or no further tool call would help), "
    "respond with ONLY valid JSON matching this schema — no prose, no "
    "markdown fences:\n"
    '{"summary": "<1-2 sentences>", '
    '"findings": [{"text": "<observation>", "evidence": ["<source_id>", ...]}, ...], '
    '"recommendations": ["<action for operator to consider>", ...], '
    '"risk_level": "low|medium|high|unknown"}\n'
    "A finding with no traceable source must set evidence to []."
)


@dataclass
class _ToolContext:
    """Bundles the store/client handles every tool handler may need."""

    audit_store: AuditStore
    disk_history_store: VMDiskHistoryStore
    inventory: InventoryConfig
    env_name: str | None
    prometheus_client: PrometheusClient | None
    elk_client: ElkClient | None
    vm_facts_store: VMFactsStore | None


@dataclass
class ToolSpec:
    """One read-only tool: its OpenAI function-calling schema + handler."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], _ToolContext], Awaitable[str]]
    available: Callable[[_ToolContext], bool]


def _known_hosts(ctx: _ToolContext) -> set[str]:
    """Every host/name from inventory — used to scope search_logs (defense
    in depth: a tool can only be pointed at hosts that are actually in the
    fleet, never an arbitrary attacker-supplied hostname)."""
    hosts: set[str] = set()
    envs = ctx.inventory.environments
    names = [ctx.env_name] if ctx.env_name else list(envs.keys())
    for name in names:
        env = envs.get(name)
        if env is None:
            continue
        for target in env.targets:
            hosts.add(target.host)
            hosts.add(target.name)
    return hosts


async def _tool_query_prometheus(args: dict[str, Any], ctx: _ToolContext) -> str:
    if ctx.prometheus_client is None:
        return "Prometheus is not configured for this environment."
    promql = str(args.get("promql", "")).strip()
    if not promql or len(promql) > 2000 or "\n" in promql:
        return "Error: invalid promql expression."
    # Defense in depth — the client only ever uses promql as the `query`
    # query-string value (never the URL path), but reject anything that
    # smells like an attempt to redirect the request anyway.
    if promql.startswith(("http://", "https://", "/")) or "/api/" in promql:
        return "Error: promql must be a PromQL expression, not a URL or path."
    range_seconds = args.get("range_seconds")
    range_seconds = int(range_seconds) if isinstance(range_seconds, int) else None
    rows = await ctx.prometheus_client.query(promql, range_seconds=range_seconds)
    return "\n".join(rows) if rows else "No data returned."


async def _tool_search_logs(args: dict[str, Any], ctx: _ToolContext) -> str:
    if ctx.elk_client is None:
        return "ELK is not configured for this environment."
    host = str(args.get("host", "")).strip()
    if host not in _known_hosts(ctx):
        return f"Error: host '{host}' is not a known fleet target."
    query_terms = args.get("query_terms") or []
    if not isinstance(query_terms, list):
        return "Error: query_terms must be a list of strings."
    window_hours = min(max(int(args.get("window_hours", 24) or 24), 1), 168)
    level = args.get("level")
    level = str(level) if level else None
    rows = await ctx.elk_client.search(
        host, [str(t) for t in query_terms], window_hours=window_hours, level=level,
    )
    return "\n".join(rows) if rows else "No matching log entries."


async def _tool_get_audit_events(args: dict[str, Any], ctx: _ToolContext) -> str:
    vm_id = args.get("vm_id")
    vm_id = str(vm_id) if vm_id else None
    action_type = args.get("action_type")
    action_type = str(action_type) if action_type else None

    event_type: EventType | None = None
    raw_event_type = args.get("event_type")
    if raw_event_type:
        try:
            event_type = EventType(str(raw_event_type))
        except ValueError:
            valid = ", ".join(sorted(e.value for e in EventType))
            return f"Error: invalid event_type '{raw_event_type}'. Valid values: {valid}"

    limit = min(max(int(args.get("limit", 20) or 20), 1), _MAX_AUDIT_EVENTS_LIMIT)
    events = await ctx.audit_store.get_events(
        vm_id=vm_id, event_type=event_type, action_type=action_type, limit=limit,
    )
    if not events:
        return "No matching audit events."
    lines = [
        f"{e.timestamp} {e.event_type} vm={e.vm_id or '-'} action={e.action_type or '-'}"
        for e in events[:limit]
    ]
    return "\n".join(lines)


async def _tool_get_disk_trend(args: dict[str, Any], ctx: _ToolContext) -> str:
    vm_id = str(args.get("vm_id", "")).strip()
    if not vm_id:
        return "Error: vm_id is required."
    window_days = min(max(int(args.get("window_days", 7) or 7), 1), _MAX_DISK_WINDOW_DAYS)

    mountpoints = await ctx.disk_history_store.get_distinct_mountpoints(vm_id)
    if not mountpoints:
        return f"No disk history recorded for {vm_id}."
    lines: list[str] = []
    for mp in mountpoints:
        points = await ctx.disk_history_store.get_window(vm_id, mp, window_days)
        if len(points) < 2:
            lines.append(f"{mp}: insufficient history ({len(points)} point(s))")
            continue
        start_pct, end_pct = points[0].used_pct, points[-1].used_pct
        delta = end_pct - start_pct
        lines.append(
            f"{mp}: {start_pct:.0f}% -> {end_pct:.0f}% ({delta:+.0f}%) over {window_days}d"
        )
    return "\n".join(lines)


async def _tool_get_vm_facts(args: dict[str, Any], ctx: _ToolContext) -> str:
    if ctx.vm_facts_store is None:
        return "VM facts store is not available."
    vm_id = str(args.get("vm_id", "")).strip()
    if not vm_id:
        return "Error: vm_id is required."

    lines: list[str] = []
    outcomes = await ctx.vm_facts_store.action_outcomes(vm_id)
    for fact in outcomes:
        line = f"{fact.action_type}: {fact.success_rate * 100:.0f}% success ({fact.sample_size} samples)"
        if fact.last_failure_reason:
            line += f" — last failure: {fact.last_failure_reason[:120]}"
        lines.append(line)
    reboot = await ctx.vm_facts_store.reboot_pattern(vm_id)
    if reboot is not None:
        lines.append(
            f"reboots required after patching: {reboot.reboots_required_after_patching}"
            f" ({reboot.sample_size} patching runs)"
        )
    return "\n".join(lines) if lines else f"No operational history facts for {vm_id}."


async def _tool_list_inventory(args: dict[str, Any], ctx: _ToolContext) -> str:
    env_filter = args.get("env")
    env_filter = str(env_filter) if env_filter else None
    envs = ctx.inventory.environments
    names = [env_filter] if env_filter and env_filter in envs else list(envs.keys())
    lines: list[str] = []
    for name in names:
        env = envs.get(name)
        if env is None:
            continue
        for target in env.targets:
            lines.append(f"{name}/{target.name} ({target.host})")
    return "\n".join(lines) if lines else "No matching targets in inventory."


_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "query_prometheus": ToolSpec(
        name="query_prometheus",
        description="Run a read-only PromQL query against the fleet's Prometheus instance.",
        parameters={
            "type": "object",
            "properties": {
                "promql": {"type": "string", "description": "PromQL expression."},
                "range_seconds": {
                    "type": "integer",
                    "description": "Optional — run a range query over the trailing N seconds.",
                },
            },
            "required": ["promql"],
        },
        handler=_tool_query_prometheus,
        available=lambda ctx: ctx.prometheus_client is not None,
    ),
    "search_logs": ToolSpec(
        name="search_logs",
        description="Search recent logs for one fleet host via ELK.",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "A known fleet target host or name."},
                "query_terms": {"type": "array", "items": {"type": "string"}},
                "window_hours": {"type": "integer", "description": "Trailing window, default 24."},
                "level": {"type": "string", "description": "Optional log level filter."},
            },
            "required": ["host", "query_terms"],
        },
        handler=_tool_search_logs,
        available=lambda ctx: ctx.elk_client is not None,
    ),
    "get_audit_events": ToolSpec(
        name="get_audit_events",
        description="Query Errander's own audit trail of maintenance actions and signals.",
        parameters={
            "type": "object",
            "properties": {
                "vm_id": {"type": "string"},
                "event_type": {"type": "string", "description": "One of the EventType values."},
                "action_type": {"type": "string"},
                "limit": {"type": "integer", "description": f"Max {_MAX_AUDIT_EVENTS_LIMIT}."},
            },
            "required": [],
        },
        handler=_tool_get_audit_events,
        available=lambda ctx: True,
    ),
    "get_disk_trend": ToolSpec(
        name="get_disk_trend",
        description="Return disk usage trend per mountpoint for one VM over a trailing window.",
        parameters={
            "type": "object",
            "properties": {
                "vm_id": {"type": "string"},
                "window_days": {"type": "integer", "description": f"Max {_MAX_DISK_WINDOW_DAYS}."},
            },
            "required": ["vm_id"],
        },
        handler=_tool_get_disk_trend,
        available=lambda ctx: True,
    ),
    "get_vm_facts": ToolSpec(
        name="get_vm_facts",
        description="Return operational-history facts (action success rates, reboot patterns) for one VM.",
        parameters={
            "type": "object",
            "properties": {"vm_id": {"type": "string"}},
            "required": ["vm_id"],
        },
        handler=_tool_get_vm_facts,
        available=lambda ctx: ctx.vm_facts_store is not None,
    ),
    "list_inventory": ToolSpec(
        name="list_inventory",
        description="List fleet VM names and hosts, optionally scoped to one environment.",
        parameters={
            "type": "object",
            "properties": {"env": {"type": "string"}},
            "required": [],
        },
        handler=_tool_list_inventory,
        available=lambda ctx: True,
    ),
}


def _tool_schemas(ctx: _ToolContext) -> list[dict[str, Any]]:
    """OpenAI tools= list for every tool currently usable given ctx's clients."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in _TOOL_REGISTRY.values()
        if spec.available(ctx)
    ]


class InvestigationAgent:
    """Layer A agentic investigation — bounded ReAct loop over read-only tools.

    Call investigate_agentic() with the same store/client kwargs as
    OperatorAssistant.investigate(). Falls back to the deterministic path on
    any failure; never raises.
    """

    async def investigate_agentic(
        self,
        question: str,
        *,
        audit_store: AuditStore,
        disk_history_store: VMDiskHistoryStore,
        baseline_store: BaselineStore,
        inventory: InventoryConfig,
        env_name: str | None = None,
        llm_client: LLMClient | None = None,
        prometheus_client: PrometheusClient | None = None,
        elk_client: ElkClient | None = None,
        vm_facts_store: VMFactsStore | None = None,
        ai_decision_store: AIDecisionStore | None = None,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    ) -> AssistantResponse:
        """Run the bounded tool-calling loop; fall back to the deterministic
        OperatorAssistant.investigate() path on any failure."""

        async def _fallback(reason: str) -> AssistantResponse:
            INVESTIGATION_FALLBACK_TOTAL.labels(reason=reason).inc()
            logger.info("Investigation agent falling back to deterministic path: %s", reason)
            return await OperatorAssistant().investigate(
                question,
                audit_store=audit_store,
                disk_history_store=disk_history_store,
                baseline_store=baseline_store,
                inventory=inventory,
                env_name=env_name,
                llm_client=llm_client,
                prometheus_client=prometheus_client,
                elk_client=elk_client,
                vm_facts_store=vm_facts_store,
                ai_decision_store=ai_decision_store,
            )

        if llm_client is None:
            return await _fallback("llm_down")

        if max_tool_calls < 1:
            logger.warning(
                "investigation_agent_max_tool_calls=%r invalid — using default %d",
                max_tool_calls, _DEFAULT_MAX_TOOL_CALLS,
            )
            max_tool_calls = _DEFAULT_MAX_TOOL_CALLS
        if timeout_seconds < 1:
            logger.warning(
                "investigation_agent_timeout_seconds=%r invalid — using default %d",
                timeout_seconds, _DEFAULT_TIMEOUT_SECONDS,
            )
            timeout_seconds = _DEFAULT_TIMEOUT_SECONDS

        ctx = _ToolContext(
            audit_store=audit_store,
            disk_history_store=disk_history_store,
            inventory=inventory,
            env_name=env_name,
            prometheus_client=prometheus_client,
            elk_client=elk_client,
            vm_facts_store=vm_facts_store,
        )
        tools = _tool_schemas(ctx)

        question, _ = _REDACTOR.redact(question)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        sources_used: list[str] = []
        total_tool_calls_made = 0
        hop = 0
        start_time = time.monotonic()

        try:
            while True:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_seconds:
                    return await _fallback("budget_exhausted")
                remaining = max(int(timeout_seconds - elapsed), _MIN_CALL_TIMEOUT_SECONDS)

                # Once the tool budget is spent, ask for a final answer with
                # no further tools offered rather than looping forever.
                call_tools = tools if total_tool_calls_made < max_tool_calls else []
                if not call_tools and hop > 0:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Tool budget exhausted. Based on everything above, "
                            "provide your final answer now in the required JSON "
                            "format. Do not request further tools."
                        ),
                    })

                result = await llm_client.complete_with_tools(
                    messages, call_tools, timeout_seconds=remaining,
                )

                if result is None:
                    return await _fallback("unsupported" if hop == 0 else "llm_down")

                if hop == 0 and not result.tool_calls:
                    return await _fallback("empty_turn1")

                if not result.tool_calls:
                    # Final answer.
                    final = _parse_response(result.content or "", AssistantResponse)
                    if final is None:
                        return await _fallback("budget_exhausted")
                    valid_sources = set(sources_used)
                    for finding in final.findings:
                        invalid = [e for e in finding.evidence if e not in valid_sources]
                        if invalid:
                            logger.warning(
                                "Agentic investigation: LLM cited unknown source(s) %s",
                                invalid,
                            )
                            finding.evidence = [e for e in finding.evidence if e in valid_sources]
                    final.data_sources = sources_used
                    return final

                # Tool calls requested — dispatch each, append results, loop.
                messages.append({
                    "role": "assistant",
                    "content": result.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments_json},
                        }
                        for tc in result.tool_calls
                    ],
                })

                for tc in result.tool_calls:
                    total_tool_calls_made += 1
                    source_id = f"{tc.name}#{total_tool_calls_made}"
                    tool_t0 = time.monotonic()
                    raw_result, tool_outcome = await self._dispatch_tool(tc.name, tc.arguments_json, ctx)
                    tool_latency_ms = round((time.monotonic() - tool_t0) * 1000, 1)

                    redacted, _ = _REDACTOR.redact(raw_result)
                    if len(redacted) > _MAX_TOOL_RESULT_CHARS:
                        redacted = redacted[: _MAX_TOOL_RESULT_CHARS - 1] + "…"

                    sources_used.append(source_id)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[source_id={source_id}]\n{redacted}",
                    })

                    INVESTIGATION_TOOL_CALLS_TOTAL.labels(tool=tc.name).inc()
                    await self._log_step(
                        ai_decision_store, llm_client,
                        tool_name=tc.name, arguments_json=tc.arguments_json,
                        result=redacted, outcome=tool_outcome,
                        latency_ms=tool_latency_ms, hop=hop, source_id=source_id,
                    )

                hop += 1
        except Exception:  # noqa: BLE001 — never raise to the operator
            logger.exception("Agentic investigation loop failed unexpectedly")
            return await _fallback("budget_exhausted")

    async def _dispatch_tool(
        self, name: str, arguments_json: str, ctx: _ToolContext,
    ) -> tuple[str, str]:
        """Parse args, run the tool handler. Returns (result_text, outcome)."""
        spec = _TOOL_REGISTRY.get(name)
        if spec is None:
            return f"Error: unknown tool '{name}'.", "error"
        try:
            args = json.loads(arguments_json) if arguments_json else {}
            if not isinstance(args, dict):
                return "Error: tool arguments must be a JSON object.", "error"
        except json.JSONDecodeError:
            return "Error: tool arguments were not valid JSON.", "error"

        try:
            result = await spec.handler(args, ctx)
            return result, "success"
        except Exception as exc:  # noqa: BLE001 — a failing tool must never abort the loop
            logger.warning("Tool %s raised: %s", name, exc)
            return f"Error: tool '{name}' failed: {exc}", "error"

    async def _log_step(
        self,
        ai_decision_store: AIDecisionStore | None,
        llm_client: LLMClient,
        *,
        tool_name: str,
        arguments_json: str,
        result: str,
        outcome: str,
        latency_ms: float,
        hop: int,
        source_id: str,
    ) -> None:
        """Per-hop audit row storing only this tool call's delta — never the
        cumulative message history (logging the full transcript every hop
        would grow the audit DB quadratically with loop length)."""
        if ai_decision_store is None:
            return
        from errander.safety.ai_audit import AIDecision

        delta_text = json.dumps({
            "hop": hop, "tool": tool_name, "arguments": arguments_json, "source_id": source_id,
        })
        await ai_decision_store.log(AIDecision(
            batch_id="ask",
            vm_id=None,
            decision_type="investigation_agent_step",
            model=getattr(llm_client, "_model", "unknown"),
            base_url=getattr(llm_client, "_base_url", ""),
            prompt_template_id="investigation_agent_v1",
            prompt_hash=AIDecision.hash_prompt(delta_text),
            response_raw=result,
            outcome=outcome,
            latency_ms=latency_ms,
            prompt_full=delta_text,
            context_snapshot=json.dumps({"hop": hop, "source_id": source_id}),
            model_params=json.dumps({"temperature": getattr(llm_client, "_temperature", None)}),
        ))
