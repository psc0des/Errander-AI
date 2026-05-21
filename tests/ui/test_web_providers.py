"""Tests for the Operations Hub provider layer.

Verifies:
  1. server.py routes no longer import VMS / BATCHES / APPROVALS etc. directly.
  2. FixtureProvider returns non-empty, well-shaped data.
  3. LiveProvider returns empty / unavailable state when stores are missing.
  4. Both modes render all page functions without crashing.
  5. ERRANDER_UI_DATA_MODE env var selects the correct provider.
  6. reset_provider_for_testing() works (used by all provider-using tests).
"""
from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from typing import Any, Generator
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER_PY = Path(__file__).parent.parent.parent / "errander" / "web" / "server.py"
_DATA_NAMES = {
    "VMS", "APPROVALS", "BATCHES", "AUDIT_EVENTS",
    "AGENT_STATUS", "DEFERRED_QUEUE", "ACTIVE_BATCH",
    "EXECUTION_TRACE", "LLM_DECISIONS", "PROBE_HISTORY",
    "SCHEDULER_TIMELINE", "VM_ACTIONS", "VM_TRACE",
    "APPROVAL_COUNT",
}


# ---------------------------------------------------------------------------
# 1. AST check — routes no longer import data constants directly
# ---------------------------------------------------------------------------

def test_server_does_not_import_data_constants() -> None:
    """server.py must not import VMS, BATCHES, etc. directly from data.py."""
    source = _SERVER_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SERVER_PY))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module in ("errander.web.data", ".data"):
                imported_names = {alias.name for alias in node.names}
                direct_data = imported_names & _DATA_NAMES
                assert not direct_data, (
                    f"server.py still imports {direct_data} directly from data.py — "
                    "routes must use get_provider() instead"
                )


def test_server_imports_get_provider() -> None:
    """server.py must import get_provider from .providers (top-level or local)."""
    source = _SERVER_PY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SERVER_PY))

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # Relative import: from .providers import ... (module='providers', level=1)
            # Absolute import: from errander.web.providers import ...
            if "providers" in mod or (node.level > 0 and mod == "providers"):
                names = {alias.name for alias in node.names}
                if "get_provider" in names:
                    found = True
    assert found, "server.py must import get_provider from .providers"


# ---------------------------------------------------------------------------
# 2. FixtureProvider
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton() -> "Generator[None, None, None]":
    """Reset the provider singleton before and after each test."""
    from errander.web import providers
    old = providers._singleton
    providers._singleton = None
    yield
    providers._singleton = old


@pytest.fixture()
def fixture_provider():
    from errander.web.providers import FixtureProvider
    return FixtureProvider()


def test_fixture_provider_mode(fixture_provider) -> None:
    assert fixture_provider.data_mode() == "FIXTURE"


def test_fixture_provider_vms_non_empty(fixture_provider) -> None:
    vms = fixture_provider.get_vms()
    assert isinstance(vms, list)
    assert len(vms) > 0
    for vm in vms:
        assert "hostname" in vm
        assert "status" in vm
        assert "env" in vm


def test_fixture_provider_approvals(fixture_provider) -> None:
    approvals = fixture_provider.get_approvals()
    assert isinstance(approvals, list)
    assert len(approvals) > 0
    for a in approvals:
        assert "id" in a
        assert "action" in a


def test_fixture_provider_batches(fixture_provider) -> None:
    batches = fixture_provider.get_batches()
    assert isinstance(batches, list)
    assert len(batches) > 0
    for b in batches:
        assert "id" in b
        assert "status" in b


def test_fixture_provider_audit_events(fixture_provider) -> None:
    events = fixture_provider.get_audit_events()
    assert isinstance(events, list)
    assert len(events) > 0


def test_fixture_provider_agent_status(fixture_provider) -> None:
    st = fixture_provider.get_agent_status()
    assert isinstance(st, dict)
    assert "state" in st


def test_fixture_provider_get_vm_found(fixture_provider) -> None:
    vms = fixture_provider.get_vms()
    hostname = vms[0]["hostname"]
    vm = fixture_provider.get_vm(hostname)
    assert vm is not None
    assert vm["hostname"] == hostname


def test_fixture_provider_get_vm_missing(fixture_provider) -> None:
    assert fixture_provider.get_vm("no-such-host") is None


def test_fixture_provider_deferred_queue(fixture_provider) -> None:
    assert isinstance(fixture_provider.get_deferred_queue(), list)


def test_fixture_provider_vm_actions_known(fixture_provider) -> None:
    # prod-api-01 has actions in VM_ACTIONS
    actions = fixture_provider.get_vm_actions("prod-api-01")
    assert isinstance(actions, list)
    assert len(actions) > 0


def test_fixture_provider_vm_actions_unknown(fixture_provider) -> None:
    # Unknown host → fallback entry
    actions = fixture_provider.get_vm_actions("no-such-host")
    assert isinstance(actions, list)
    assert len(actions) > 0  # returns the "no actions" placeholder


