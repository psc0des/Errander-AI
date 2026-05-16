"""Phase F Commit 1 tests: stored signals feed into plan_vm_node and prioritize_actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from errander.agent.decisions import StoredSignalContext, _build_prioritize_prompt
from errander.agent.graph import _load_stored_signals
from errander.models.actions import ActionType
from errander.models.events import EventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audit_store(
    drift_events: list[object] | None = None,
    failure_events: list[object] | None = None,
    completed_events: list[object] | None = None,
    login_events: list[object] | None = None,
) -> MagicMock:
    store = MagicMock()

    async def _get_events(vm_id: str, event_type: object, limit: int = 100) -> list[object]:
        if event_type == EventType.DRIFT_KIND_CHANGED:
            return drift_events or []
        if event_type == EventType.ACTION_FAILED:
            return failure_events or []
        if event_type == EventType.ACTION_COMPLETED:
            return completed_events or []
        if event_type == EventType.FAILED_SSH_LOGINS_OBSERVED:
            return login_events or []
        return []

    store.get_events = _get_events
    return store


def _make_disk_store(points_by_mp: dict[str, list[object]] | None = None) -> MagicMock:
    from errander.safety.disk_history import VMDiskHistoryStore

    store = MagicMock(spec=VMDiskHistoryStore)

    async def _get_distinct(vm_id: str) -> list[str]:
        return list((points_by_mp or {}).keys())

    async def _get_window(vm_id: str, mountpoint: str, window_days: int = 7) -> list[object]:
        return (points_by_mp or {}).get(mountpoint, [])

    store.get_distinct_mountpoints = _get_distinct
    store.get_window = _get_window
    return store


def _make_disk_point(used_pct: float) -> MagicMock:
    p = MagicMock()
    p.used_pct = used_pct
    return p


def _make_drift_event(kind: str) -> MagicMock:
    ev = MagicMock()
    ev.metadata = {"kind": kind}
    return ev


def _make_failure_event() -> MagicMock:
    return MagicMock()


def _make_completed_patching_event(days_ago: int = 10) -> MagicMock:
    import datetime
    ev = MagicMock()
    ev.action_type = "patching"
    ev.timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    return ev


def _make_login_event(total: int = 5) -> MagicMock:
    ev = MagicMock()
    ev.metadata = {"total_count": total}
    return ev


def _make_vm_info() -> MagicMock:
    from errander.models.actions import ActionType
    from errander.models.vm import OSFamily
    vi = MagicMock()
    vi.os_family = OSFamily.UBUNTU
    vi.os_version = "22.04"
    vi.disk_usage = {"/": 60.0}
    vi.docker_available = False
    vi.pending_packages = 3
    vi.uptime_seconds = 86400
    return vi


# ---------------------------------------------------------------------------
# _load_stored_signals tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_stored_signals_disk_trend() -> None:
    points = [_make_disk_point(65.0), _make_disk_point(78.0)]
    disk_store = _make_disk_store({"/": points})

    ctx = await _load_stored_signals("vm-1", None, disk_store, None)

    assert "/: 78%" in ctx.disk_trend_summary
    assert "+13% over 7d" in ctx.disk_trend_summary


@pytest.mark.asyncio
async def test_load_stored_signals_drift_detected() -> None:
    events = [_make_drift_event("sudoers"), _make_drift_event("authorized_keys")]
    audit = _make_audit_store(drift_events=events)

    ctx = await _load_stored_signals("vm-1", audit, None, None)

    assert "authorized_keys" in ctx.drift_kinds_detected
    assert "sudoers" in ctx.drift_kinds_detected


@pytest.mark.asyncio
async def test_load_stored_signals_recent_failures() -> None:
    events = [_make_failure_event(), _make_failure_event(), _make_failure_event()]
    audit = _make_audit_store(failure_events=events)

    ctx = await _load_stored_signals("vm-1", audit, None, None)

    assert ctx.recent_failure_count == 3


@pytest.mark.asyncio
async def test_load_stored_signals_last_patch_date() -> None:
    patch_ev = _make_completed_patching_event(days_ago=15)
    audit = _make_audit_store(completed_events=[patch_ev])

    ctx = await _load_stored_signals("vm-1", audit, None, None)

    assert ctx.last_patch_days_ago == 15


@pytest.mark.asyncio
async def test_load_stored_signals_failed_logins() -> None:
    events = [_make_login_event(7), _make_login_event(3)]
    audit = _make_audit_store(login_events=events)

    ctx = await _load_stored_signals("vm-1", audit, None, None)

    assert ctx.failed_login_count_24h == 10


@pytest.mark.asyncio
async def test_load_stored_signals_no_stores() -> None:
    ctx = await _load_stored_signals("vm-1", None, None, None)

    assert isinstance(ctx, StoredSignalContext)
    assert ctx.disk_trend_summary == ""
    assert ctx.drift_kinds_detected == []
    assert ctx.recent_failure_count == 0
    assert ctx.last_patch_days_ago is None
    assert ctx.failed_login_count_24h == 0


@pytest.mark.asyncio
async def test_load_stored_signals_store_exception() -> None:
    audit = MagicMock()
    audit.get_events = AsyncMock(side_effect=RuntimeError("DB down"))

    ctx = await _load_stored_signals("vm-1", audit, None, None)

    assert isinstance(ctx, StoredSignalContext)
    assert ctx.recent_failure_count == 0


# ---------------------------------------------------------------------------
# _build_prioritize_prompt with stored_signals
# ---------------------------------------------------------------------------


def test_prioritize_actions_with_signals() -> None:
    vm_info = _make_vm_info()
    actions = [ActionType.PATCHING, ActionType.DISK_CLEANUP]
    signals = StoredSignalContext(
        disk_trend_summary="/: 78% (+13% over 7d)",
        drift_kinds_detected=["sudoers"],
        recent_failure_count=2,
        last_patch_days_ago=30,
        failed_login_count_24h=5,
    )

    prompt = _build_prioritize_prompt(vm_info, actions, signals)

    assert "Historical signals" in prompt
    assert "/: 78% (+13% over 7d)" in prompt
    assert "sudoers" in prompt
    assert "Action failures (14d): 2" in prompt
    assert "Last patched: 30 days ago" in prompt
    assert "Failed SSH logins (24h): 5" in prompt


def test_prioritize_actions_signals_none() -> None:
    vm_info = _make_vm_info()
    actions = [ActionType.PATCHING]

    prompt = _build_prioritize_prompt(vm_info, actions, None)

    assert "Historical signals" not in prompt
    assert "disk_trend" not in prompt
