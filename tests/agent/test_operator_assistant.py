"""Tests for errander/agent/operator_assistant.py — Layer A investigation engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from errander.agent.operator_assistant import (
    OperatorAssistant,
    _fallback_response,
    _format_prompt,
)
from errander.models.analysis import AssistantResponse, FleetContext, VMSignalSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inventory(env_names: list[str], vms_per_env: int = 1) -> MagicMock:
    """Build a fake InventoryConfig with named environments."""
    inv = MagicMock()
    envs = {}
    for env in env_names:
        env_mock = MagicMock()
        env_mock.targets = [
            MagicMock(name=f"{env}-vm-{i}", host=f"10.0.{i}.1")
            for i in range(vms_per_env)
        ]
        envs[env] = env_mock
    inv.environments = envs
    return inv


def _make_audit_store(
    failures: int = 0,
    started: int = 0,
    drift_events: int = 0,
    login_count: int = 0,
    recent_batches: list[dict] | None = None,
) -> MagicMock:
    store = MagicMock()

    async def _get_events(vm_id=None, event_type=None, limit=100, **_kw):
        from errander.models.events import EventType
        if event_type == EventType.ACTION_FAILED:
            return [MagicMock(action_type="patching") for _ in range(failures)]
        if event_type == EventType.ACTION_STARTED:
            return [MagicMock(action_type="disk_cleanup") for _ in range(started)]
        if event_type == EventType.DRIFT_KIND_CHANGED:
            return [MagicMock(metadata={"kind": "sudoers"}) for _ in range(drift_events)]
        if event_type == EventType.FAILED_SSH_LOGINS_OBSERVED:
            return [MagicMock(metadata={"total_count": login_count})] if login_count else []
        return []

    store.get_events = _get_events
    store.get_recent_batches = AsyncMock(return_value=recent_batches or [])
    return store


def _empty_stores() -> tuple[MagicMock, MagicMock]:
    """Disk history + baseline stores that always return empty."""
    disk = MagicMock()
    disk.get_distinct_mountpoints = AsyncMock(return_value=[])
    disk.get_window = AsyncMock(return_value=[])

    base = MagicMock()
    base.latest = AsyncMock(return_value=None)
    return disk, base


def _healthy_context(env: str = "dev") -> FleetContext:
    return FleetContext(
        env_name=env,
        vm_summaries=[VMSignalSummary(vm_id="v1", hostname="h1")],
        recent_batch_count=3,
        last_batch_at="2026-05-15T02:00:00",
        total_failures_7d=0,
    )


# ---------------------------------------------------------------------------
# investigate() — LLM path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investigate_returns_llm_response_when_available() -> None:
    llm = MagicMock()
    expected = AssistantResponse(
        summary="Fleet healthy.",
        findings=["No issues found"],
        recommendations=["Continue monitoring"],
        risk_level="low",
    )
    llm.complete = AsyncMock(return_value=expected)

    disk, base = _empty_stores()
    audit = _make_audit_store()
    inv = _make_inventory(["dev"])

    result = await OperatorAssistant().investigate(
        "How is the fleet?",
        audit_store=audit,
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        llm_client=llm,
    )

    assert result is expected
    llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_investigate_falls_back_when_llm_is_none() -> None:
    disk, base = _empty_stores()
    audit = _make_audit_store()
    inv = _make_inventory(["dev"])

    result = await OperatorAssistant().investigate(
        "How is the fleet?",
        audit_store=audit,
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        llm_client=None,
    )

    assert isinstance(result, AssistantResponse)
    assert result.risk_level in ("low", "medium", "high", "unknown")


@pytest.mark.asyncio
async def test_investigate_falls_back_when_llm_returns_none() -> None:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=None)  # parse failure

    disk, base = _empty_stores()
    audit = _make_audit_store()
    inv = _make_inventory(["dev"])

    result = await OperatorAssistant().investigate(
        "How is the fleet?",
        audit_store=audit,
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        llm_client=llm,
    )

    assert isinstance(result, AssistantResponse)
    # Fallback produces the LLM-unavailable summary
    assert "unavailable" in result.summary.lower() or result.findings


# ---------------------------------------------------------------------------
# _build_context() — store queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_scopes_to_env() -> None:
    inv = _make_inventory(["dev", "prod"], vms_per_env=2)
    disk, base = _empty_stores()
    audit = _make_audit_store()

    ctx = await OperatorAssistant()._build_context(
        audit_store=audit,
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name="dev",
    )

    assert ctx.env_name == "dev"
    # Only dev VMs — names start with "dev-vm-"
    assert all(v.vm_id.startswith("dev-vm-") for v in ctx.vm_summaries)
    assert len(ctx.vm_summaries) == 2


@pytest.mark.asyncio
async def test_build_context_queries_all_envs_when_no_env() -> None:
    inv = _make_inventory(["dev", "prod"], vms_per_env=1)
    disk, base = _empty_stores()
    audit = _make_audit_store()

    ctx = await OperatorAssistant()._build_context(
        audit_store=audit,
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name=None,
    )

    assert ctx.env_name is None
    assert len(ctx.vm_summaries) == 2  # 1 dev + 1 prod


@pytest.mark.asyncio
async def test_build_context_counts_failures() -> None:
    inv = _make_inventory(["dev"])
    disk, base = _empty_stores()
    audit = _make_audit_store(failures=3)

    ctx = await OperatorAssistant()._build_context(
        audit_store=audit,
        disk_history_store=disk,
        baseline_store=base,
        inventory=inv,
        env_name="dev",
    )

    assert ctx.total_failures_7d == 3
    assert ctx.vm_summaries[0].recent_failure_count == 3


# ---------------------------------------------------------------------------
# _fallback_response()
# ---------------------------------------------------------------------------


def test_fallback_healthy_fleet() -> None:
    ctx = _healthy_context()
    result = _fallback_response("How are things?", ctx)
    assert "no significant signals" in result.findings[0].lower()
    assert result.risk_level == "low"


def test_fallback_flags_failures() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[VMSignalSummary(vm_id="v1", hostname="h1", recent_failure_count=2)],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=2,
    )
    result = _fallback_response("q", ctx)
    assert any("failure" in f.lower() for f in result.findings)
    assert result.risk_level == "high"


def test_fallback_flags_disk_alerts() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[
            VMSignalSummary(
                vm_id="v1", hostname="h1",
                disk_alerts=["/ 70% -> 85% (+15%) over 7d"],
            )
        ],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=0,
    )
    result = _fallback_response("q", ctx)
    assert any("disk" in f.lower() for f in result.findings)
    assert result.risk_level == "medium"


def test_fallback_flags_drift_changes() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[
            VMSignalSummary(vm_id="v1", hostname="h1", drift_kinds=["sudoers"])
        ],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=0,
    )
    result = _fallback_response("q", ctx)
    assert any("drift" in f.lower() for f in result.findings)
    assert result.risk_level == "high"


def test_fallback_flags_failed_logins() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[
            VMSignalSummary(vm_id="v1", hostname="h1", failed_login_count=42)
        ],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=0,
    )
    result = _fallback_response("q", ctx)
    assert any("login" in f.lower() for f in result.findings)
    assert result.risk_level == "medium"


def test_fallback_risk_level_high_on_both_failures_and_drift() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[
            VMSignalSummary(
                vm_id="v1", hostname="h1",
                recent_failure_count=1,
                drift_kinds=["authorized_keys"],
            )
        ],
        recent_batch_count=1,
        last_batch_at=None,
        total_failures_7d=1,
    )
    result = _fallback_response("q", ctx)
    assert result.risk_level == "high"


# ---------------------------------------------------------------------------
# _format_prompt()
# ---------------------------------------------------------------------------


def test_format_prompt_includes_question() -> None:
    ctx = _healthy_context()
    prompt = _format_prompt("Why is disk growing?", ctx)
    assert "Why is disk growing?" in prompt


def test_format_prompt_includes_vm_id() -> None:
    ctx = _healthy_context()
    prompt = _format_prompt("q", ctx)
    assert "v1" in prompt


def test_format_prompt_includes_disk_alert() -> None:
    ctx = FleetContext(
        env_name="dev",
        vm_summaries=[
            VMSignalSummary(
                vm_id="v1", hostname="h1",
                disk_alerts=["/ 70% -> 90% (+20%) over 7d"],
            )
        ],
        recent_batch_count=0,
        last_batch_at=None,
        total_failures_7d=0,
    )
    prompt = _format_prompt("q", ctx)
    assert "70%" in prompt
    assert "90%" in prompt


def test_format_prompt_layer_a_instruction_present() -> None:
    ctx = _healthy_context()
    prompt = _format_prompt("q", ctx)
    assert "NEVER suggest executing commands" in prompt