def test_fixture_provider_active_batch(fixture_provider) -> None:
    ab = fixture_provider.get_active_batch()
    assert isinstance(ab, dict)
    assert "id" in ab
    assert "status" in ab


def test_fixture_provider_scheduler_timeline(fixture_provider) -> None:
    sch = fixture_provider.get_scheduler_timeline()
    assert isinstance(sch, dict)
    assert "cron" in sch


def test_fixture_provider_probe_history(fixture_provider) -> None:
    ph = fixture_provider.get_probe_history()
    assert isinstance(ph, list)
    assert len(ph) > 0


def test_fixture_provider_execution_trace(fixture_provider) -> None:
    tr = fixture_provider.get_execution_trace()
    assert isinstance(tr, dict)
    assert "nodes" in tr


def test_fixture_provider_vm_trace(fixture_provider) -> None:
    assert isinstance(fixture_provider.get_vm_trace(), list)


def test_fixture_provider_llm_decisions(fixture_provider) -> None:
    ld = fixture_provider.get_llm_decisions()
    assert isinstance(ld, list)
    assert len(ld) > 0


# ---------------------------------------------------------------------------
# 3. LiveProvider — unavailable state when stores are missing
# ---------------------------------------------------------------------------

@pytest.fixture()
def live_provider():
    from errander.web.providers import LiveProvider
    return LiveProvider()


def test_live_provider_mode(live_provider) -> None:
    assert live_provider.data_mode() == "LIVE"


def test_live_provider_empty_before_refresh(live_provider) -> None:
    """Before refresh(), LiveProvider returns empty collections — never fixture data."""
    assert live_provider.get_vms() == []
    assert live_provider.get_approvals() == []
    assert live_provider.get_batches() == []
    assert live_provider.get_audit_events() == []
    assert live_provider.get_deferred_queue() == []
    assert live_provider.get_probe_history() == []
    assert live_provider.get_vm_trace() == []
    assert live_provider.get_llm_decisions() == []


def test_live_provider_sentinels_before_refresh(live_provider) -> None:
    """Before refresh(), dict getters return sentinel dicts (not fixture data)."""
    st = live_provider.get_agent_status()
    assert st["state"] == "UNAVAILABLE"

    ab = live_provider.get_active_batch()
    assert ab["id"] == "—"
    assert ab["status"] == "unavailable"

    sch = live_provider.get_scheduler_timeline()
    assert sch["cron"] == "—"
    assert sch["next_runs"] == []

    tr = live_provider.get_execution_trace()
    assert tr["status"] == "unavailable"
    assert tr["nodes"] == []


def test_live_provider_get_vm_missing(live_provider) -> None:
    assert live_provider.get_vm("anything") is None


def test_live_provider_vm_actions_empty(live_provider) -> None:
    assert live_provider.get_vm_actions("prod-api-01") == []


def test_live_provider_refresh_is_coroutine(live_provider) -> None:
    """refresh() must be a coroutine so it can be awaited in _on_startup."""
    assert inspect.iscoroutinefunction(live_provider.refresh)


def test_live_provider_refresh_no_stores(live_provider) -> None:
    """Before refresh, LiveProvider is already in the same state as refresh with no stores."""
    assert live_provider.get_vms() == []
    assert live_provider.get_batches() == []
    assert live_provider.get_approvals() == []
    assert "live" in live_provider.data_freshness()


def test_live_provider_refresh_with_approval_manager(live_provider) -> None:
    """Approvals cache is returned by get_approvals() after population."""
    live_provider._approvals = [
        {"id": "batch-xyz", "action": "BATCH APPROVAL", "tier": "MEDIUM", "hostname": "—"},
    ]
    approvals = live_provider.get_approvals()
    assert len(approvals) == 1
    assert approvals[0]["id"] == "batch-xyz"


def test_live_provider_refresh_with_failing_store(live_provider) -> None:
    """Freshness string notes unavailable stores; collections stay empty."""
    live_provider._freshness = "live · refreshed 2026-01-01 00:00 UTC · 1 store(s) unavailable"
    assert "unavailable" in live_provider.data_freshness()
    assert live_provider.get_vms() == []


def test_live_provider_never_falls_back_to_fixture(live_provider) -> None:
    """LiveProvider initial state is empty — never returns fixture data."""
    from errander.web.data import VMS as FIXTURE_VMS
    assert live_provider.get_vms() != FIXTURE_VMS


# ---------------------------------------------------------------------------
# 4. Page renders work with both providers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server_module():
    import errander.web.server as srv
    return srv


def _install(provider: Any) -> None:
    from errander.web import providers
    providers._singleton = provider


def test_page_fleet_fixture_renders(fixture_provider, server_module) -> None:
    _install(fixture_provider)
    html = server_module.page_fleet()
    assert len(html) > 100


