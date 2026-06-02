"""Capture Web UI screenshots for the README — from synthetic demo data only.

Usage:
    uv run python scripts/capture_ui_screenshots.py

Seeds an in-memory AuditStore / ApprovalManager / AIDecisionStore with entirely
fake data (no real hosts, IPs, tokens, or Slack workspace), serves the UI on a
loopback port, then drives headless Chromium to screenshot each page into
docs/images/. A bright "DEMO DATA" banner is injected into every capture so the
images can never be mistaken for a real fleet.

Nothing here touches a real inventory or network — it is safe to run anywhere.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from errander.models.events import AuditEvent, EventType
from errander.models.vm import OSFamily, VMTarget
from errander.observability.metrics import start_metrics_server
from errander.safety.ai_audit import AIDecision, AIDecisionStore
from errander.safety.approval import ApprovalManager
from errander.safety.audit import AuditStore
from errander.safety.overrides import OverridesStore

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "images"

# Bright banner injected into every screenshot — makes "this is fake" unmissable.
_BANNER_JS = r"""
(() => {
  if (document.getElementById('__demo_banner__')) return;
  // The content column (.wrap) sits right of a fixed sidebar; the banner goes
  // at its top so the sidebar layout stays intact.
  const host = document.querySelector('.wrap') || document.body;
  const b = document.createElement('div');
  b.id = '__demo_banner__';
  b.textContent = 'DEMO DATA — synthetic fleet, not a real deployment';
  Object.assign(b.style, {
    position: 'relative', zIndex: '99999', width: '100%',
    background: 'repeating-linear-gradient(45deg,#b91c1c,#b91c1c 14px,#7f1d1d 14px,#7f1d1d 28px)',
    color: '#fff', font: '700 13px/1.4 system-ui,-apple-system,Segoe UI,sans-serif',
    letterSpacing: '0.04em', textAlign: 'center', padding: '8px 12px',
    textShadow: '0 1px 2px rgba(0,0,0,0.5)', boxSizing: 'border-box',
  });
  host.insertBefore(b, host.firstChild);
})();
"""


def _ts(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


_SAMPLE_REPORT = """\
Errander-AI Plan — batch-2026-05-28-prod
========================================
Environment : production
Targets     : 3 VMs
Mode        : awaiting approval (no changes applied yet)

