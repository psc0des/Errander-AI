"""Local dev script — starts the Errander-AI UI with sample data and opens a browser.

Usage:
    uv run python scripts/dev_ui.py

Opens http://localhost:9090/ui in your default browser with realistic fake data
so you can explore the UI without needing real VMs or a running agent.
Includes a pending approval so you can test the Approve/Reject buttons.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from errander.models.events import AuditEvent, EventType
from errander.observability.metrics import start_metrics_server
from errander.safety.approval import ApprovalManager
from errander.safety.audit import AuditStore


def _ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


async def _seed(store: AuditStore) -> None:
    events = [
        # Batch 1 — mixed outcome
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-04-01", detail="Batch started",              timestamp=_ts(2026, 4, 1, 2, 0)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-01", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 1, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-01", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 2.3 GB",             timestamp=_ts(2026, 4, 1, 2, 5)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-01", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 1, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_FAILED,    batch_id="batch-2026-04-01", vm_id="prod/db-01",     action_type="disk_cleanup", detail="SSH timeout after 300s",  timestamp=_ts(2026, 4, 1, 2, 6)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-04-01", detail="1 success, 1 failed",        timestamp=_ts(2026, 4, 1, 3, 0)),

        # Batch 2 — all green
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-04-02", detail="Batch started",              timestamp=_ts(2026, 4, 2, 2, 0)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-02", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 2, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-02", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 1.1 GB",             timestamp=_ts(2026, 4, 2, 2, 4)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-02", vm_id="staging/app-01", action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 2, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-02", vm_id="staging/app-01", action_type="disk_cleanup", detail="Freed 512 MB",             timestamp=_ts(2026, 4, 2, 2, 3)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-04-02", detail="2 success",                 timestamp=_ts(2026, 4, 2, 3, 0)),

        # Batch 3 — today's run
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-04-10", detail="Batch started",              timestamp=_ts(2026, 4, 10, 2, 0)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-10", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 10, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-10", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 800 MB",             timestamp=_ts(2026, 4, 10, 2, 4)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-10", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 10, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-10", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Freed 2.1 GB",             timestamp=_ts(2026, 4, 10, 2, 6)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-04-10", vm_id="staging/app-01", action_type="disk_cleanup", detail="Starting disk cleanup",   timestamp=_ts(2026, 4, 10, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-04-10", vm_id="staging/app-01", action_type="disk_cleanup", detail="Freed 320 MB",             timestamp=_ts(2026, 4, 10, 2, 3)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-04-10", detail="3 success",                 timestamp=_ts(2026, 4, 10, 3, 0)),
    ]
    for e in events:
        await store.log_event(e)


_SAMPLE_REPORT = """\
Errander-AI Dry-Run Report — batch-2026-04-13
============================================
Environment : production
Targets     : 3 VMs
Mode        : DRY-RUN (no changes applied)

Actions planned
---------------
prod/web-01   disk_cleanup
  [DRY-RUN] Would execute: find /tmp -type f -atime +7 -delete
  [DRY-RUN] Would execute: apt-get clean
  [DRY-RUN] Would execute: journalctl --vacuum-time=30d
  Estimated space freed: ~950 MB

prod/db-01    disk_cleanup
  [DRY-RUN] Would execute: find /tmp -type f -atime +7 -delete
  [DRY-RUN] Would execute: apt-get autoremove -y
  Estimated space freed: ~1.8 GB

staging/app-01  disk_cleanup
  [DRY-RUN] Would execute: find /tmp -type f -atime +7 -delete
  [DRY-RUN] Would execute: dnf clean all
  Estimated space freed: ~430 MB

Summary
-------
Total estimated space to free: ~3.2 GB across 3 VMs
No high-risk actions planned.
Approve to run live. Reject to cancel.
"""


async def main() -> None:
    store = AuditStore(":memory:")
    await store.initialize()
    await _seed(store)

    # Seed approval manager with one pending + one decided approval
    manager = ApprovalManager()
    manager.register(
        "batch-2026-04-13",
        _SAMPLE_REPORT,
        slack_message_ts="1744500000.000001",  # fake — Slack not running
    )
    # Add a historical decision so the history table is visible
    manager.register("batch-2026-04-12", "Freed 2.1 GB on prod/db-01 (dry-run)")
    manager.decide("batch-2026-04-12", approved=True, user_id="ops-team")

    port = 9092
    await start_metrics_server(port=port, audit_store=store, approval_manager=manager)

    url = f"http://localhost:{port}/ui"
    print(f"\n  Errander-AI UI running at {url}")
    print(f"  Approvals : http://localhost:{port}/ui/approvals")
    print(f"  Metrics   : http://localhost:{port}/metrics")
    print(f"  Health    : http://localhost:{port}/health")
    print("\n  Press Ctrl+C to stop.\n")

    try:
        await asyncio.Event().wait()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