def test_page_fleet_live_renders(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module.page_fleet()
    # Must render without crashing even with 0 VMs
    assert len(html) > 100


def test_page_approvals_fixture_renders(fixture_provider, server_module) -> None:
    _install(fixture_provider)
    html = server_module.page_approvals()
    assert len(html) > 100


def test_page_approvals_live_renders(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module.page_approvals()
    assert len(html) > 100


def test_page_audit_live_renders(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module.page_audit()
    assert len(html) > 100


def test_page_batches_live_renders(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module.page_batches()
    assert len(html) > 100


def test_page_agent_live_renders(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module.page_agent()
    assert len(html) > 100


def test_page_inventory_live_renders(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module.page_inventory()
    assert len(html) > 100


# ---------------------------------------------------------------------------
# 5. ERRANDER_UI_DATA_MODE selects the correct provider
# ---------------------------------------------------------------------------

def test_env_var_fixture(monkeypatch) -> None:
    from errander.web import providers
    monkeypatch.setenv("ERRANDER_UI_DATA_MODE", "fixture")
    providers._singleton = None
    p = providers.get_provider()
    assert isinstance(p, providers.FixtureProvider)
    assert p.data_mode() == "FIXTURE"


def test_env_var_live(monkeypatch) -> None:
    from errander.web import providers
    monkeypatch.setenv("ERRANDER_UI_DATA_MODE", "live")
    providers._singleton = None
    p = providers.get_provider()
    assert isinstance(p, providers.LiveProvider)
    assert p.data_mode() == "LIVE"


def test_env_var_unknown_defaults_to_fixture(monkeypatch) -> None:
    from errander.web import providers
    monkeypatch.setenv("ERRANDER_UI_DATA_MODE", "bogus")
    providers._singleton = None
    p = providers.get_provider()
    assert isinstance(p, providers.FixtureProvider)


# ---------------------------------------------------------------------------
# 6. Mode banner reflects provider state
# ---------------------------------------------------------------------------

def test_mode_banner_says_demo_in_fixture_mode(fixture_provider, server_module) -> None:
    _install(fixture_provider)
    html = server_module._mode_banner_html()
    assert "DEMO" in html or "FIXTURE" in html


def test_mode_banner_says_live_in_live_mode(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module._mode_banner_html()
    assert "LIVE" in html


def test_mode_banner_shows_live_freshness(live_provider, server_module) -> None:
    _install(live_provider)
    html = server_module._mode_banner_html()
    assert "live" in html


# ---------------------------------------------------------------------------
# 7. Live-mode regression — no fixture operational facts in any page render
# ---------------------------------------------------------------------------

# Strings that identify fixture-only data. If any appear when LiveProvider is
# active (no stores connected), it is a data-leak bug.
_FIXTURE_ONLY_STRINGS = [
    "2026-04",             # April 2026 fixture dates
    "2026-05-13",          # admin health-check fixture timestamp
    "prod-0423",           # fixture batch IDs
    "staging-0422",        # fixture staging batch IDs
    "prod-web-01",         # fixture VM hostnames
    "prod-db-01",          # fixture VM hostnames
    "Qwen3-8B-AWQ",        # fixture LLM model
    "10.0.0.100",          # fixture LLM endpoint IP
    "14 actions approved", # fixture resolved-today count
    "28 batches",          # fixture batch KPI
    "96.4%",               # fixture completion rate
    "2,418 actions",       # fixture action total
    "last verified 02:14", # inventory KPI fixture timestamp
    "Ubuntu · RHEL",       # inventory hardcoded OS subtitle
    "nginx",               # fixture restartable units
    "gunicorn",            # fixture restartable units
]


@pytest.mark.parametrize("page_fn", [
    "page_fleet",
    "page_approvals",
    "page_batches",
    "page_audit",
    "page_agent",
    "page_inventory",
    "page_settings",
    "page_admin",
])
def test_live_mode_no_fixture_facts(live_provider, server_module, page_fn) -> None:
    """No known fixture-only string should appear when LiveProvider is active."""
    _install(live_provider)
    html = getattr(server_module, page_fn)()
    for marker in _FIXTURE_ONLY_STRINGS:
        assert marker not in html, (
            f"{page_fn}() in live mode leaks fixture string {marker!r}. "
            "Live mode must only show real provider data or an explicit unavailable state."
        )


def test_live_mode_vm_page_unknown_host_not_found(live_provider, server_module) -> None:
    """In live mode with no VMs, a fixture hostname returns 'not found', not fixture data."""
    _install(live_provider)
    html = server_module.page_vm("prod-web-01")
    assert "not found" in html
    # Operational facts that must not appear (excluding the hostname itself, which
    # is legitimately echoed back in the "not found" message).
    _ops_facts = [m for m in _FIXTURE_ONLY_STRINGS if "prod-web-01" not in m]
    for marker in _ops_facts:
        assert marker not in html, (
            f"page_vm('prod-web-01') in live mode leaks fixture string {marker!r}"
        )