Highest priority: prod/web-01 has 3 security updates pending and /var at 86%.
Lowest risk first: log rotation and /tmp cleanup are categorical and reversible.
Approve to run live. Reject to cancel.
"""

# Exact-object plan so the approval card renders per-package checkboxes,
# a service_restart unit, and categorical (auto-included) actions.
_VM_PLANS: list[dict[str, object]] = [
    {
        "vm_id": "prod/web-01",
        "os_family": "ubuntu",
        "planned_actions": [
            {
                "action_type": "patching",
                "preview": {
                    "packages": [
                        {"name": "openssl", "current": "3.0.2-0ubuntu1.15", "target": "3.0.2-0ubuntu1.18"},
                        {"name": "libssl3", "current": "3.0.2-0ubuntu1.15", "target": "3.0.2-0ubuntu1.18"},
                        {"name": "curl", "current": "7.81.0-1ubuntu1.15", "target": "7.81.0-1ubuntu1.16"},
                    ],
                },
            },
            {"action_type": "log_rotation"},
            {"action_type": "disk_cleanup"},
        ],
    },
    {
        "vm_id": "prod/db-01",
        "os_family": "debian",
        "planned_actions": [
            {"action_type": "service_restart", "params": {"unit_name": "postgresql.service"}},
            {"action_type": "disk_cleanup"},
        ],
    },
    {
        "vm_id": "staging/app-01",
        "os_family": "rhel",
        "planned_actions": [
            {"action_type": "patching", "preview": {"packages": []}},
            {"action_type": "log_rotation"},
        ],
    },
]


async def _seed_audit(store: AuditStore) -> None:
    events = [
        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-05-01", detail="Batch started",             timestamp=_ts(2026, 5, 1, 2)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-05-01", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",  timestamp=_ts(2026, 5, 1, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-05-01", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 2.3 GB",            timestamp=_ts(2026, 5, 1, 2, 5)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-05-01", vm_id="prod/db-01",     action_type="patching",     detail="Applying 3 packages",     timestamp=_ts(2026, 5, 1, 2, 2)),
        AuditEvent(event_type=EventType.ACTION_FAILED,    batch_id="batch-2026-05-01", vm_id="prod/db-01",     action_type="patching",     detail="SSH timeout after 300s",  timestamp=_ts(2026, 5, 1, 2, 7)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-05-01", detail="1 success, 1 failed",       timestamp=_ts(2026, 5, 1, 3)),

        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-05-14", detail="Batch started",             timestamp=_ts(2026, 5, 14, 2)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-05-14", vm_id="prod/web-01",    action_type="patching",     detail="Applying 5 packages",     timestamp=_ts(2026, 5, 14, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-05-14", vm_id="prod/web-01",    action_type="patching",     detail="5 packages upgraded",     timestamp=_ts(2026, 5, 14, 2, 6)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-05-14", vm_id="staging/app-01", action_type="log_rotation", detail="Rotating logs",           timestamp=_ts(2026, 5, 14, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-05-14", vm_id="staging/app-01", action_type="log_rotation", detail="Compressed 1.4 GB",       timestamp=_ts(2026, 5, 14, 2, 3)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-05-14", detail="2 success",                timestamp=_ts(2026, 5, 14, 3)),

        AuditEvent(event_type=EventType.BATCH_STARTED,    batch_id="batch-2026-05-27", detail="Batch started",             timestamp=_ts(2026, 5, 27, 2)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-05-27", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Starting disk cleanup",  timestamp=_ts(2026, 5, 27, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-05-27", vm_id="prod/web-01",    action_type="disk_cleanup", detail="Freed 800 MB",            timestamp=_ts(2026, 5, 27, 2, 4)),
        AuditEvent(event_type=EventType.ACTION_STARTED,   batch_id="batch-2026-05-27", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Starting disk cleanup",  timestamp=_ts(2026, 5, 27, 2, 1)),
        AuditEvent(event_type=EventType.ACTION_COMPLETED, batch_id="batch-2026-05-27", vm_id="prod/db-01",     action_type="disk_cleanup", detail="Freed 2.1 GB",            timestamp=_ts(2026, 5, 27, 2, 6)),
        AuditEvent(event_type=EventType.BATCH_COMPLETED,  batch_id="batch-2026-05-27", detail="2 success",                timestamp=_ts(2026, 5, 27, 3)),
    ]
    for e in events:
        await store.log_event(e)


async def _seed_ai_decisions(store: AIDecisionStore) -> None:
    model = "qwen3-8b-awq"
    base_url = "http://llm.internal.demo:8000/v1"
    decisions = [
        AIDecision(
            batch_id="batch-2026-05-28-prod", vm_id="prod/web-01",
            decision_type="prioritize_actions", model=model, base_url=base_url,
            prompt_template_id="prioritize_v2",
            prompt_hash=AIDecision.hash_prompt("prioritize prod/web-01"),
            outcome="success", latency_ms=842.0, prompt_tokens=512, completion_tokens=96,
            timestamp=_ts(2026, 5, 28, 1, 58),
            response_raw='{"order":["patching","log_rotation","disk_cleanup"],"reason":"3 security CVEs pending; /var at 86%"}',
        ),
        AIDecision(
            batch_id="batch-2026-05-28-prod", vm_id="prod/db-01",
            decision_type="prioritize_actions", model=model, base_url=base_url,
            prompt_template_id="prioritize_v2",
            prompt_hash=AIDecision.hash_prompt("prioritize prod/db-01"),
            outcome="fallback", latency_ms=None, prompt_tokens=None, completion_tokens=None,
            timestamp=_ts(2026, 5, 28, 1, 58),
            response_raw=None,
        ),
        AIDecision(
            batch_id="batch-2026-05-27", vm_id=None,
            decision_type="generate_report", model=model, base_url=base_url,
            prompt_template_id="report_v1",
            prompt_hash=AIDecision.hash_prompt("report batch-2026-05-27"),
            outcome="success", latency_ms=1310.0, prompt_tokens=903, completion_tokens=240,
            timestamp=_ts(2026, 5, 27, 3, 1),
            response_raw='{"summary":"2 VMs cleaned, 2.9 GB reclaimed, no high-risk actions."}',
        ),
    ]
    for d in decisions:
        await store.log(d)


def _demo_inventory() -> list[VMTarget]:
    return [
        VMTarget(vm_id="web-01", hostname="10.0.0.11", ssh_user="errander",
                 ssh_key_path="~/.ssh/demo_ed25519", os_family=OSFamily.UBUNTU,
                 policy="moderate", tags={"env": "production"}),
        VMTarget(vm_id="db-01", hostname="10.0.0.12", ssh_user="errander",
                 ssh_key_path="~/.ssh/demo_ed25519", os_family=OSFamily.DEBIAN,
                 policy="strict", tags={"env": "production"}),
        VMTarget(vm_id="app-01", hostname="10.0.0.21", ssh_user="errander",
                 ssh_key_path="~/.ssh/demo_ed25519", os_family=OSFamily.RHEL,
                 policy="relaxed", tags={"env": "staging"}),
    ]


async def _setup_server() -> tuple[AuditStore, AIDecisionStore, OverridesStore, object, int]:
    audit = AuditStore(":memory:")
    await audit.initialize()
    await _seed_audit(audit)

    ai = AIDecisionStore(":memory:")
    await ai.initialize()
    await _seed_ai_decisions(ai)

    overrides = OverridesStore(":memory:")
    await overrides.initialize()

    manager = ApprovalManager()
    manager.register(
        "batch-2026-05-28-prod", _SAMPLE_REPORT,
        slack_message_ts="1748400000.000001", vm_plans=_VM_PLANS,
    )
    manager.register("batch-2026-05-27", "Freed 2.9 GB across 2 VMs (live run)")
    manager.decide("batch-2026-05-27", approved=True, user_id="ops-team")

    runner = await start_metrics_server(
        port=0, audit_store=audit, approval_manager=manager,
        ai_decision_store=ai, overrides_store=overrides,
        base_inventory=_demo_inventory(),
    )
    site = list(runner.sites)[0]
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return audit, ai, overrides, runner, port


def _start_server() -> tuple[str, asyncio.AbstractEventLoop, dict[str, object], threading.Thread]:
    ready = threading.Event()
    ctx: dict[str, object] = {}

    async def _run() -> None:
        audit, ai, overrides, runner, port = await _setup_server()
        stop: asyncio.Event = asyncio.Event()
        ctx.update(audit=audit, ai=ai, overrides=overrides, runner=runner, port=port, stop=stop)
        ready.set()
        await stop.wait()
        await runner.cleanup()  # type: ignore[union-attr]
        await audit.close()
        await ai.close()
        await overrides.close()

    loop = asyncio.new_event_loop()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    if not ready.wait(timeout=15):
        raise RuntimeError("server failed to start")
    return f"http://localhost:{ctx['port']}", loop, ctx, t


# (path, output filename) — order matters only for console output.
_SHOTS: list[tuple[str, str]] = [
    ("/ui", "ui-dashboard.png"),
    ("/ui/approvals", "ui-approvals.png"),
    ("/ui/batches", "ui-batches.png"),
    ("/ui/batches/batch-2026-05-01", "ui-batch-detail.png"),
    ("/ui/ai-decisions", "ui-ai-decisions.png"),
    ("/ui/inventory", "ui-inventory.png"),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Starting Errander-AI UI with synthetic demo data...")
    base_url, loop, ctx, thread = _start_server()
    print(f"  Serving at {base_url}/ui\n")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1366, "height": 900}, device_scale_factor=2,
            )
            page = context.new_page()
            for i, (path, fname) in enumerate(_SHOTS, 1):
                page.goto(f"{base_url}{path}")
                page.wait_for_load_state("networkidle")
                page.evaluate(_BANNER_JS)
                out = OUT_DIR / fname
                page.screenshot(path=str(out), full_page=True)
                print(f"  [{i}/{len(_SHOTS)}] {path:<34} -> docs/images/{fname}")
            context.close()
            browser.close()
    finally:
        loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[arg-type]
        thread.join(timeout=5)

    print(f"\nDone. {len(_SHOTS)} screenshots written to {OUT_DIR}")


if __name__ == "__main__":
    main()
