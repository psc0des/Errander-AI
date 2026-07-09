"""Tests for `errander vm-facts` CLI (Phase B3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from errander.commands.vm_facts import cmd_vm_facts
from errander.db.core import AsyncDatabase
from tests.conftest import TEST_DB_URL

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path() -> str:
    """Test PostgreSQL URL — schema applied by the session migration fixture."""
    return TEST_DB_URL


async def _insert_events(db_path: str, rows: list[dict]) -> None:
    """Insert raw audit_events rows for test setup."""
    db = AsyncDatabase(db_path)
    async with db.begin() as conn:
        for r in rows:
            await conn.execute(
                text(
                    "INSERT INTO audit_events"
                    " (event_type, batch_id, vm_id, action_type, detail, metadata, timestamp)"
                    " VALUES (:et, :bid, :vid, :at, :det, :meta, :ts)"
                ),
                {
                    "et": r.get("event_type", "action_completed"),
                    "bid": r.get("batch_id", "batch-1"),
                    "vid": r.get("vm_id"),
                    "at": r.get("action_type"),
                    "det": r.get("detail", ""),
                    "meta": r.get("metadata", "{}"),
                    "ts": r.get("timestamp", datetime.now(tz=UTC).isoformat()),
                },
            )
    await db.close()


def _make_args(vm_id: str | None = None, action: str | None = None):
    """Build a minimal argparse.Namespace for cmd_vm_facts."""
    import argparse

    ns = argparse.Namespace()
    ns.vm_facts_vm_id = vm_id
    ns.vm_facts_action = action
    return ns


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_args_returns_error(db_path: str, capsys) -> None:
    """Neither vm_id nor action given → error exit code 1."""
    args = _make_args()
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 1
    out = capsys.readouterr().out
    assert "provide" in out.lower()


# ---------------------------------------------------------------------------
# Action outcomes table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outcomes_single_vm(db_path: str, capsys) -> None:
    """vm-facts <vm_id> shows outcome table for that VM."""
    await _insert_events(db_path, [
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_failed",    "vm_id": "prod/web-01", "action_type": "patching",
         "detail": "dpkg lock"},
    ])
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "patching" in out
    assert "prod/web-01" in out
    assert "67%" in out or "66%" in out  # 2/3 ≈ 66.7%


@pytest.mark.asyncio
async def test_outcomes_filtered_by_action(db_path: str, capsys) -> None:
    """--action filters output to only that action type."""
    await _insert_events(db_path, [
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "disk_cleanup"},
    ])
    args = _make_args(vm_id="prod/web-01", action="patching")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "patching" in out
    assert "disk_cleanup" not in out


@pytest.mark.asyncio
async def test_outcomes_no_data(db_path: str, capsys) -> None:
    """VM with no action history prints a friendly 'no data' message."""
    args = _make_args(vm_id="prod/ghost-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no action outcome data" in out.lower() or "No action outcome" in out


@pytest.mark.asyncio
async def test_outcomes_100_percent_success(db_path: str, capsys) -> None:
    """All completions → 100% success rate shown."""
    await _insert_events(db_path, [
        {"event_type": "action_completed", "vm_id": "prod/db-01", "action_type": "disk_cleanup"},
        {"event_type": "action_completed", "vm_id": "prod/db-01", "action_type": "disk_cleanup"},
    ])
    args = _make_args(vm_id="prod/db-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "100%" in out


@pytest.mark.asyncio
async def test_outcomes_0_percent_success(db_path: str, capsys) -> None:
    """All failures → 0% shown."""
    await _insert_events(db_path, [
        {"event_type": "action_failed", "vm_id": "prod/db-02", "action_type": "patching",
         "detail": "apt error"},
    ])
    args = _make_args(vm_id="prod/db-02")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0%" in out


# ---------------------------------------------------------------------------
# Cross-fleet mode (--action only, no vm_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_fleet_by_action(db_path: str, capsys) -> None:
    """vm-facts --action patching shows all VMs with that action."""
    await _insert_events(db_path, [
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_completed", "vm_id": "prod/web-02", "action_type": "patching"},
        {"event_type": "action_failed",    "vm_id": "prod/web-02", "action_type": "patching"},
    ])
    args = _make_args(action="patching")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "prod/web-01" in out
    assert "prod/web-02" in out


@pytest.mark.asyncio
async def test_cross_fleet_no_data(db_path: str, capsys) -> None:
    """Cross-fleet with no data for action → friendly message."""
    args = _make_args(action="backup_verify")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no action outcome" in out.lower()


# ---------------------------------------------------------------------------
# Reboot pattern table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reboot_pattern_shown(db_path: str, capsys) -> None:
    """VM with patching + reboot events shows reboot pattern row."""
    await _insert_events(db_path, [
        {"event_type": "action_completed",       "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_completed",       "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "reboot_required_detected", "vm_id": "prod/web-01"},
    ])
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Reboot pattern" in out
    assert "1 / 2" in out


@pytest.mark.asyncio
async def test_reboot_pattern_no_patching_history(db_path: str, capsys) -> None:
    """VM with no patching runs → 'No patching history' for reboot section."""
    args = _make_args(vm_id="prod/ghost-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No patching history" in out


@pytest.mark.asyncio
async def test_reboot_section_absent_for_cross_fleet(db_path: str, capsys) -> None:
    """Cross-fleet mode (no vm_id) does not show reboot pattern section."""
    await _insert_events(db_path, [
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
    ])
    args = _make_args(action="patching")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Reboot pattern" not in out


# ---------------------------------------------------------------------------
# Rejection facts table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejection_facts_shown(db_path: str, capsys) -> None:
    """Approval rejection events appear in rejection facts table."""
    await _insert_events(db_path, [
        {"event_type": "approval_rejected", "batch_id": "batch-rej-1",
         "detail": "risk too high", "timestamp": datetime.now(tz=UTC).isoformat()},
        {"event_type": "action_completed", "batch_id": "batch-rej-1",
         "action_type": "patching"},
    ])
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Approval rejections" in out
    assert "patching" in out


@pytest.mark.asyncio
async def test_rejection_facts_empty(db_path: str, capsys) -> None:
    """No rejections → friendly message in rejection section."""
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No approval rejections" in out


@pytest.mark.asyncio
async def test_rejection_old_events_excluded(db_path: str, capsys) -> None:
    """Rejections older than 90 days are excluded from the facts table."""
    old_ts = (datetime.now(tz=UTC) - timedelta(days=100)).isoformat()
    await _insert_events(db_path, [
        {"event_type": "approval_rejected", "batch_id": "old-batch",
         "detail": "old rejection", "timestamp": old_ts},
        {"event_type": "action_completed", "batch_id": "old-batch",
         "action_type": "disk_cleanup"},
    ])
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No approval rejections" in out


# ---------------------------------------------------------------------------
# Output formatting / indicators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_indicator_high(db_path: str, capsys) -> None:
    """≥90% success rate shows '✓' indicator."""
    for _ in range(10):
        await _insert_events(db_path, [
            {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
        ])
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "✓" in out


@pytest.mark.asyncio
async def test_success_indicator_low(db_path: str, capsys) -> None:
    """<60% success rate shows '✗' indicator."""
    await _insert_events(db_path, [
        {"event_type": "action_failed",    "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_failed",    "vm_id": "prod/web-01", "action_type": "patching"},
        {"event_type": "action_completed", "vm_id": "prod/web-01", "action_type": "patching"},
    ])
    args = _make_args(vm_id="prod/web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "✗" in out


# ---------------------------------------------------------------------------
# Agent proposal history section (fable-plan Phase 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposal_history_shows_counts(db_path: str, capsys) -> None:
    await _insert_events(db_path, [
        {"event_type": "proposal_created", "vm_id": "web-01", "action_type": "disk_cleanup"},
        {"event_type": "proposal_approved", "vm_id": "web-01", "action_type": "disk_cleanup"},
        {"event_type": "proposal_execution_completed",
         "vm_id": "web-01", "action_type": "disk_cleanup"},
    ])
    args = _make_args(vm_id="web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Agent proposal history" in out
    assert "disk_cleanup" in out


@pytest.mark.asyncio
async def test_proposal_history_no_data(db_path: str, capsys) -> None:
    args = _make_args(vm_id="web-01")
    rc = await cmd_vm_facts(args, db_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No agent-proposal history for web-01" in out


@pytest.mark.asyncio
async def test_proposal_history_shows_suppressed_status(db_path: str, capsys) -> None:
    from errander.models.proposals import AgentProposal, ProposalKind
    from errander.safety.proposal_store import ProposalStore

    store = ProposalStore(AsyncDatabase(db_path))
    await store.initialize()

    def candidate() -> AgentProposal:
        return AgentProposal(
            env_name="prod", vm_id="web-01", kind=ProposalKind.ACTION,
            action_type="disk_cleanup", signal_kind="disk_growth",
        )

    for _ in range(2):
        stored, _ = await store.create_or_refresh(candidate())
        await store.decide(stored.proposal_id, approved=False, decided_by="ui:a")
    await _insert_events(db_path, [
        {"event_type": "proposal_created", "vm_id": "web-01", "action_type": "disk_cleanup"},
        {"event_type": "proposal_rejected", "vm_id": "web-01", "action_type": "disk_cleanup"},
        {"event_type": "proposal_created", "vm_id": "web-01", "action_type": "disk_cleanup"},
        {"event_type": "proposal_rejected", "vm_id": "web-01", "action_type": "disk_cleanup"},
    ])

    args = _make_args(vm_id="web-01")
    rc = await cmd_vm_facts(
        args, db_path, suppression_threshold=2, suppression_window_days=14,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "SUPPRESSED" in out
