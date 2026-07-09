"""vm-facts CLI sub-commands — Project B, B3.

Provides:
  errander vm-facts <vm_id> [--action <type>]
  errander vm-facts --action <type>   # cross-fleet: all VMs, one action type

Prints three tables (where data is available):
  1. Action outcomes — success rate, sample size, last failure reason.
  2. Reboot pattern  — reboots required after patching.
  3. Rejection facts — approval rejections per action type (last 90 days).

Useful for SRE spot-checking whether the facts surfaced to the LLM match
operational reality before trusting LLM summaries built on them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from errander.db.core import AsyncDatabase

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

_SEP = "-" * 72


def _fmt_rate(rate: float) -> str:
    """Return a colour-free rate string with a visual indicator."""
    pct = rate * 100
    if pct >= 90:
        indicator = "✓"
    elif pct >= 60:
        indicator = "~"
    else:
        indicator = "✗"
    return f"{indicator} {pct:.0f}%"


def _fmt_ts(ts: object) -> str:
    from datetime import datetime

    if ts is None:
        return "—"
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M UTC")
    return str(ts)[:16]


async def _print_outcomes(
    db: AsyncDatabase,
    vm_id: str | None,
    action_type: str | None,
) -> None:
    """Print action outcome facts for one VM or all VMs for one action type."""
    from sqlalchemy import text

    from errander.safety.vm_facts import VMFactsStore

    async with VMFactsStore(db) as store:
        if vm_id is not None:
            facts = await store.action_outcomes(vm_id, action_type=action_type)
        else:
            # Cross-fleet: query every distinct VM that has the action_type
            async with db.begin() as conn:
                result = await conn.execute(
                    text("""
                    SELECT DISTINCT vm_id FROM audit_events
                    WHERE action_type = :action_type AND vm_id IS NOT NULL
                      AND event_type IN ('action_completed', 'action_failed')
                    """),
                    {"action_type": action_type},
                )
                rows = result.fetchall()
            vms = [str(r[0]) for r in rows]
            facts = []
            for vid in vms:
                facts.extend(await store.action_outcomes(vid, action_type=action_type))

    if not facts:
        scope = vm_id or f"any VM with action={action_type}"
        print(f"  No action outcome data for {scope}.")
        return

    col_vm   = max(len(f.vm_id)          for f in facts)
    col_at   = max(len(f.action_type)    for f in facts)
    col_vm   = max(col_vm, 6)
    col_at   = max(col_at, 6)

    hdr = (
        f"  {'VM':<{col_vm}}  {'ACTION':<{col_at}}  "
        f"{'RATE':>6}  {'SAMPLE':>6}  {'LAST SUCCESS':<17}  LAST FAILURE"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for f in sorted(facts, key=lambda x: (x.vm_id, x.action_type)):
        failure = (f.last_failure_reason or "—")[:50]
        print(
            f"  {f.vm_id:<{col_vm}}  {f.action_type:<{col_at}}  "
            f"{_fmt_rate(f.success_rate):>6}  {f.sample_size:>6}  "
            f"{_fmt_ts(f.last_success_at):<17}  {failure}"
        )


async def _print_reboot(db: AsyncDatabase, vm_id: str) -> None:
    """Print reboot pattern fact for one VM."""
    from errander.safety.vm_facts import VMFactsStore

    async with VMFactsStore(db) as store:
        fact = await store.reboot_pattern(vm_id)

    if fact is None:
        print(f"  No patching history for {vm_id}.")
        return

    ratio = (
        f"{fact.reboots_required_after_patching} / {fact.sample_size} "
        f"patching runs required a reboot"
    )
    print(f"  {vm_id}: {ratio}")


async def _print_proposal_history(
    db: AsyncDatabase,
    vm_id: str,
    *,
    suppression_threshold: int,
    suppression_window_days: int,
) -> None:
    """Print agent-proposal lifecycle facts + suppression state for one VM."""
    from errander.safety.proposal_store import ProposalStore
    from errander.safety.vm_facts import VMFactsStore

    async with VMFactsStore(db) as store:
        facts = await store.proposal_outcomes(vm_id)

    if not facts:
        print(f"  No agent-proposal history for {vm_id}.")
        return

    proposal_store = ProposalStore(db)
    col_at = max(max(len(f.action_type) for f in facts), 6)

    hdr = (
        f"  {'ACTION':<{col_at}}  {'PROPOSED':>8}  {'APPROVED':>8}  "
        f"{'REJECTED':>8}  {'EXEC OK':>7}  {'EXEC FAIL':>9}  STATUS"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for f in sorted(facts, key=lambda x: x.action_type):
        suppressed = await proposal_store.is_suppressed(
            vm_id, f.action_type,
            threshold=suppression_threshold, window_days=suppression_window_days,
        )
        if suppressed:
            cooldown = (
                f.last_decided_at + timedelta(days=suppression_window_days)
                if f.last_decided_at else None
            )
            status = (
                f"SUPPRESSED until {cooldown:%Y-%m-%d}" if cooldown else "SUPPRESSED"
            )
        else:
            status = "—"
        print(
            f"  {f.action_type:<{col_at}}  {f.proposed_count:>8}  {f.approved_count:>8}  "
            f"{f.rejected_count:>8}  {f.executed_success_count:>7}  "
            f"{f.executed_failed_count:>9}  {status}"
        )


async def _print_rejections(db: AsyncDatabase) -> None:
    """Print approval rejection counts (all action types, last 90 days)."""
    from errander.safety.vm_facts import VMFactsStore

    async with VMFactsStore(db) as store:
        facts = await store.rejection_facts()

    if not facts:
        print("  No approval rejections in the last 90 days.")
        return

    col_at = max(len(f.action_type) for f in facts)
    col_at = max(col_at, 6)

    hdr = f"  {'ACTION':<{col_at}}  {'REJECTIONS (90d)':>16}  REASONS"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for f in sorted(facts, key=lambda x: -x.rejections_last_90d):
        reasons = "; ".join(f.rejection_reasons[:3]) or "—"
        print(
            f"  {f.action_type:<{col_at}}  {f.rejections_last_90d:>16}  {reasons[:60]}"
        )


async def cmd_vm_facts(
    args: argparse.Namespace,
    db_path: str,
    *,
    suppression_threshold: int = 2,
    suppression_window_days: int = 14,
) -> int:
    """Main handler for `errander vm-facts`."""
    vm_id: str | None = getattr(args, "vm_facts_vm_id", None)
    action_type: str | None = getattr(args, "vm_facts_action", None)

    if vm_id is None and action_type is None:
        print("Error: provide <vm_id> and/or --action <type>.")
        print("  errander vm-facts <vm_id>")
        print("  errander vm-facts <vm_id> --action patching")
        print("  errander vm-facts --action patching   # cross-fleet")
        return 1

    from errander.safety.migrations import run_migrations

    db = AsyncDatabase(db_path)
    async with db.begin() as conn:
        await run_migrations(conn)

    # ── Outcomes table ────────────────────────────────────────────────────────
    scope = vm_id if vm_id else f"all VMs (action={action_type})"
    print(f"\nAction outcomes — {scope}")
    print(_SEP)
    await _print_outcomes(db, vm_id, action_type)

    # ── Reboot pattern (only makes sense per-VM) ──────────────────────────────
    if vm_id is not None:
        print(f"\nReboot pattern — {vm_id}")
        print(_SEP)
        await _print_reboot(db, vm_id)

    # ── Agent proposal history (Phase 4; only makes sense per-VM) ────────────
    if vm_id is not None:
        print(f"\nAgent proposal history — {vm_id}")
        print(_SEP)
        await _print_proposal_history(
            db, vm_id,
            suppression_threshold=suppression_threshold,
            suppression_window_days=suppression_window_days,
        )

    # ── Rejection facts (fleet-wide, not per-VM or per-action) ───────────────
    print("\nApproval rejections — last 90 days (fleet-wide)")
    print(_SEP)
    await _print_rejections(db)

    await db.close()

    print()
    return 0


def dispatch_vm_facts(
    args: argparse.Namespace,
    db_path: str,
    *,
    suppression_threshold: int = 2,
    suppression_window_days: int = 14,
) -> int:
    """Synchronous entry point — runs cmd_vm_facts in an event loop."""
    return asyncio.run(cmd_vm_facts(
        args, db_path,
        suppression_threshold=suppression_threshold,
        suppression_window_days=suppression_window_days,
    ))
