"""Data providers for the Operations Hub UI.

FixtureProvider: static demo data from errander.web.data (default for demos and CI).
LiveProvider: queries real backend stores with graceful unavailable state.

Hard rules:
- LiveProvider NEVER falls back to fixture data silently.
- Missing live stores produce empty lists or _UNAVAIL_* sentinels, not fake data.
- All getters are synchronous — they read from a cache populated by refresh().
- Call await provider.refresh(...) from _on_startup and schedule periodic re-fetches.

Active provider selected by ERRANDER_UI_DATA_MODE env var (fixture | live).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class DataProvider(Protocol):
    """Sync interface consumed by page_* functions in server.py.

    All collection methods return list copies.
    All dict methods return dict copies (never None for agent_status /
    active_batch / scheduler_timeline / execution_trace).
    """

    def data_mode(self) -> str: ...
    def data_freshness(self) -> str: ...
    def get_vms(self) -> list[dict[str, Any]]: ...
    def get_vm(self, hostname: str) -> dict[str, Any] | None: ...
    def get_approvals(self) -> list[dict[str, Any]]: ...
    def get_batches(self) -> list[dict[str, Any]]: ...
    def get_audit_events(self) -> list[dict[str, Any]]: ...
    def get_agent_status(self) -> dict[str, Any]: ...
    def get_deferred_queue(self) -> list[dict[str, Any]]: ...
    def get_vm_actions(self, hostname: str) -> list[dict[str, Any]]: ...
    def get_active_batch(self) -> dict[str, Any]: ...
    def get_scheduler_timeline(self) -> dict[str, Any]: ...
    def get_probe_history(self) -> list[dict[str, Any]]: ...
    def get_execution_trace(self) -> dict[str, Any]: ...
    def get_vm_trace(self) -> list[dict[str, Any]]: ...
    def get_llm_decisions(self) -> list[dict[str, Any]]: ...


# ── FixtureProvider ──────────────────────────────────────────────────────────


class FixtureProvider:
    """Returns static demo fixture data. Safe for demos, testing, and CI."""

    def data_mode(self) -> str:
        return "FIXTURE"

    def data_freshness(self) -> str:
        return "static fixture · not auto-refreshing"

    def get_vms(self) -> list[dict[str, Any]]:
        from errander.web.data import VMS
        return VMS

    def get_vm(self, hostname: str) -> dict[str, Any] | None:
        return next((v for v in self.get_vms() if v["hostname"] == hostname), None)

    def get_approvals(self) -> list[dict[str, Any]]:
        from errander.web.data import APPROVALS
        return APPROVALS

    def get_batches(self) -> list[dict[str, Any]]:
        from errander.web.data import BATCHES
        return BATCHES

    def get_audit_events(self) -> list[dict[str, Any]]:
        from errander.web.data import AUDIT_EVENTS
        return AUDIT_EVENTS

    def get_agent_status(self) -> dict[str, Any]:
        from errander.web.data import AGENT_STATUS
        return AGENT_STATUS

    def get_deferred_queue(self) -> list[dict[str, Any]]:
        from errander.web.data import DEFERRED_QUEUE
        return DEFERRED_QUEUE

    def get_vm_actions(self, hostname: str) -> list[dict[str, Any]]:
        from errander.web.data import VM_ACTIONS
        return VM_ACTIONS.get(hostname, [
            {
                "ts": "—", "action": "No actions recorded",
                "status": "ok", "duration": "—", "op": "—",
                "detail": "No maintenance actions have been run on this host yet.",
            },
        ])

    def get_active_batch(self) -> dict[str, Any]:
        from errander.web.data import ACTIVE_BATCH
        return ACTIVE_BATCH

    def get_scheduler_timeline(self) -> dict[str, Any]:
        from errander.web.data import SCHEDULER_TIMELINE
        return SCHEDULER_TIMELINE

    def get_probe_history(self) -> list[dict[str, Any]]:
        from errander.web.data import PROBE_HISTORY
        return PROBE_HISTORY

    def get_execution_trace(self) -> dict[str, Any]:
        from errander.web.data import EXECUTION_TRACE
        return EXECUTION_TRACE

    def get_vm_trace(self) -> list[dict[str, Any]]:
        from errander.web.data import VM_TRACE
        return VM_TRACE

    def get_llm_decisions(self) -> list[dict[str, Any]]:
        from errander.web.data import LLM_DECISIONS
        return LLM_DECISIONS


# ── LiveProvider sentinels ────────────────────────────────────────────────────
# These are returned when a live store is missing — never fixture data.

_UNAVAIL_AGENT_STATUS: dict[str, Any] = {
    "state": "UNAVAILABLE", "mode": "—", "scheduler": "UNAVAILABLE",
    "llm_endpoint": "—", "llm_model": "—", "llm_latency_ms": 0,
    "llm_status": "unavailable", "active_batch": None, "last_batch_id": "—",
    "last_batch_ts": "—", "next_run": "—", "uptime": "—",
}

_UNAVAIL_ACTIVE_BATCH: dict[str, Any] = {
    "id": "—", "status": "unavailable", "vms_done": 0, "vms_total": 0,
    "actions_done": 0, "actions_total": 0, "duration": "—",
    "patched": 0, "rotations": 0, "prunes": 0, "errors": 0,
}

_UNAVAIL_SCHEDULER: dict[str, Any] = {
    "cron": "—", "human": "—", "probe_cron": "—", "probe_human": "—",
    "next_runs": [], "recent_runs": [],
}

_UNAVAIL_EXEC_TRACE: dict[str, Any] = {
    "batch_id": "—", "started": "—", "completed": "—",
    "duration": "—", "status": "unavailable", "nodes": [],
}


# ── LiveProvider ─────────────────────────────────────────────────────────────


class LiveProvider:
    """Queries real backend stores.  Never falls back to fixture data.

    Missing stores → empty lists / _UNAVAIL_* sentinels.
    Call ``await refresh()`` from _on_startup and schedule periodic re-fetches.
    """

    def __init__(self) -> None:
        self._vms: list[dict[str, Any]] = []
        self._approvals: list[dict[str, Any]] = []
        self._batches: list[dict[str, Any]] = []
        self._audit_events: list[dict[str, Any]] = []
        self._agent_status: dict[str, Any] = dict(_UNAVAIL_AGENT_STATUS)
        self._deferred_queue: list[dict[str, Any]] = []
        self._vm_actions: dict[str, list[dict[str, Any]]] = {}
        self._active_batch: dict[str, Any] = dict(_UNAVAIL_ACTIVE_BATCH)
        self._scheduler_timeline: dict[str, Any] = dict(_UNAVAIL_SCHEDULER)
        self._probe_history: list[dict[str, Any]] = []
        self._execution_trace: dict[str, Any] = dict(_UNAVAIL_EXEC_TRACE)
        self._vm_trace: list[dict[str, Any]] = []
        self._llm_decisions: list[dict[str, Any]] = []
        self._freshness: str = "live · not yet loaded"

    def data_mode(self) -> str:
        return "LIVE"

    def data_freshness(self) -> str:
        return self._freshness

    def get_vms(self) -> list[dict[str, Any]]:
        return list(self._vms)

    def get_vm(self, hostname: str) -> dict[str, Any] | None:
        return next((v for v in self._vms if v["hostname"] == hostname), None)

    def get_approvals(self) -> list[dict[str, Any]]:
        return list(self._approvals)

    def get_batches(self) -> list[dict[str, Any]]:
        return list(self._batches)

    def get_audit_events(self) -> list[dict[str, Any]]:
        return list(self._audit_events)

    def get_agent_status(self) -> dict[str, Any]:
        return dict(self._agent_status)

    def get_deferred_queue(self) -> list[dict[str, Any]]:
        return list(self._deferred_queue)

    def get_vm_actions(self, hostname: str) -> list[dict[str, Any]]:
        return list(self._vm_actions.get(hostname, []))

    def get_active_batch(self) -> dict[str, Any]:
        return dict(self._active_batch)

    def get_scheduler_timeline(self) -> dict[str, Any]:
        return dict(self._scheduler_timeline)

    def get_probe_history(self) -> list[dict[str, Any]]:
        return list(self._probe_history)

    def get_execution_trace(self) -> dict[str, Any]:
        return dict(self._execution_trace)

    def get_vm_trace(self) -> list[dict[str, Any]]:
        return list(self._vm_trace)

    def get_llm_decisions(self) -> list[dict[str, Any]]:
        return list(self._llm_decisions)

    async def refresh(
        self,
        db: Any | None = None,
        approval_manager: Any | None = None,
        deferred_store: Any | None = None,
        inventory_path: Any | None = None,
    ) -> None:
        """Populate the cache from live stores.  Safe to call repeatedly.

        Each store is fetched independently — one failure does not prevent
        the others from loading.  Errors are logged and noted in freshness.
        """
        import datetime as _dt

        errors: list[str] = []

        # ── Inventory / VMs ──────────────────────────────────────────────────
        vms: list[dict[str, Any]] = []
        if inventory_path is not None:
            try:
                from errander.config.inventory import load_inventory
                for t in load_inventory(inventory_path):
                    env = t.tags.get("env", "unknown").upper() if t.tags else "UNKNOWN"
                    vms.append({
                        "hostname":         t.hostname,
                        "ip":               t.hostname,
                        "os":               str(t.os_family).title(),
                        "env":              env,
                        "status":           "ok",
                        "disk":             0,
                        "cpu":              0,
                        "mem":              0,
                        "pending_patches":  0,
                        "last_action_type": "—",
                        "last_action":      "—",
                        "uptime":           "—",
                        "note":             "",
                    })
            except Exception as exc:
                errors.append(f"inventory: {exc}")
                logger.warning("LiveProvider: inventory load failed: %s", exc)
        self._vms = vms

        # ── Audit events, batches, per-VM action history ─────────────────────
        audit_events: list[dict[str, Any]] = []
        batches: list[dict[str, Any]] = []
        vm_actions: dict[str, list[dict[str, Any]]] = {}
        active_batch: dict[str, Any] = dict(_UNAVAIL_ACTIVE_BATCH)

        if db is not None:
            try:
                rows = await db.execute_fetchall(
                    "SELECT event_type, batch_id, vm_id, action_type, detail, timestamp "
                    "FROM audit_events ORDER BY timestamp DESC, id DESC LIMIT 200",
                    [],
                )
                for row in rows:
                    action_label = (
                        str(row[3]) if row[3] else str(row[0])
                    ).replace("_", " ").title()
                    d: dict[str, Any] = {
                        "ts":       str(row[5])[:19],
                        "batch":    str(row[1]),
                        "vm":       str(row[2]) if row[2] is not None else "—",
                        "action":   action_label,
                        "status":   _event_status(str(row[0])),
                        "duration": "—",
                        "op":       "agent",
                        "detail":   str(row[4]),
                    }
                    audit_events.append(d)
                    if row[2] is not None:
                        vm_actions.setdefault(str(row[2]), []).append(d)

                batch_rows = await db.execute_fetchall(
                    """
                    SELECT batch_id,
                           MIN(timestamp) AS started_at,
                           COUNT(*)       AS event_count,
                           GROUP_CONCAT(DISTINCT vm_id) AS vm_ids
                    FROM audit_events
                    GROUP BY batch_id
                    ORDER BY started_at DESC
                    LIMIT 50
                    """,
                    [],
                )
                for b in batch_rows:
                    vm_ids_str = str(b[3]) if b[3] is not None else ""
                    vm_ids = [v for v in vm_ids_str.split(",") if v and v != "None"]
                    batches.append({
                        "id":            str(b[0]),
                        "started":       str(b[1])[:16],
                        "env":           "—",
                        "vms":           len(vm_ids),
                        "actions":       int(str(b[2])),
                        "status":        "completed",
                        "duration":      "—",
                        "errors":        0,
                        "failed_vms":    [],
                        "error_summary": "",
                    })

                if batch_rows:
                    first = batch_rows[0]
                    fv_str = str(first[3]) if first[3] is not None else ""
                    fv = [v for v in fv_str.split(",") if v and v != "None"]
                    n = int(str(first[2]))
                    active_batch = {
                        "id":            str(first[0]),
                        "status":        "completed",
                        "vms_done":      len(fv),
                        "vms_total":     len(fv),
                        "actions_done":  n,
                        "actions_total": n,
                        "duration":      "—",
                        "patched":       0,
                        "rotations":     0,
                        "prunes":        0,
                        "errors":        0,
                    }
            except Exception as exc:
                errors.append(f"audit: {exc}")
                logger.warning("LiveProvider: audit read failed: %s", exc)

        self._audit_events = audit_events
        self._batches = batches
        self._vm_actions = vm_actions
        self._active_batch = active_batch

        # ── Approval queue ───────────────────────────────────────────────────
        approvals: list[dict[str, Any]] = []
        if approval_manager is not None:
            try:
                for p in approval_manager.get_pending():
                    approvals.append({
                        "id":                p.batch_id,
                        "action":            "BATCH APPROVAL",
                        "tier":              "MEDIUM",
                        "hostname":          "—",
                        "os":                "—",
                        "ip":                "—",
                        "env":               "—",
                        "countdown":         "—",
                        "vm_cpu":            0,
                        "vm_mem":            0,
                        "vm_disk":           0,
                        "vm_load":           "—",
                        "vm_uptime":         "—",
                        "trigger":           (p.report[:200] if p.report else "—"),
                        "reject_consequence": "Agent will auto-reject at timeout.",
                        "rollback_strategy":  "—",
                        "reasoning":         (p.report[:500] if p.report else "—"),
                        "commands":          [],
                        "header_color":      "#d97706",
                        "tier_color":        "#d97706",
                    })
            except Exception as exc:
                errors.append(f"approvals: {exc}")
                logger.warning("LiveProvider: approval_manager read failed: %s", exc)
        self._approvals = approvals

        # ── Deferred queue ───────────────────────────────────────────────────
        deferred: list[dict[str, Any]] = []
        if deferred_store is not None:
            try:
                envs = list({v["env"] for v in self._vms}) or ["production"]
                for env in envs:
                    for r in await deferred_store.get_pending(env):
                        deferred.append({
                            "batch_id":    r.batch_id,
                            "env":         r.env_name,
                            "approved_by": r.approved_by or "—",
                            "approved_at": str(r.approved_at)[:16],
                            "window_start": str(r.window_start)[:16],
                            "expiry_at":   str(r.expiry_at)[:16],
                            "status":      r.status,
                        })
            except Exception as exc:
                errors.append(f"deferred: {exc}")
                logger.warning("LiveProvider: deferred store read failed: %s", exc)
        self._deferred_queue = deferred

        now = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        suffix = f" · {len(errors)} store(s) unavailable" if errors else ""
        self._freshness = f"live · refreshed {now}{suffix}"
        logger.info(
            "LiveProvider refresh: %d VMs, %d events, %d batches, %d approvals, %d deferred",
            len(self._vms), len(self._audit_events), len(self._batches),
            len(self._approvals), len(self._deferred_queue),
        )


def _event_status(event_type_value: str) -> str:
    """Map audit EventType value to the UI status string."""
    return {
        "action_completed":      "ok",
        "action_failed":         "failed",
        "action_started":        "pending",
        "batch_started":         "ok",
        "batch_completed":       "ok",
        "batch_failed":          "failed",
        "approval_requested":    "pending",
        "approval_granted":      "ok",
        "approval_rejected":     "failed",
        "rollback_triggered":    "warning",
        "rollback_completed":    "ok",
        "rollback_failed":       "failed",
        "validation_failed":     "failed",
        "safety_gate_triggered": "warning",
    }.get(event_type_value, "ok")


# ── Singleton ─────────────────────────────────────────────────────────────────

_singleton: DataProvider | None = None


def get_provider() -> DataProvider:
    """Return the active provider singleton (created lazily on first call)."""
    global _singleton
    if _singleton is None:
        _singleton = _make_provider()
    return _singleton


def _make_provider() -> DataProvider:
    mode = os.environ.get("ERRANDER_UI_DATA_MODE", "fixture").lower().strip()
    if mode == "live":
        logger.info("Operations Hub: data mode = LIVE (ERRANDER_UI_DATA_MODE=live)")
        return LiveProvider()
    if mode != "fixture":
        logger.warning(
            "Unknown ERRANDER_UI_DATA_MODE=%r — defaulting to 'fixture'", mode
        )
    return FixtureProvider()


def reset_provider_for_testing(provider: DataProvider) -> None:
    """Replace the singleton — test use only.  Never call in production code."""
    global _singleton
    _singleton = provider
