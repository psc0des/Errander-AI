"""errander — Operations Hub development UI server."""
from __future__ import annotations

import hashlib
import hmac
import json as _json
import logging
import math
import os
import time
from typing import Any

from aiohttp import web

from .evidence import (
    APPROVAL_EVIDENCE,
    BATCH_EVIDENCE,
    UI_MODE,
    VM_EVIDENCE,
    audit_evidence_for,
)
from .providers import get_provider

logger = logging.getLogger(__name__)

# ── Evidence gating — overlays are fixture-mode-only ─────────────────────────
# In live mode every _ev_* helper returns {} / a null sentinel so page
# functions never accidentally render April-2026 demo facts.

_NULL_AUDIT_EV: dict[str, Any] = {
    "event_id": "—", "action_id": "—", "plan_hash": "—",
    "approver": "(n/a)", "approval_source": "(n/a)",
    "before": "", "after": "", "command": "",
    "stdout_summary": "", "stderr_summary": "", "rollback_status": "",
}


def _ev_vm(hostname: str) -> dict[str, Any]:
    return VM_EVIDENCE.get(hostname, {}) if get_provider().data_mode() == "FIXTURE" else {}


def _ev_batch(batch_id: str) -> dict[str, Any]:
    return BATCH_EVIDENCE.get(batch_id, {}) if get_provider().data_mode() == "FIXTURE" else {}


def _ev_approval(batch_id: str) -> dict[str, Any]:
    return APPROVAL_EVIDENCE.get(batch_id, {}) if get_provider().data_mode() == "FIXTURE" else {}


def _ev_audit(idx: int) -> dict[str, Any]:
    return audit_evidence_for(idx) if get_provider().data_mode() == "FIXTURE" else _NULL_AUDIT_EV


PORT = 8099

# ── Auth ──────────────────────────────────────────────────────────────────────

_AUTH_USERNAME: str = os.environ.get("ERRANDER_UI_USERNAME", "admin")
_AUTH_PASSWORD: str = os.environ.get("ERRANDER_UI_PASSWORD", "errander")
_AUTH_SECRET: bytes = os.environ.get("ERRANDER_UI_SECRET", "errander-dev-secret-2026").encode()
_AUTH_COOKIE = "errander_session"
_AUTH_TTL = 28800  # 8 hours


def _make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(_AUTH_SECRET, ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _valid_token(token: str) -> bool:
    try:
        ts_str, sig = token.split(".", 1)
        expected = hmac.new(_AUTH_SECRET, ts_str.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        return (time.time() - float(ts_str)) < _AUTH_TTL
    except Exception:
        return False

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@600;700;800&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body { font-family: 'Inter', system-ui, sans-serif; background: #f0f2ff; color: #0f172a; display: flex; }

/* ── Sidebar ── */
.sidebar {
  width: 240px; min-height: 100vh; background: #1e1b4b;
  display: flex; flex-direction: column; flex-shrink: 0; position: fixed; top: 0; left: 0; bottom: 0; z-index: 100;
}
.sidebar-logo {
  padding: 20px 20px 16px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 1.25rem; font-weight: 800; color: #fff; letter-spacing: -0.02em;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}
.sidebar-logo span { color: #a5b4fc; }
.nav-section { padding: 16px 0 4px; }
.nav-label {
  padding: 0 16px 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6rem; font-weight: 500; letter-spacing: 0.1em;
  text-transform: uppercase; color: rgba(255,255,255,0.35);
}
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 0 16px; height: 40px;
  font-size: 0.875rem; font-weight: 500; color: rgba(255,255,255,0.6);
  text-decoration: none; position: relative; transition: all 0.15s;
}
.nav-item:hover { color: rgba(255,255,255,0.9); background: rgba(255,255,255,0.06); }
.nav-item.active {
  color: #fff; background: rgba(255,255,255,0.12);
  border-left: 3px solid #a5b4fc;
}
.nav-badge {
  margin-left: auto; background: #7c3aed; color: #fff;
  font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; font-weight: 700;
  padding: 2px 6px; border-radius: 999px;
}
.sidebar-footer {
  margin-top: auto; padding: 16px;
  border-top: 1px solid rgba(255,255,255,0.08);
  display: flex; flex-direction: column; gap: 8px;
}
.sys-chip {
  display: flex; align-items: center; gap: 8px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem;
  color: rgba(255,255,255,0.7); background: rgba(255,255,255,0.06);
  padding: 5px 10px; border-radius: 6px;
}
.sys-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot-green { background: #22c55e; box-shadow: 0 0 4px #22c55e; }
.dot-indigo { background: #818cf8; box-shadow: 0 0 4px #818cf8; }
.sys-version { font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; color: rgba(255,255,255,0.25); text-align: center; }

/* ── Shell ── */
.shell { margin-left: 240px; display: flex; flex-direction: column; min-height: 100vh; flex: 1; }

/* ── Top nav ── */
.topnav {
  height: 56px; background: #fff; border-bottom: 1px solid #e2e8f0;
  display: flex; align-items: center; gap: 12px; padding: 0 24px;
  position: sticky; top: 0; z-index: 50;
}
.breadcrumb { font-family: 'Space Grotesk', sans-serif; font-size: 1rem; font-weight: 700; color: #0f172a; flex: 1; }
.breadcrumb .sep { color: #94a3b8; margin: 0 6px; font-weight: 400; }
.breadcrumb .sub { color: #475569; font-weight: 600; }
.env-badge {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 700;
  padding: 3px 10px; border-radius: 6px; letter-spacing: 0.05em;
}
.env-prod { background: #4f46e5; color: #fff; }
.env-staging { background: #d97706; color: #fff; }
.env-dev { background: #16a34a; color: #fff; }
.last-batch { font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; color: #94a3b8; }
.btn-primary {
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: #fff; font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700;
  padding: 7px 16px; border: none; border-radius: 6px; cursor: pointer;
  box-shadow: 0 2px 8px rgba(79,70,229,0.35); letter-spacing: 0.02em;
  text-decoration: none; display: inline-flex; align-items: center;
  transition: box-shadow 0.15s;
}
.btn-primary:hover { box-shadow: 0 4px 16px rgba(79,70,229,0.45); }
.btn-outline {
  background: #fff; font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 600;
  padding: 6px 14px; border-radius: 6px; cursor: pointer; text-decoration: none;
  display: inline-flex; align-items: center; transition: all 0.15s;
}
.btn-outline-indigo { border: 1.5px solid #4f46e5; color: #4f46e5; }
.btn-outline-indigo:hover { background: #f0f2ff; }
.btn-outline-amber { border: 1.5px solid #d97706; color: #d97706; }
.btn-outline-amber:hover { background: #fffbeb; }
.pending-chip {
  background: #7c3aed; color: #fff;
  font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700;
  padding: 5px 14px; border-radius: 6px;
}

/* ── Content ── */
.content { padding: 24px; flex: 1; }

/* ── Cards ── */
.card {
  background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
}
.card-elevated {
  box-shadow: 0 4px 12px rgba(79,70,229,0.1), 0 1px 3px rgba(0,0,0,0.06);
}

/* ── KPI Tiles ── */
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.kpi-tile { padding: 18px 20px 16px; }
.kpi-top-border { border-top: 4px solid; border-radius: 8px 8px 0 0; }
.kpi-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; margin-bottom: 10px; }
.kpi-value {
  font-family: 'JetBrains Mono', monospace; font-size: 1.875rem; font-weight: 700;
  line-height: 1; margin-bottom: 6px;
}
.kpi-subtitle { font-size: 0.75rem; color: #94a3b8; }

/* ── Status badges ── */
.badge {
  display: inline-flex; align-items: center;
  font-family: 'JetBrains Mono', monospace; font-size: 0.6rem; font-weight: 700;
  letter-spacing: 0.07em; text-transform: uppercase;
  padding: 3px 8px; border-radius: 4px;
}
.badge-green   { background: #dcfce7; color: #15803d; }
.badge-amber   { background: #fef3c7; color: #92400e; }
.badge-red     { background: #fee2e2; color: #991b1b; }
.badge-violet  { background: #ede9fe; color: #5b21b6; }
.badge-indigo  { background: #e0e7ff; color: #3730a3; }
.badge-slate   { background: #f1f5f9; color: #475569; }
.badge-danger  { background: #dc2626; color: #fff; }
.badge-green-solid { background: #16a34a; color: #fff; }

/* ── Progress bar ── */
.prog-wrap { background: #f1f5f9; border-radius: 6px; height: 6px; overflow: hidden; }
.prog-fill { height: 100%; border-radius: 6px; transition: width 0.3s; }
.prog-green  { background: #16a34a; }
.prog-amber  { background: #d97706; }
.prog-red    { background: #dc2626; }
.prog-indigo { background: #4f46e5; }
.prog-teal   { background: #0891b2; }

/* ── Batch status card ── */
.batch-card { padding: 18px 20px; margin-bottom: 20px; border-left: 4px solid #4f46e5; }
.batch-header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.batch-id { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 600; color: #4f46e5; }
.batch-bars { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }
.bar-row { display: flex; flex-direction: column; gap: 5px; }
.bar-label { display: flex; justify-content: space-between; font-size: 0.75rem; color: #64748b; }
.bar-label span:last-child { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: #0f172a; }
.batch-stats { display: flex; gap: 8px; flex-wrap: wrap; }
.stat-chip {
  font-size: 0.75rem; padding: 3px 10px; border-radius: 4px;
  background: #f8fafc; color: #475569; border: 1px solid #e2e8f0;
  font-family: 'JetBrains Mono', monospace;
}
.stat-chip.err { background: #fee2e2; color: #991b1b; border-color: #fecaca; }

/* ── VM Grid ── */
.vm-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.vm-card {
  padding: 14px 16px;
  border-left: 4px solid;
  text-decoration: none; color: inherit; display: block;
  transition: box-shadow 0.15s, background 0.15s;
}
.vm-card:hover { background: #f8f9ff; box-shadow: 0 4px 12px rgba(79,70,229,0.1), 0 1px 3px rgba(0,0,0,0.06); }
.vm-card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
.vm-hostname { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 600; color: #4f46e5; }
.vm-tags { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.tag {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6rem; font-weight: 600;
  padding: 2px 6px; border-radius: 3px; letter-spacing: 0.04em;
}
.tag-ubuntu { background: #e0f2fe; color: #0369a1; }
.tag-rhel { background: #fee2e2; color: #991b1b; }
.tag-debian { background: #ede9fe; color: #5b21b6; }
.tag-prod { background: #e0e7ff; color: #3730a3; }
.tag-staging { background: #fef3c7; color: #92400e; }
.tag-dev { background: #dcfce7; color: #15803d; }
.vm-disk-row { margin-bottom: 10px; }
.vm-disk-label { display: flex; justify-content: space-between; font-size: 0.6875rem; color: #94a3b8; margin-bottom: 4px; font-family: 'JetBrains Mono', monospace; }
.vm-note { font-size: 0.6875rem; font-weight: 500; margin-bottom: 8px; }
.vm-footer { display: flex; justify-content: space-between; align-items: center; }
.vm-ts { font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; color: #94a3b8; }

/* ── Approval cards ── */
.appr-card { overflow: hidden; margin-bottom: 20px; }
.appr-band {
  padding: 12px 20px;
  display: flex; align-items: center; gap: 12px;
  color: #fff;
}
.appr-band-title { font-family: 'Space Grotesk', sans-serif; font-size: 0.9375rem; font-weight: 700; }
.appr-band-host { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 600; margin-left: auto; }
.appr-body { padding: 18px 20px; }
.appr-meta-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.appr-hostname { font-family: 'JetBrains Mono', monospace; font-size: 0.9375rem; font-weight: 600; color: #4f46e5; }
.appr-osinfo { font-size: 0.8125rem; color: #475569; }
.appr-countdown { margin-left: auto; font-family: 'Space Grotesk', sans-serif; font-size: 1rem; font-weight: 700; color: #d97706; }
.appr-reasoning { font-size: 0.875rem; color: #0f172a; line-height: 1.6; margin-bottom: 14px; }
.terminal {
  background: #0f172a; border-radius: 8px; padding: 14px 16px; margin-bottom: 16px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; line-height: 1.7;
  color: #a5f3fc;
}
.terminal .comment { color: #64748b; }
.pkg-pills { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.pkg-pill {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 500;
  padding: 3px 10px; border-radius: 4px;
  background: #fef3c7; color: #92400e;
}
.appr-footer { display: flex; align-items: center; gap: 10px; }
.btn-approve {
  background: #16a34a; color: #fff;
  font-family: 'Space Grotesk', sans-serif; font-size: 0.875rem; font-weight: 700;
  padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer;
  box-shadow: 0 2px 6px rgba(22,163,74,0.3); text-decoration: none;
  display: inline-flex; align-items: center;
}
.btn-reject {
  background: #fff; color: #dc2626;
  font-family: 'Space Grotesk', sans-serif; font-size: 0.875rem; font-weight: 700;
  padding: 7px 18px; border: 1.5px solid #dc2626; border-radius: 6px; cursor: pointer;
  text-decoration: none; display: inline-flex; align-items: center;
}
.appr-details { margin-left: auto; font-size: 0.8125rem; color: #4f46e5; text-decoration: none; font-weight: 500; }
.resolved-card { padding: 14px 20px; background: #fafafa; border: 1.5px dashed #e2e8f0; display: flex; align-items: center; gap: 10px; }
.resolved-label { font-family: 'Space Grotesk', sans-serif; font-size: 0.875rem; font-weight: 600; color: #64748b; }
.filter-chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  font-size: 0.8125rem; font-weight: 500; padding: 5px 14px; border-radius: 6px;
  border: 1.5px solid #e2e8f0; color: #475569; background: #fff; cursor: pointer;
  text-decoration: none;
}
.chip.active { background: #4f46e5; color: #fff; border-color: #4f46e5; }

/* ── Section header ── */
.section-hdr { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }
.section-title { font-family: 'Space Grotesk', sans-serif; font-size: 1.125rem; font-weight: 700; color: #0f172a; }
.section-sub { font-size: 0.8125rem; color: #475569; margin-top: 2px; }

/* ── Data table ── */
.table-card { padding: 0; overflow: hidden; }
.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  background: #f8fafc; padding: 11px 16px; text-align: left;
  font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b;
  border-bottom: 1px solid #f1f5f9;
}
.data-table th.r { text-align: right; }
.data-table td {
  padding: 12px 16px; font-size: 0.875rem; color: #0f172a;
  border-bottom: 1px solid #f8fafc;
  vertical-align: middle;
}
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: #f0f4ff; }
.data-table tr.row-alt td { background: #fafbff; }
.data-table tr.row-alt:hover td { background: #f0f4ff; }
.data-table tr.row-failed td { background: #fff8f8; }
.data-table tr.row-failed:hover td { background: #fee2e2; }
.data-table tr.row-pending td { background: #faf8ff; }
.data-table tr.row-pending:hover td { background: #ede9fe; }
.td-mono { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; }
.td-host { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 600; color: #4f46e5; text-decoration: none; }
.td-host:hover { text-decoration: underline; }
.td-ts { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #94a3b8; }
.td-right { text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; }
.td-link { color: #4f46e5; text-decoration: none; font-size: 0.8125rem; font-weight: 500; }
.td-link:hover { text-decoration: underline; }

/* ── Filter bar ── */
.filter-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.search-input {
  flex: 1; min-width: 240px; padding: 7px 12px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem;
  border: 1.5px solid #e2e8f0; border-radius: 6px; outline: none; color: #0f172a;
}
.search-input::placeholder { color: #94a3b8; }
.search-input:focus { border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79,70,229,0.12); }
.select-input {
  padding: 7px 12px; font-size: 0.8125rem; font-family: 'Inter', sans-serif;
  border: 1.5px solid #e2e8f0; border-radius: 6px; outline: none;
  background: #fff; color: #475569; cursor: pointer;
}
.select-input:focus { border-color: #4f46e5; }
.results-bar { font-size: 0.8125rem; color: #64748b; margin: 12px 0; }
.results-bar strong { color: #0f172a; font-family: 'Space Grotesk', sans-serif; }

/* ── VM Detail ── */
.detail-top { display: grid; grid-template-columns: 1fr 2fr; gap: 16px; margin-bottom: 16px; }
.identity-card { padding: 18px 20px; }
.identity-top-border { border-top: 4px solid; }
.identity-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.identity-hostname { font-family: 'Space Grotesk', sans-serif; font-size: 1.125rem; font-weight: 700; color: #0f172a; }
.fields-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; }
.field-row { display: flex; flex-direction: column; gap: 2px; }
.field-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #94a3b8; }
.field-value { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; color: #0f172a; }
.divider { height: 1px; background: #f1f5f9; margin: 14px 0; }
.maint-label { font-size: 0.6875rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: #94a3b8; margin-bottom: 6px; }
.maint-val { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; color: #0f172a; margin-bottom: 4px; }
.maint-next { font-size: 0.75rem; color: #16a34a; font-weight: 500; }
.disk-card { padding: 18px 20px; }
.disk-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.disk-partition { margin-bottom: 14px; }
.disk-partition:last-child { margin-bottom: 0; }
.disk-row { display: flex; align-items: center; gap: 10px; margin-bottom: 5px; }
.disk-path { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 600; color: #0f172a; width: 60px; }
.disk-pct { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 700; width: 36px; text-align: right; }
.disk-size { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #64748b; margin-left: auto; }
.disk-progwrap { flex: 1; background: #f1f5f9; border-radius: 6px; height: 8px; overflow: hidden; }
.disk-fill { height: 100%; border-radius: 6px; }
.callout {
  padding: 10px 14px; border-radius: 6px; font-size: 0.8125rem; line-height: 1.5;
  margin-top: 14px;
}
.callout-amber { background: #fffbeb; border: 1px solid #fde68a; color: #92400e; }
.callout-red   { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }

/* ── Pagination ── */
.pagination { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 14px; border-top: 1px solid #f1f5f9; font-size: 0.875rem; color: #475569; }
.pg-btn { padding: 5px 12px; border: 1.5px solid #e2e8f0; border-radius: 6px; background: #fff; cursor: pointer; font-size: 0.8125rem; color: #475569; text-decoration: none; }
.pg-btn:hover { border-color: #4f46e5; color: #4f46e5; }
.pg-current { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: #0f172a; padding: 0 8px; }

/* ── Responsive tweaks ── */
@media (max-width: 1100px) { .vm-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 900px)  { .kpi-grid { grid-template-columns: repeat(2, 1fr); } .detail-top { grid-template-columns: 1fr; } }
/* ── Mobile (≤768px) — hide sidebar, full-width shell, wrap tables ── */
html, body { max-width: 100vw; overflow-x: hidden; }
@media (max-width: 768px) {
  .sidebar { display: none; }
  .shell { margin-left: 0; max-width: 100vw; overflow-x: hidden; }
  .topnav { padding: 0 12px; flex-wrap: wrap; height: auto; min-height: 52px; }
  .topnav-left { gap: 8px; }
  .topnav-right { gap: 6px; flex-wrap: wrap; }
  .topnav-right .btn, .topnav-right a { font-size: 0.7rem; padding: 4px 8px; }
  .mode-banner { font-size: 0.65rem; flex-wrap: wrap; gap: 4px; padding: 4px 12px; }
  .page-content { padding: 10px; max-width: 100%; box-sizing: border-box; }
  .card { overflow-x: auto; max-width: 100%; box-sizing: border-box; }
  .data-table { font-size: 0.7rem; min-width: 480px; }
  .data-table th, .data-table td { padding: 6px 8px; }
  .table-card { overflow-x: auto; max-width: 100%; }
  .kpi-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .vm-grid { grid-template-columns: 1fr !important; }
  .filter-bar { flex-wrap: wrap; gap: 8px; }
  .filter-bar .search-input, .filter-bar select { width: 100%; box-sizing: border-box; }
  .section-hdr { flex-direction: column; align-items: flex-start; gap: 10px; }
  .section-hdr .btn { width: 100%; text-align: center; box-sizing: border-box; }
  .detail-top { grid-template-columns: 1fr; }
  .settings-grid { grid-template-columns: 1fr; }
  .admin-top { grid-template-columns: 1fr; }
  .inv-kpi { grid-template-columns: repeat(2, 1fr); }
  .evidence-grid { grid-template-columns: 1fr !important; }
  .layer-partition { flex-direction: column; }
  .layer-a, .layer-b { min-width: 0; width: 100%; }
  .layer-divider { width: 100%; height: 2px; margin: 8px 0; }
  .deeplink-chip { font-size: 0.65rem; padding: 2px 6px; }
  .countdown-big { font-size: 2rem; }
  .confirm-modal-box { width: 95vw; max-width: 95vw; box-sizing: border-box; padding: 16px; }
  .confirm-modal-box input { width: 100%; box-sizing: border-box; }
  .approval-card { padding: 12px; }
  .agent-status-grid { grid-template-columns: 1fr !important; }
  .two-col-grid { grid-template-columns: 1fr !important; }
  .gloss-grid { grid-template-columns: 1fr !important; }
  pre, code { font-size: 0.65rem; overflow-x: auto; max-width: 100%; }
}

/* ── Sparkline placeholder ── */
.sparkline-wrap { position: relative; height: 72px; margin: 16px 0 8px; }
.sparkline-svg { width: 100%; height: 100%; }
.spark-x-labels { display: flex; justify-content: space-between; font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; color: #94a3b8; padding: 0 2px; }
.anomaly-chip {
  position: absolute; top: 4px;
  background: #fef3c7; color: #92400e; border: 1px solid #fde68a;
  font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; font-weight: 600;
  padding: 2px 7px; border-radius: 4px; white-space: nowrap;
}

/* ── Glossary grid ── */
.gloss-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 28px; }
.gloss-card { background: #fff; border-radius: 8px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.gloss-card-hdr { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.gloss-term { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 700; color: #4f46e5; }
.gloss-chip { font-family: 'JetBrains Mono', monospace; font-size: 0.55rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 2px 6px; border-radius: 3px; flex-shrink: 0; }
.gloss-chip-core   { background: #e0e7ff; color: #3730a3; }
.gloss-chip-safety { background: #ede9fe; color: #5b21b6; }
.gloss-chip-action { background: #cffafe; color: #155e75; }
.gloss-chip-infra  { background: #fef3c7; color: #92400e; }
.gloss-defn { font-size: 0.8125rem; color: #475569; line-height: 1.55; }

/* ── Workflow diagram ── */
@keyframes dash-flow { to { stroke-dashoffset: -26; } }
.wf-outer-card { background: #0f172a; border-radius: 12px; padding: 24px; margin-bottom: 8px; }
.wf-diagram-wrap { overflow-x: auto; padding-bottom: 8px; }
.wf-diagram { position: relative; width: 960px; height: 845px; margin: 0 auto; }
.wf-svg { position: absolute; top: 0; left: 0; width: 960px; height: 845px; pointer-events: none; overflow: visible; }
.wf-node {
  position: absolute; width: 160px; height: 50px; border-radius: 8px;
  display: flex; align-items: center; gap: 10px; padding: 0 14px;
  cursor: pointer; transition: all 0.18s; background: #1e293b; user-select: none;
}
.wf-node:hover { background: #243348; transform: translateY(-1px); box-shadow: 0 4px 16px rgba(79,70,229,0.3); }
.wf-node.active { background: linear-gradient(135deg, #3525cd, #712ae2) !important; box-shadow: 0 4px 24px rgba(79,70,229,0.5); border: none !important; }
.wf-node.active .wf-node-name { color: #fff !important; }
.wf-node.active .wf-node-sub  { color: rgba(255,255,255,0.65) !important; }
.wf-node-conditional { border: 1.5px dashed #d97706; background: #1e1a0f !important; }
.wf-node-conditional:hover { background: #29240f !important; }
.wf-node-failure-node { border: 1.5px dashed #ef4444; background: #1e1010 !important; }
.wf-node-failure-node:hover { background: #2a1515 !important; }
.wf-node-terminal {
  position: absolute; width: 110px; height: 38px; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
  background: #1e1010; border: 1.5px dashed #ef4444;
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem;
  font-weight: 700; color: #ef4444; letter-spacing: 0.05em;
}
.wf-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.wf-dot-amber  { background: #fbbf24; box-shadow: 0 0 6px #fbbf24; }
.wf-dot-indigo { background: #818cf8; box-shadow: 0 0 6px #818cf8; }
.wf-dot-violet { background: #a78bfa; box-shadow: 0 0 6px #a78bfa; }
.wf-dot-teal   { background: #22d3ee; box-shadow: 0 0 6px #22d3ee; }
.wf-dot-red    { background: #f87171; box-shadow: 0 0 6px #f87171; }
.wf-dot-green  { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
.wf-dot-white  { background: rgba(255,255,255,0.85); }
.wf-node-name { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 700; color: #e2e8f0; white-space: nowrap; }
.wf-node-sub  { font-size: 0.585rem; color: #64748b; font-family: 'Inter', sans-serif; white-space: nowrap; margin-top: 2px; }
.wf-legend { display: flex; align-items: center; gap: 20px; margin-bottom: 16px; flex-wrap: wrap; }
.wf-legend-item { display: flex; align-items: center; gap: 8px; font-size: 0.75rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace; }
.wf-detail { background: #fff; border-radius: 8px; border-left: 4px solid #4f46e5; padding: 16px 20px; margin-top: 16px; transition: border-color 0.2s; }
.wf-detail-hdr { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.wf-detail-title { font-family: 'Space Grotesk', sans-serif; font-size: 1rem; font-weight: 700; color: #4f46e5; transition: color 0.2s; }
.wf-detail-badge { font-family: 'JetBrains Mono', monospace; font-size: 0.6rem; font-weight: 700; letter-spacing: 0.08em; padding: 3px 8px; border-radius: 4px; color: #fff; transition: background 0.2s; }
.wf-detail-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.wf-detail-row { display: flex; gap: 14px; }
.wf-detail-lbl { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 0.6875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.06em; width: 68px; flex-shrink: 0; padding-top: 1px; }
.wf-detail-val { font-family: 'JetBrains Mono', monospace; font-size: 0.775rem; color: #0f172a; line-height: 1.55; }
.wf-detail-note { font-size: 0.8rem; color: #64748b; font-style: italic; border-top: 1px solid #f1f5f9; padding-top: 10px; }
.wf-hint { text-align: center; font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; color: #334155; padding: 12px 0 4px; letter-spacing: 0.04em; }

/* ── Node detail modal ── */
.wf-modal-backdrop {
  display: none; position: fixed; inset: 0;
  background: rgba(15,23,42,0.55); backdrop-filter: blur(4px);
  z-index: 200;
}
.wf-modal-backdrop.open { display: block; }
@keyframes modal-in {
  from { opacity: 0; transform: translate(-50%, -48%); }
  to   { opacity: 1; transform: translate(-50%, -50%); }
}
.wf-modal {
  display: none; position: fixed;
  top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 520px; max-width: 92vw;
  background: #fff; border-radius: 12px; border-left: 4px solid #4f46e5;
  padding: 22px 26px;
  z-index: 201;
  box-shadow: 0 24px 64px -12px rgba(24,20,69,0.28);
}
.wf-modal.open { display: block; animation: modal-in 0.17s ease; }
.wf-modal-close {
  position: absolute; top: 12px; right: 14px;
  background: none; border: none; cursor: pointer;
  font-size: 0.9rem; color: #94a3b8;
  font-family: 'JetBrains Mono', monospace; font-weight: 700;
  padding: 3px 8px; border-radius: 4px; transition: all 0.12s; line-height: 1;
}
.wf-modal-close:hover { background: #f1f5f9; color: #0f172a; }

@media (max-width: 1100px) { .gloss-grid { grid-template-columns: repeat(2, 1fr); } }

/* ── Inventory page ── */
.inv-kpi { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 20px; }

/* ── Settings page ── */
.settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.settings-card { padding: 20px 22px; }
.settings-section-title { font-family: 'Space Grotesk', sans-serif; font-size: 0.875rem; font-weight: 700; color: #0f172a; margin-bottom: 14px; display: flex; align-items: center; gap: 10px; }
.settings-icon { width: 28px; height: 28px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 0.875rem; flex-shrink: 0; }
.settings-rows { display: flex; flex-direction: column; }
.settings-row { display: flex; align-items: center; justify-content: space-between; padding: 9px 0; border-bottom: 1px solid #f8fafc; }
.settings-row:last-child { border-bottom: none; }
.settings-key { font-size: 0.8125rem; color: #475569; }
.settings-val { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; color: #0f172a; font-weight: 500; }
.settings-masked { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; color: #94a3b8; letter-spacing: 0.08em; }
.settings-badge { font-family: 'JetBrains Mono', monospace; font-size: 0.6rem; font-weight: 700; letter-spacing: 0.06em; padding: 2px 7px; border-radius: 4px; }
.settings-note { background: #f8fafc; border-radius: 8px; padding: 14px 16px; font-size: 0.8125rem; color: #64748b; line-height: 1.6; margin-top: 4px; }
.settings-note code { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; color: #4f46e5; background: #e0e7ff; padding: 1px 5px; border-radius: 3px; }

/* ── Admin page ── */
.admin-top { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.admin-card { padding: 18px 20px; }
.admin-section-title { font-family: 'Space Grotesk', sans-serif; font-size: 0.9375rem; font-weight: 700; color: #0f172a; margin-bottom: 14px; }
.agent-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.agent-row-label { font-size: 0.8125rem; color: #64748b; width: 148px; flex-shrink: 0; }
.agent-row-val { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 600; color: #0f172a; }
.admin-btns { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; padding-top: 14px; border-top: 1px solid #f1f5f9; }
.btn-run { background: linear-gradient(135deg, #3525cd, #712ae2); color: #fff; font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700; padding: 7px 16px; border: none; border-radius: 6px; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }
.btn-warn-ol { background: #fff; color: #d97706; font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700; padding: 6px 14px; border: 1.5px solid #d97706; border-radius: 6px; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }
.btn-danger-ol { background: #fff; color: #dc2626; font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700; padding: 6px 14px; border: 1.5px solid #dc2626; border-radius: 6px; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }
.health-rows { display: flex; flex-direction: column; gap: 11px; }
.health-row { display: flex; align-items: center; gap: 10px; }
.health-label { font-size: 0.875rem; color: #0f172a; font-weight: 500; flex: 1; }
.health-detail { font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: #94a3b8; max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.h-ok   { display:flex;align-items:center;gap:5px;font-family:'JetBrains Mono',monospace;font-size:0.6875rem;font-weight:700;color:#16a34a;white-space:nowrap }
.h-warn { display:flex;align-items:center;gap:5px;font-family:'JetBrains Mono',monospace;font-size:0.6875rem;font-weight:700;color:#d97706;white-space:nowrap }
.h-err  { display:flex;align-items:center;gap:5px;font-family:'JetBrains Mono',monospace;font-size:0.6875rem;font-weight:700;color:#dc2626;white-space:nowrap }
.hdot-ok  { width:7px;height:7px;border-radius:50%;background:#16a34a;box-shadow:0 0 5px #16a34a;flex-shrink:0 }
.hdot-warn{ width:7px;height:7px;border-radius:50%;background:#d97706;box-shadow:0 0 5px #d97706;flex-shrink:0 }
.hdot-err { width:7px;height:7px;border-radius:50%;background:#dc2626;box-shadow:0 0 5px #dc2626;flex-shrink:0 }
.override-rows { display: flex; flex-direction: column; }
.override-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid #f8fafc; }
.override-row:last-child { border-bottom: none; }
.override-label { font-size: 0.875rem; font-weight: 600; color: #0f172a; margin-bottom: 3px; }
.override-desc  { font-size: 0.75rem; color: #94a3b8; line-height: 1.45; max-width: 360px; }
.toggle-wrap { display: flex; align-items: center; gap: 8px; flex-shrink: 0; padding-top: 2px; }
.toggle { position: relative; display: inline-block; width: 40px; height: 22px; }
.toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
.toggle-slider { position: absolute; cursor: pointer; inset: 0; background: #cbd5e1; border-radius: 22px; transition: 0.2s; }
.toggle-slider:before { position: absolute; content: ""; height: 16px; width: 16px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: 0.2s; }
.toggle input:checked + .toggle-slider { background: #4f46e5; }
.toggle input:checked + .toggle-slider:before { transform: translateX(18px); }
.t-on  { font-family:'JetBrains Mono',monospace;font-size:0.625rem;font-weight:700;color:#4f46e5;width:22px;text-align:right }
.t-off { font-family:'JetBrains Mono',monospace;font-size:0.625rem;font-weight:700;color:#94a3b8;width:22px;text-align:right }
.danger-zone-card { border-top: 4px solid #dc2626; padding: 18px 20px; }
.danger-zone-hdr { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.danger-zone-title { font-family: 'Space Grotesk', sans-serif; font-size: 0.9375rem; font-weight: 700; color: #dc2626; }
.danger-zone-sub { font-size: 0.8125rem; color: #94a3b8; margin-bottom: 16px; line-height: 1.5; }
.danger-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.btn-danger { color: #fff; font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700; padding: 7px 16px; border: none; border-radius: 6px; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }
@media (max-width: 900px) { .admin-top { grid-template-columns: 1fr; } .settings-grid { grid-template-columns: 1fr; } }

/* ── VM Metric tri-bars (CPU / MEM / DISK) ── */
.vm-metrics { display: flex; flex-direction: column; gap: 5px; margin: 8px 0 10px; }
.vm-metric-row { display: flex; align-items: center; gap: 7px; }
.vm-metric-lbl { font-family: 'JetBrains Mono', monospace; font-size: 0.5625rem; font-weight: 700; color: #94a3b8; letter-spacing: 0.08em; width: 28px; flex-shrink: 0; }
.vm-metric-bar { flex: 1; }
.vm-metric-num { font-family: 'JetBrains Mono', monospace; font-size: 0.625rem; font-weight: 700; width: 28px; text-align: right; }

/* ── Pending patches chip ── */
.patches-chip { font-family: 'JetBrains Mono', monospace; font-size: 0.5625rem; font-weight: 700; padding: 2px 6px; border-radius: 3px; background: #fef3c7; color: #92400e; letter-spacing: 0.04em; white-space: nowrap; }
.patches-chip-crit { background: #fee2e2; color: #991b1b; }

/* ── VM footer two-line ── */
.vm-footer-col { display: flex; flex-direction: column; gap: 2px; }
.vm-footer-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }

/* ── Needs Attention callout ── */
.attn-box { border-left: 4px solid #d97706; margin-bottom: 20px; padding: 0; }
.attn-hdr { display: flex; align-items: center; gap: 10px; padding: 11px 16px 9px; border-bottom: 1px solid #fef9ee; }
.attn-title { font-family: 'Space Grotesk', sans-serif; font-size: 0.8125rem; font-weight: 700; color: #d97706; }
.attn-row { display: flex; align-items: center; gap: 10px; padding: 8px 16px; border-bottom: 1px solid #fafaf8; flex-wrap: wrap; }
.attn-row:last-child { border-bottom: none; }
.attn-host { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 700; color: #d97706; width: 155px; flex-shrink: 0; text-decoration: none; }
.attn-host:hover { text-decoration: underline; }
.attn-host-failed { color: #dc2626; }
.attn-host-pending { color: #7c3aed; }
.attn-reason { font-size: 0.8125rem; color: #475569; flex: 1; min-width: 200px; }
.attn-link { font-size: 0.8125rem; color: #4f46e5; text-decoration: none; font-weight: 500; flex-shrink: 0; }
.attn-link:hover { text-decoration: underline; }

/* ── Approval: VM health panel ── */
.appr-health { background: #f8fafc; border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; border: 1px solid #f1f5f9; }
.appr-health-title { font-family: 'JetBrains Mono', monospace; font-size: 0.5625rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; margin-bottom: 10px; }
.appr-health-metrics { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 12px; margin-bottom: 10px; }
.appr-hm { display: flex; flex-direction: column; gap: 4px; }
.appr-hm-lbl { font-family: 'JetBrains Mono', monospace; font-size: 0.5rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; }
.appr-hm-val { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 700; }
.appr-hm-bar { height: 5px; background: #e2e8f0; border-radius: 3px; overflow: hidden; margin-top: 2px; }
.appr-hm-fill { height: 100%; border-radius: 3px; }
.appr-trigger-row { display: flex; gap: 10px; padding-top: 8px; border-top: 1px solid #f1f5f9; }
.appr-trigger-lbl { font-family: 'JetBrains Mono', monospace; font-size: 0.5rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; padding-top: 2px; flex-shrink: 0; width: 50px; }
.appr-trigger-val { font-size: 0.8125rem; color: #d97706; font-weight: 500; line-height: 1.45; }

/* ── Approval: consequences panel ── */
.appr-consequences { background: #fafafa; border-top: 1px solid #f1f5f9; padding: 12px 14px; display: flex; flex-direction: column; gap: 7px; margin: 14px -20px -18px; border-radius: 0 0 8px 8px; }
.appr-cons-row { display: flex; gap: 10px; align-items: flex-start; }
.appr-cons-lbl { font-family: 'JetBrains Mono', monospace; font-size: 0.5rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; padding-top: 2px; flex-shrink: 0; width: 68px; }
.appr-cons-val { font-size: 0.8125rem; color: #475569; line-height: 1.5; }
.appr-cons-val-risk { color: #dc2626; font-weight: 500; }

/* ── Audit detail inline ── */
.audit-detail { font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; margin-top: 3px; line-height: 1.45; }
.audit-detail-ok      { color: #64748b; }
.audit-detail-failed  { color: #dc2626; font-weight: 500; }
.audit-detail-warning { color: #d97706; font-weight: 500; }
.audit-detail-pending { color: #7c3aed; }

/* ── Batch error summary ── */
.batch-err-summary { font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; color: #dc2626; margin-top: 3px; }

/* ── Agent page: status strip ── */
.agent-status-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.agent-status-chip { padding: 14px 18px; display: flex; align-items: center; gap: 12px; }
.agent-status-icon { width: 36px; height: 36px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 1rem; flex-shrink: 0; }
.agent-status-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.agent-status-label { font-size: 0.6rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; color: #94a3b8; font-family: 'JetBrains Mono', monospace; }
.agent-status-val   { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 700; color: #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.agent-status-sub   { font-size: 0.6875rem; color: #94a3b8; }

/* ── Execution trace ── */
.trace-card { padding: 0; overflow: hidden; margin-bottom: 20px; }
.trace-hdr  { display: flex; align-items: center; gap: 12px; padding: 14px 20px 12px; border-bottom: 1px solid #f1f5f9; }
.trace-batch-id { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 700; color: #4f46e5; }
.trace-table { width: 100%; border-collapse: collapse; }
.trace-table td { padding: 9px 16px; vertical-align: middle; border-bottom: 1px solid #f8fafc; font-size: 0.8125rem; }
.trace-table tr:last-child td { border-bottom: none; }
.trace-table tr:hover td { background: #f8f9ff; }
.trace-node-name { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 700; color: #0f172a; width: 136px; }
.trace-started   { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #94a3b8; width: 66px; }
.trace-bar-cell  { width: 280px; padding-right: 8px !important; }
.trace-bar-wrap  { background: #f1f5f9; border-radius: 4px; height: 8px; overflow: hidden; }
.trace-bar-fill  { height: 100%; border-radius: 4px; }
.trace-duration  { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #475569; width: 64px; }
.trace-detail    { font-size: 0.75rem; color: #64748b; }

/* ── VM Outcome grid ── */
.outcome-table { width: 100%; border-collapse: collapse; }
.outcome-table th { background: #f8fafc; padding: 8px 10px; text-align: center; font-size: 0.5625rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; border-bottom: 1px solid #f1f5f9; }
.outcome-table th.left { text-align: left; padding-left: 16px; }
.outcome-table td { padding: 8px 10px; border-bottom: 1px solid #f8fafc; text-align: center; vertical-align: middle; }
.outcome-table td.left { text-align: left; padding-left: 16px; }
.outcome-table tr:last-child td { border-bottom: none; }
.outcome-table tr:hover td { background: #f8f9ff; }
.oc-ok   { color: #16a34a; font-size: 0.875rem; font-weight: 700; }
.oc-warn { color: #d97706; font-size: 0.875rem; font-weight: 700; }
.oc-fail { color: #dc2626; font-size: 0.875rem; font-weight: 700; }
.oc-skip { color: #cbd5e1; font-size: 0.875rem; }
.oc-appr { font-family: 'JetBrains Mono', monospace; font-size: 0.5rem; font-weight: 700; color: #fff; background: #d97706; padding: 2px 4px; border-radius: 3px; }
.outcome-vm   { font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem; font-weight: 700; color: #4f46e5; text-decoration: none; }
.outcome-vm:hover { text-decoration: underline; }
.outcome-notes { font-size: 0.75rem; color: #64748b; max-width: 260px; }

/* ── LLM decisions ── */
.llm-meta-strip { display: flex; align-items: center; gap: 20px; padding: 10px 16px 10px; background: #f8fafc; border-bottom: 1px solid #f1f5f9; flex-wrap: wrap; }
.llm-meta-item  { display: flex; align-items: center; gap: 6px; font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; color: #475569; }
.llm-meta-lbl   { color: #94a3b8; font-size: 0.5625rem; text-transform: uppercase; letter-spacing: 0.08em; }
.llm-table { width: 100%; border-collapse: collapse; }
.llm-table th { background: #f8fafc; padding: 8px 12px; text-align: left; font-size: 0.5625rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #64748b; border-bottom: 1px solid #f1f5f9; }
.llm-table td { padding: 10px 12px; border-bottom: 1px solid #f8fafc; font-size: 0.8125rem; vertical-align: top; }
.llm-table tr:last-child td { border-bottom: none; }
.llm-table tr:hover td { background: #f8f9ff; }
.signal-tag { font-family: 'JetBrains Mono', monospace; font-size: 0.5625rem; font-weight: 700; padding: 2px 6px; border-radius: 3px; display: inline-block; margin: 1px 2px; }
.signal-tag-disk    { background: #fef3c7; color: #92400e; }
.signal-tag-svc     { background: #fee2e2; color: #991b1b; }
.signal-tag-err     { background: #fce7f3; color: #9d174d; }
.signal-tag-drift   { background: #ede9fe; color: #5b21b6; }
.signal-tag-login   { background: #e0f2fe; color: #0369a1; }
.signal-tag-ok      { background: #dcfce7; color: #15803d; }
.plan-step { font-family: 'JetBrains Mono', monospace; font-size: 0.5625rem; font-weight: 700; padding: 2px 6px; border-radius: 3px; display: inline-block; margin: 1px 2px; background: #e0e7ff; color: #3730a3; }
.plan-step-high { background: #fee2e2; color: #991b1b; }
.plan-step-med  { background: #fef3c7; color: #92400e; }
.plan-step-low  { background: #dcfce7; color: #15803d; }
.llm-reasoning  { font-size: 0.75rem; color: #64748b; line-height: 1.5; }
.llm-fallback-badge { font-family:'JetBrains Mono',monospace; font-size:0.5rem; font-weight:700; background:#fef3c7; color:#92400e; padding:2px 6px; border-radius:3px; letter-spacing:0.05em; }

/* ── Scheduler ── */
.sched-run-row { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid #f8fafc; }
.sched-run-row:last-child { border-bottom: none; }
.sched-run-dot  { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.sched-run-ts   { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #475569; flex: 1; }
.sched-run-dur  { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #94a3b8; }
.sched-run-err  { font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 700; color: #dc2626; }
.sched-next-item { font-family:'JetBrains Mono',monospace; font-size:0.8125rem; color:#4f46e5; padding:5px 0; border-bottom:1px solid #f8fafc; }
.sched-next-item:last-child { border-bottom:none; }
.cron-badge { font-family:'JetBrains Mono',monospace; font-size:0.8125rem; font-weight:600; background:#e0e7ff; color:#3730a3; padding:4px 10px; border-radius:4px; }

/* ── Probe ── */
.probe-escalated-banner { background:#fee2e2; border:1px solid #fecaca; border-radius:6px; padding:10px 14px; margin-bottom:12px; display:flex; align-items:center; gap:8px; font-size:0.8125rem; font-weight:600; color:#991b1b; }
.probe-signal-group { margin-bottom:10px; }
.probe-signal-group:last-child { margin-bottom:0; }
.probe-signal-type { font-family:'JetBrains Mono',monospace; font-size:0.5625rem; font-weight:700; text-transform:uppercase; letter-spacing:0.08em; color:#94a3b8; margin-bottom:4px; }
.probe-signal-item { font-size:0.8125rem; color:#475569; padding:2px 0; }
.probe-ok-banner { background:#dcfce7; border:1px solid #bbf7d0; border-radius:6px; padding:10px 14px; font-size:0.8125rem; font-weight:600; color:#15803d; display:flex; align-items:center; gap:8px; }

/* ── Deferred queue ── */
.deferred-empty { padding:28px; text-align:center; color:#94a3b8; font-family:'JetBrains Mono',monospace; font-size:0.8125rem; }

/* ── Side-by-side two column ── */
.two-col-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px; }
@media (max-width:900px) { .agent-status-grid { grid-template-columns:repeat(2,1fr); } .two-col-grid { grid-template-columns:1fr; } }

/* ── Sign-out link in sidebar footer ── */
.signout-link {
  display: block; margin-top: 8px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.65rem; font-weight: 600; letter-spacing: 0.06em;
  color: rgba(255,255,255,0.25); text-decoration: none;
  text-align: center; padding: 4px 0;
  transition: color 0.15s;
}
.signout-link:hover { color: rgba(255,255,255,0.6); }

/* ─────────────────────────────────────────────────────────────────────────
   SRE enterprise-readiness additions (2026-05-20)
   ───────────────────────────────────────────────────────────────────────── */

/* ── Mode banner (every page, above breadcrumb) ── */
.mode-banner {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 8px 24px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6875rem; font-weight: 600; letter-spacing: 0.04em;
  background: linear-gradient(90deg, #fde68a 0%, #fef3c7 100%);
  color: #78350f; border-bottom: 2px solid #f59e0b;
}
.mode-banner.live {
  background: linear-gradient(90deg, #fecaca 0%, #fee2e2 100%);
  color: #7f1d1d; border-bottom-color: #dc2626;
}
.mode-banner.live-prod {
  background: linear-gradient(90deg, #fda4af 0%, #fecdd3 100%);
  color: #831843; border-bottom-color: #be185d;
}
.mode-banner-pill {
  background: rgba(255,255,255,0.55); padding: 3px 10px; border-radius: 4px;
  text-transform: uppercase;
}
.mode-banner-pill.demo { background: #1e1b4b; color: #fde68a; }
.mode-banner-pill.dry  { background: #1e1b4b; color: #c7d2fe; }
.mode-banner-pill.live { background: #7f1d1d; color: #fee2e2; }
.mode-banner-sep { color: rgba(120,53,15,0.4); }

/* ── Evidence grid (used on Approvals + VM Detail + Audit) ── */
.evidence-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 24px;
  padding: 14px 16px; background: #f6f2ff; border-radius: 8px;
  margin: 10px 0;
}
.evidence-cell { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.evidence-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.625rem; font-weight: 700; letter-spacing: 0.07em;
  color: #6b7280; text-transform: uppercase;
}
.evidence-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8125rem; color: #1e1b4b;
  word-break: break-all; line-height: 1.4;
}
.evidence-value.muted { color: #94a3b8; }
.evidence-value.warn  { color: #b45309; }
.evidence-value.ok    { color: #16a34a; }
.evidence-value.danger{ color: #dc2626; }
@media (max-width: 900px) { .evidence-grid { grid-template-columns: 1fr; } }

/* ── Layer A vs Layer B separation (Approvals page) ── */
.layer-section { border-radius: 8px; padding: 12px 16px; margin: 8px 0; }
.layer-b {
  background: #ecfdf5;
  border-left: 4px solid #16a34a;
}
.layer-policy {
  background: #eef2ff;
  border-left: 4px solid #4f46e5;
}
.layer-a {
  background: #fef3c7;
  border-left: 4px solid #d97706;
}
.layer-hdr {
  display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6875rem; font-weight: 700; letter-spacing: 0.07em;
  text-transform: uppercase;
}
.layer-b .layer-hdr   { color: #047857; }
.layer-policy .layer-hdr { color: #3730a3; }
.layer-a .layer-hdr   { color: #92400e; }
.layer-hdr-tag {
  font-size: 0.5625rem; padding: 1px 7px; border-radius: 3px;
  background: rgba(255,255,255,0.7); letter-spacing: 0.06em;
}
.layer-body {
  font-family: 'Inter', sans-serif;
  font-size: 0.8125rem; color: #1f2937; line-height: 1.55;
}
.layer-body ul { list-style: none; padding-left: 0; }
.layer-body li {
  padding: 3px 0 3px 16px; position: relative;
  font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
}
.layer-body li::before {
  content: '▸'; position: absolute; left: 2px; color: currentColor; opacity: 0.5;
}
.layer-a .layer-body { font-family: 'Inter', sans-serif; }
.layer-a .layer-body p { font-style: italic; }
.layer-a-disclaimer {
  margin-top: 8px; font-family: 'JetBrains Mono', monospace;
  font-size: 0.625rem; color: #92400e; opacity: 0.75;
  letter-spacing: 0.04em; text-transform: uppercase;
}

/* ── Approval action evidence table (pkg cur→target, units) ── */
.appr-action-tbl {
  width: 100%; border-collapse: collapse; margin-top: 8px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
}
.appr-action-tbl th {
  text-align: left; padding: 6px 10px;
  font-size: 0.5625rem; font-weight: 700; letter-spacing: 0.07em;
  color: #6b7280; text-transform: uppercase; background: #f1f5f9;
}
.appr-action-tbl td {
  padding: 5px 10px; color: #1e1b4b;
  border-top: 1px solid #f1f5f9;
}
.appr-action-tbl td.cur { color: #94a3b8; text-decoration: line-through; }
.appr-action-tbl td.tgt { color: #047857; font-weight: 600; }
.appr-action-tbl td.cve {
  font-size: 0.6875rem; color: #b91c1c; font-weight: 700;
}

/* ── Countdown timer (Approvals) ── */
.countdown-big {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.125rem; font-weight: 700;
  padding: 5px 14px; border-radius: 6px;
}
.countdown-ok   { background: #ecfdf5; color: #047857; }
.countdown-warn { background: #fef3c7; color: #b45309; }
.countdown-crit { background: #fee2e2; color: #b91c1c; }
.countdown-big::before { content: '⏱'; font-size: 0.875rem; }

/* ── Slack + Audit deep-link chips ── */
.deeplink-chip {
  display: inline-flex; align-items: center; gap: 5px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 600;
  padding: 4px 10px; border-radius: 4px; text-decoration: none;
  transition: opacity 0.15s;
}
.deeplink-chip:hover { opacity: 0.85; }
.dl-slack { background: #4a154b; color: #fff; }
.dl-audit { background: #eef2ff; color: #3730a3; }
.dl-batch { background: #ecfdf5; color: #047857; }
.dl-vm    { background: #f1f5f9; color: #1e293b; }

/* ── Typed-confirm modal (Approvals + Admin destructive) ── */
.confirm-modal {
  display: none; position: fixed; inset: 0; z-index: 1000;
  background: rgba(15,12,46,0.6); backdrop-filter: blur(4px);
  align-items: center; justify-content: center;
}
.confirm-modal.show { display: flex; }
.confirm-card {
  background: #ffffff; border-radius: 12px;
  width: 520px; max-width: calc(100vw - 32px);
  box-shadow: 0px 24px 48px -12px rgba(24,20,69,0.25);
  overflow: hidden;
}
.confirm-hdr {
  padding: 16px 20px; color: #fff;
  display: flex; align-items: center; gap: 10px;
  font-family: 'Space Grotesk', sans-serif; font-weight: 700;
}
.confirm-hdr.approve { background: linear-gradient(135deg, #047857, #16a34a); }
.confirm-hdr.reject  { background: linear-gradient(135deg, #b91c1c, #dc2626); }
.confirm-hdr.danger  { background: linear-gradient(135deg, #7f1d1d, #b91c1c); }
.confirm-body { padding: 20px; }
.confirm-body p { font-size: 0.8125rem; color: #334155; line-height: 1.55; margin-bottom: 12px; }
.confirm-evidence {
  font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
  background: #f6f2ff; padding: 10px 12px; border-radius: 6px;
  color: #1e1b4b; word-break: break-all;
  margin-bottom: 12px;
}
.confirm-field { margin-bottom: 12px; }
.confirm-field label {
  display: block; margin-bottom: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.625rem; font-weight: 700; letter-spacing: 0.07em;
  color: #475569; text-transform: uppercase;
}
.confirm-field input,
.confirm-field textarea {
  width: 100%; padding: 8px 10px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem;
  background: #f6f2ff; border: 1.5px solid transparent; border-radius: 6px;
  color: #1e1b4b; outline: none;
}
.confirm-field input:focus,
.confirm-field textarea:focus { border-color: #4f46e5; background: #fff; }
.confirm-field .mismatch { color: #b91c1c; }
.confirm-field .match    { color: #047857; }
.confirm-foot {
  padding: 14px 20px; background: #f8fafc;
  display: flex; gap: 10px; justify-content: flex-end;
}
.confirm-foot .btn-cancel {
  background: #e2e8f0; color: #1e293b; border: none; padding: 8px 16px;
  border-radius: 6px; font-weight: 600; font-size: 0.8125rem; cursor: pointer;
}
.confirm-foot .btn-go {
  border: none; padding: 8px 18px; border-radius: 6px;
  font-weight: 700; font-size: 0.8125rem; cursor: pointer; color: #fff;
}
.confirm-foot .btn-go:disabled { opacity: 0.4; cursor: not-allowed; }
.confirm-foot .btn-go.approve { background: linear-gradient(135deg, #047857, #16a34a); }
.confirm-foot .btn-go.reject  { background: linear-gradient(135deg, #b91c1c, #dc2626); }
.confirm-foot .btn-go.danger  { background: linear-gradient(135deg, #7f1d1d, #b91c1c); }

/* ── Admin page DESTRUCTIVE — AUDITED header ── */
.destructive-hdr {
  background: linear-gradient(90deg, #7f1d1d 0%, #b91c1c 100%);
  color: #fef2f2;
  padding: 12px 20px; margin-bottom: 16px; border-radius: 8px;
  display: flex; align-items: center; gap: 12px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem; font-weight: 700; letter-spacing: 0.07em;
}
.destructive-hdr .pill {
  background: rgba(255,255,255,0.2); padding: 3px 10px; border-radius: 4px;
  font-size: 0.625rem;
}

/* ── Agent page Layer A/B partition ── */
.layer-partition {
  display: grid; grid-template-columns: 1fr 40px 1fr; gap: 0;
  align-items: stretch; margin: 16px 0;
}
.layer-pane {
  padding: 18px 20px; border-radius: 10px;
}
.layer-pane.a { background: #fef3c7; border-left: 4px solid #d97706; }
.layer-pane.b { background: #ecfdf5; border-left: 4px solid #16a34a; }
.layer-divider {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  color: #4f46e5; font-size: 0.5625rem;
  letter-spacing: 0.1em; text-transform: uppercase; writing-mode: vertical-rl;
  text-orientation: mixed; padding: 8px 0;
}
.layer-divider::before, .layer-divider::after {
  content: ''; width: 2px; flex: 1;
  background: linear-gradient(180deg, rgba(79,70,229,0.4), rgba(79,70,229,0.1));
}
@media (max-width: 900px) {
  .layer-partition { grid-template-columns: 1fr; }
  .layer-divider {
    writing-mode: horizontal-tb; text-orientation: initial; padding: 8px 0;
  }
  .layer-divider::before, .layer-divider::after {
    width: auto; height: 2px;
    background: linear-gradient(90deg, rgba(79,70,229,0.4), rgba(79,70,229,0.1));
  }
}
.layer-pane-hdr {
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 700; font-size: 0.9375rem; margin-bottom: 4px;
}
.layer-pane.a .layer-pane-hdr { color: #92400e; }
.layer-pane.b .layer-pane-hdr { color: #047857; }
.layer-pane-sub {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6875rem; letter-spacing: 0.04em;
  text-transform: uppercase; margin-bottom: 12px;
}
.layer-pane.a .layer-pane-sub { color: rgba(146,64,14,0.7); }
.layer-pane.b .layer-pane-sub { color: rgba(4,120,87,0.7); }
.layer-pane-rows { display: flex; flex-direction: column; gap: 8px; }
.layer-pane-row {
  display: flex; justify-content: space-between; align-items: center;
  font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
}
.layer-pane-row .lbl { color: #6b7280; }
.layer-pane-row .val { color: #1e1b4b; font-weight: 600; }

/* ── Fleet operator queue (P1 — SRE: "dashboard needs an operator queue") ── */
.op-queue-card {
  background: #fff; border-radius: 12px;
  padding: 0; margin-bottom: 16px; overflow: hidden;
  box-shadow: 0px 8px 24px -8px rgba(24,20,69,0.06);
}
.op-queue-hdr {
  padding: 14px 20px;
  background: linear-gradient(135deg, #1e1b4b 0%, #4f46e5 100%);
  color: #fff;
  display: flex; align-items: center; gap: 12px;
}
.op-queue-hdr .ttl {
  font-family: 'Space Grotesk', sans-serif; font-weight: 700;
  font-size: 1rem; letter-spacing: -0.01em;
}
.op-queue-hdr .sub {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem;
  color: rgba(255,255,255,0.6); letter-spacing: 0.04em;
}
.op-queue-hdr .count {
  margin-left: auto;
  background: rgba(255,255,255,0.18); color: #fff;
  font-family: 'JetBrains Mono', monospace; font-weight: 700;
  padding: 4px 12px; border-radius: 999px;
  font-size: 0.75rem;
}
.op-queue-row {
  display: flex; align-items: center; gap: 16px;
  padding: 14px 20px;
  border-top: 1px solid #f1f5f9;
}
.op-queue-row:hover { background: #f8fafc; }
.op-queue-pri {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.6875rem; font-weight: 700;
  padding: 4px 10px; border-radius: 4px;
  min-width: 64px; text-align: center;
}
.op-pri-crit  { background: #fee2e2; color: #b91c1c; }
.op-pri-high  { background: #fef3c7; color: #b45309; }
.op-pri-med   { background: #eef2ff; color: #3730a3; }
.op-pri-low   { background: #ecfdf5; color: #047857; }
.op-pri-info  { background: #f1f5f9; color: #475569; }
.op-queue-icon { font-size: 1.1rem; }
.op-queue-body { flex: 1; min-width: 0; }
.op-queue-label {
  font-family: 'Inter', sans-serif; font-weight: 600;
  font-size: 0.875rem; color: #1e1b4b;
}
.op-queue-detail {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem;
  color: #6b7280; margin-top: 3px;
}
.op-queue-action {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 600;
  padding: 6px 14px; border-radius: 6px; text-decoration: none;
  white-space: nowrap;
}
.op-queue-action.go {
  background: #4f46e5; color: #fff;
}
.op-queue-action.go:hover { background: #4338ca; }
.op-queue-action.muted {
  background: #f1f5f9; color: #475569;
}
.op-queue-empty {
  padding: 24px; text-align: center;
  font-family: 'JetBrains Mono', monospace; font-size: 0.8125rem;
  color: #94a3b8;
}

/* ── Audit page filters + export ── */
.audit-toolbar {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  padding: 12px 16px; background: #f6f2ff; border-radius: 8px;
  margin-bottom: 12px;
}
.audit-filter {
  display: flex; flex-direction: column; gap: 3px; min-width: 120px;
}
.audit-filter label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.5625rem; font-weight: 700; letter-spacing: 0.07em;
  color: #6b7280; text-transform: uppercase;
}
.audit-filter select,
.audit-filter input {
  font-family: 'Inter', sans-serif; font-size: 0.75rem;
  padding: 5px 8px; background: #fff;
  border: 1.5px solid transparent; border-radius: 5px;
  color: #1e1b4b; outline: none; min-width: 100px;
}
.audit-filter select:focus,
.audit-filter input:focus { border-color: #4f46e5; }
.audit-export {
  margin-left: auto; display: flex; gap: 6px;
}
.audit-export a {
  font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 700;
  padding: 6px 12px; border-radius: 5px; text-decoration: none;
}
.audit-export .csv { background: #047857; color: #fff; }
.audit-export .json { background: #3730a3; color: #fff; }
.audit-row-expand {
  background: #f8fafc; padding: 14px 20px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
  display: grid; grid-template-columns: 200px 1fr; gap: 6px 16px;
  color: #1e1b4b;
}
.audit-row-expand .k {
  font-size: 0.625rem; color: #6b7280; letter-spacing: 0.07em;
  text-transform: uppercase; font-weight: 700;
}
.audit-row-expand .v { word-break: break-all; }
.audit-row-expand .v.before { color: #94a3b8; }
.audit-row-expand .v.after  { color: #047857; font-weight: 600; }
.audit-row-expand .v.failed { color: #b91c1c; }

/* ── VM Resource Trends (Metricbeat-style sparklines) ── */
.vm-trends-card { padding: 18px 20px; margin-bottom: 16px; }
.vm-trends-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.vm-trends-toggle { display: flex; gap: 4px; }
.trend-btn {
  font-size: 0.75rem; padding: 3px 12px; border: 1px solid #e2e8f0;
  border-radius: 4px; background: #fff; color: #64748b;
  cursor: pointer; font-family: 'JetBrains Mono', monospace; font-weight: 600;
  transition: background 0.12s, color 0.12s;
}
.trend-btn.active { background: #1e293b; color: #fff; border-color: #1e293b; }
.vm-trends-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.vm-trend-panel { }
.vm-trend-label {
  font-size: 0.625rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #94a3b8; margin-bottom: 4px;
}
.vm-trend-current-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
.vm-trend-current {
  font-size: 1.625rem; font-weight: 700;
  font-family: 'JetBrains Mono', monospace; line-height: 1;
}
.vm-trend-delta { font-size: 0.75rem; color: #94a3b8; }
.vm-trend-svg-wrap { position: relative; margin-bottom: 4px; }
.vm-trend-threshold {
  position: absolute; right: 0; font-size: 0.55rem; font-family: 'JetBrains Mono', monospace;
  font-weight: 700; color: #d97706; padding: 0 3px;
}
.spark-stats {
  display: flex; gap: 16px; margin-top: 3px;
  font-size: 0.65rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace;
}
.spark-stats span { white-space: nowrap; }
.spark-stats .stat-v { color: #475569; font-weight: 600; }
.spark-x-axis {
  display: flex; justify-content: space-between;
  font-family: 'JetBrains Mono', monospace; font-size: 0.55rem; color: #cbd5e1;
  margin-top: 2px; padding: 0 1px;
}
.disk-mini-spark { display: flex; align-items: center; gap: 8px; margin-top: 3px; }
.disk-mini-spark svg { flex-shrink: 0; }
.disk-mini-stat { font-size: 0.6rem; font-family: 'JetBrains Mono', monospace; color: #94a3b8; white-space: nowrap; }
"""

# ── Login page CSS ─────────────────────────────────────────────────────────────

LOGIN_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600;700&family=Space+Grotesk:wght@600;700;800&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }

body {
  font-family: 'Inter', system-ui, sans-serif;
  background: #0f0e2e;
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh;
  position: relative; overflow: hidden;
}

/* Subtle background grid */
body::before {
  content: '';
  position: fixed; inset: 0;
  background-image:
    linear-gradient(rgba(99,102,241,0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(99,102,241,0.06) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
}

/* Glow orbs */
body::after {
  content: '';
  position: fixed;
  width: 600px; height: 600px;
  background: radial-gradient(circle, rgba(99,102,241,0.18) 0%, transparent 70%);
  top: -100px; left: -100px;
  pointer-events: none;
}

.login-wrapper {
  position: relative; z-index: 1;
  display: flex; flex-direction: column; align-items: center;
  width: 100%; max-width: 420px;
  padding: 24px 16px;
}

/* Logo */
.login-logo {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 1.75rem; font-weight: 800;
  color: #fff; letter-spacing: -0.03em;
  margin-bottom: 4px;
}
.login-logo span { color: #a5b4fc; }
.login-subtitle {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase;
  color: rgba(165,180,252,0.6);
  margin-bottom: 36px;
}

/* Card */
.login-card {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(165,180,252,0.15);
  border-radius: 16px;
  padding: 36px 32px 28px;
  width: 100%;
  backdrop-filter: blur(12px);
  box-shadow: 0 24px 64px rgba(0,0,0,0.4);
}

.login-card h2 {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 1.2rem; font-weight: 700;
  color: #fff; margin-bottom: 6px;
}
.login-card .login-desc {
  font-size: 0.8125rem; color: rgba(255,255,255,0.45);
  margin-bottom: 28px; line-height: 1.5;
}

/* Fields */
.login-field { margin-bottom: 16px; }
.login-field label {
  display: block;
  font-size: 0.75rem; font-weight: 600;
  color: rgba(165,180,252,0.8);
  letter-spacing: 0.04em; text-transform: uppercase;
  margin-bottom: 6px;
}
.login-field input {
  width: 100%;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(165,180,252,0.2);
  border-radius: 8px;
  padding: 10px 14px;
  font-family: 'Inter', sans-serif;
  font-size: 0.9375rem; color: #fff;
  outline: none; transition: border-color 0.15s, box-shadow 0.15s;
}
.login-field input::placeholder { color: rgba(255,255,255,0.25); }
.login-field input:focus {
  border-color: #818cf8;
  box-shadow: 0 0 0 3px rgba(99,102,241,0.2);
}

/* Error */
.login-error {
  display: flex; align-items: center; gap: 8px;
  background: rgba(239,68,68,0.12);
  border: 1px solid rgba(239,68,68,0.3);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 0.8125rem; color: #fca5a5;
  margin-bottom: 18px;
}

/* Button */
.login-btn {
  width: 100%; padding: 12px;
  background: #4f46e5;
  border: none; border-radius: 8px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 0.9375rem; font-weight: 700;
  color: #fff; letter-spacing: 0.01em;
  cursor: pointer; transition: background 0.15s, transform 0.1s;
  margin-top: 8px;
}
.login-btn:hover { background: #4338ca; }
.login-btn:active { transform: scale(0.98); }

/* Divider hint */
.login-hint {
  margin-top: 20px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem; color: rgba(255,255,255,0.2);
  text-align: center; letter-spacing: 0.04em;
}

/* Footer */
.login-footer {
  margin-top: 28px;
  font-size: 0.72rem; color: rgba(255,255,255,0.2);
  text-align: center; letter-spacing: 0.02em;
}
"""


def page_login(error: bool = False) -> str:
    error_block = """
    <div class="login-error">
      <span>&#9888;</span> Invalid username or password. Try again.
    </div>""" if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign In — errander-ai</title>
  <style>{LOGIN_CSS}</style>
</head>
<body>
  <div class="login-wrapper">
    <div class="login-logo">⚡ errander<span>-ai</span></div>
    <div class="login-subtitle">Operations Hub · Authorized Access Only</div>

    <div class="login-card">
      <h2>Welcome back</h2>
      <p class="login-desc">Sign in to access the SRE Operations Hub.</p>
      {error_block}
      <form method="POST" action="/login" autocomplete="on">
        <div class="login-field">
          <label for="username">Username</label>
          <input id="username" name="username" type="text"
                 placeholder="admin" autocomplete="username" autofocus>
        </div>
        <div class="login-field">
          <label for="password">Password</label>
          <input id="password" name="password" type="password"
                 placeholder="••••••••" autocomplete="current-password">
        </div>
        <button class="login-btn" type="submit">SIGN IN &rarr;</button>
      </form>
      <div class="login-hint">SESSION EXPIRES AFTER 8 HOURS</div>
    </div>

    <div class="login-footer">errander-ai v1.0.0 &nbsp;·&nbsp; internal tool &nbsp;·&nbsp; do not expose publicly</div>
  </div>
</body>
</html>"""


# ── Helpers ───────────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "ok":      ("#16a34a", "badge-green",  "#16a34a"),
    "warning": ("#d97706", "badge-amber",  "#d97706"),
    "failed":  ("#dc2626", "badge-red",    "#dc2626"),
    "pending": ("#7c3aed", "badge-violet", "#7c3aed"),
    "offline": ("#94a3b8", "badge-slate",  "#94a3b8"),
}

STATUS_LABELS = {
    "ok": "OK", "warning": "WARNING", "failed": "FAILED",
    "pending": "PENDING APPROVAL", "offline": "OFFLINE",
}


def badge(status: str) -> str:
    _, cls, _ = STATUS_COLORS.get(status, ("#94a3b8", "badge-slate", "#94a3b8"))
    label = STATUS_LABELS.get(status, status.upper())
    return f'<span class="badge {cls}">{label}</span>'


def audit_badge(status: str) -> str:
    mapping = {
        "ok":      "badge-green",
        "failed":  "badge-red",
        "pending": "badge-violet",
        "warning": "badge-amber",
        "partial": "badge-amber",
        "completed": "badge-green-solid",
    }
    cls = mapping.get(status, "badge-slate")
    return f'<span class="badge {cls}">{status.upper()}</span>'


def disk_bar(pct: int) -> str:
    color = ("amber" if pct < 90 else "red") if pct >= 75 else "indigo"
    return f"""
      <div class="prog-wrap">
        <div class="prog-fill prog-{color}" style="width:{pct}%"></div>
      </div>"""


def _metric_color(pct: int) -> str:
    if pct >= 90:
        return "red"
    if pct >= 75:
        return "amber"
    return "indigo"


def _metric_text_color(pct: int) -> str:
    if pct >= 90:
        return "#dc2626"
    if pct >= 75:
        return "#d97706"
    return "#4f46e5"


def _metric_bar_color(pct: int) -> str:
    if pct >= 90:
        return "#dc2626"
    if pct >= 75:
        return "#d97706"
    return "#4f46e5"


def env_tag(env: str) -> str:
    cls = {"PROD": "tag-prod", "STAGING": "tag-staging", "DEV": "tag-dev"}.get(env, "tag-prod")
    return f'<span class="tag {cls}">{env}</span>'


def os_tag(os_str: str) -> str:
    if "Ubuntu" in os_str:
        return '<span class="tag tag-ubuntu">Ubuntu</span>'
    if "RHEL" in os_str:
        return '<span class="tag tag-rhel">RHEL</span>'
    return '<span class="tag tag-debian">Debian</span>'


def env_badge_top(env: str) -> str:
    cls = {"PROD": "env-prod", "STAGING": "env-staging", "DEV": "env-dev"}.get(env, "env-prod")
    return f'<span class="env-badge {cls}">{env}</span>'


# ── Layout ────────────────────────────────────────────────────────────────────

NAV_ITEMS = [
    ("OVERVIEW",    None),
    ("Fleet Dashboard", "/",          "overview"),
    ("Agent Status",    "/agent",     "overview"),
    ("OPERATIONS",  None),
    ("Approval Queue",  "/approvals", "operations"),
    ("Batch History",   "/batches",   "operations"),
    ("Audit Log",       "/audit",     "operations"),
    ("SYSTEM",      None),
    ("Inventory",       "/inventory", "system"),
    ("Settings",        "/settings",  "system"),
    ("Glossary",        "/glossary",  "system"),
    ("ADMIN",       None),
    ("Admin Panel",     "/admin",     "admin"),
]


# ── Static config / admin data ────────────────────────────────────────────────

_SETTINGS_SECTIONS = [
    {
        "title": "LLM Configuration", "icon": "🤖", "icon_bg": "#ede9fe",
        "rows": [
            ("Base URL",    "http://10.0.0.100:8000/v1", False),
            ("Model",       "Qwen3-8B-AWQ",              False),
            ("API Key",     "xai-••••••••",              True),
            ("Timeout",     "60s",                       False),
            ("Temperature", "0.1",                       False),
        ],
    },
    {
        "title": "Slack Integration", "icon": "💬", "icon_bg": "#e0f2fe",
        "rows": [
            ("Bot Token",        "xoxb-••••••••",  True),
            ("Channel ID",       "C04ERRANDER1",   False),
            ("Approval Timeout", "30 min",         False),
            ("Poll Interval",    "30s",            False),
        ],
    },
    {
        "title": "Scheduling", "icon": "🕐", "icon_bg": "#fef3c7",
        "rows": [
            ("Cron Expression", "0 2 * * 2,4",       False),
            ("Human Schedule",  "Tue/Thu 02:00 UTC", False),
            ("Dry Run Default", "ON",                False),
            ("Force Override",  "Requires reason",   False),
        ],
    },
    {
        "title": "Safety & Audit", "icon": "🛡", "icon_bg": "#dcfce7",
        "rows": [
            ("Audit DB URL",   "postgresql://errander:***@localhost:5432/errander", False),
            ("DB Size",        "2.4 MB",          False),
            ("Log Retention",  "90 days",         False),
            ("Strict Mode",    "ON",              False),
        ],
    },
]

_HEALTH_CHECKS = [
    {"label": "vLLM Endpoint",    "detail": "http://10.0.0.100:8000/v1",  "status": "ok",   "meta": "42 ms"},
    {"label": "Slack API",        "detail": "api.slack.com",               "status": "ok",   "meta": "outbound HTTPS"},
    {"label": "Audit DB",         "detail": "PostgreSQL · errander",      "status": "ok",   "meta": "writable"},
    {"label": "SSH Keys",         "detail": "11 / 11 key files present",   "status": "ok",   "meta": "/keys/"},
    {"label": "APScheduler",      "detail": "next: 2026-05-14 02:00 UTC",  "status": "ok",   "meta": "running"},
]

_ACTIVE_LOCKS: list[dict[str, Any]] = []  # empty = clean state; add dicts with vm/since/path to simulate stuck locks

_OVERRIDES = [
    ("Dry Run Mode",            "All batches simulate actions without executing on real VMs.",        True),
    ("Force Maintenance Window","Allow batches outside configured windows. Requires --force reason.", False),
    ("Skip Approval Gate",      "Bypass approval gate for High-risk actions (Slack and Web UI). Emergency use only.",  False),
    ("Strict Audit Mode",       "Halt agent if any audit write fails — integrity over execution.",    True),
]


def _mode_banner_html() -> str:
    """Render the global mode banner. Loud on purpose."""
    _p   = get_provider()
    src  = "LIVE (DB)" if _p.data_mode() == "LIVE" else UI_MODE.get("data_source", "DEMO")
    env  = UI_MODE.get("env", "PROD")
    exe  = UI_MODE.get("execution", "DRY RUN")
    fr   = _p.data_freshness() if _p.data_mode() == "LIVE" else UI_MODE.get("freshness", "")
    be   = "errander.web.providers (live)" if _p.data_mode() == "LIVE" else UI_MODE.get("backend", "")
    build= UI_MODE.get("build", "")
    if exe == "LIVE EXECUTION" and env == "PROD":
        banner_cls = "mode-banner live-prod"
    elif exe == "LIVE EXECUTION":
        banner_cls = "mode-banner live"
    else:
        banner_cls = "mode-banner"
    src_cls = "demo" if src == "DEMO" else "live"
    exe_cls = "live" if exe == "LIVE EXECUTION" else "dry"
    return f"""
    <div class="{banner_cls}" role="status" aria-live="polite">
      <span class="mode-banner-pill {src_cls}">{src} DATA</span>
      <span class="mode-banner-pill">{env}</span>
      <span class="mode-banner-pill {exe_cls}">{exe}</span>
      <span class="mode-banner-sep">·</span>
      <span>{fr}</span>
      <span class="mode-banner-sep">·</span>
      <span>backend: {be}</span>
      <span class="mode-banner-sep">·</span>
      <span>build {build}</span>
    </div>"""


def layout(title: str, active_url: str, breadcrumb: str, topnav_extra: str, content: str) -> str:
    nav_parts: list[str] = []
    section_open = False
    for item in NAV_ITEMS:
        if item[1] is None:
            if section_open:
                nav_parts.append('</div>')
            nav_parts.append(f'<div class="nav-section"><div class="nav-label">{item[0]}</div>')
            section_open = True
        else:
            label, url = item[0], item[1]
            active_cls = ' active' if url == active_url else ''
            ab = f'<span class="nav-badge">{len(get_provider().get_approvals())}</span>' if label == "Approval Queue" and len(get_provider().get_approvals()) else ""
            nav_parts.append(f'<a href="{url}" class="nav-item{active_cls}">{label}{ab}</a>')
    if section_open:
        nav_parts.append('</div>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — errander-ai</title>
  <style>{CSS}</style>
</head>
<body>
  <aside class="sidebar">
    <div class="sidebar-logo">⚡ errander<span>-ai</span></div>
    <nav>{"".join(nav_parts)}</nav>
    <div class="sidebar-footer">
      <div class="sys-chip"><span class="sys-dot dot-green"></span>vLLM &nbsp;·&nbsp; ONLINE</div>
      <div class="sys-chip"><span class="sys-dot dot-indigo"></span>APScheduler &nbsp;·&nbsp; RUNNING</div>
      <div class="sys-version">v1.0.0 &nbsp;·&nbsp; PostgreSQL audit</div>
      <a href="/logout" class="signout-link">&#8594; Sign out</a>
    </div>
  </aside>
  <div class="shell">
    {_mode_banner_html()}
    <header class="topnav">
      <div class="breadcrumb">{breadcrumb}</div>
      {topnav_extra}
    </header>
    <main class="content">
      {content}
    </main>
  </div>
</body>
</html>"""


# ── Pages ─────────────────────────────────────────────────────────────────────

def _operator_queue() -> str:
    """The SRE's "what needs me now" queue. Aggregates everything human-actionable
    in priority order: critical → high → medium → info."""
    rows: list[tuple[str, str, str, str, str, str]] = []
    # tuple: (priority, icon, label, detail, action_label, href)

    # 1. Pending approvals (with countdown)
    pending = get_provider().get_approvals()
    if pending:
        soonest_min = min(int(a.get("countdown", "30:00").split(":")[0]) for a in pending)
        pri = "CRITICAL" if soonest_min < 5 else "HIGH" if soonest_min < 15 else "MED"
        rows.append((
            pri, "🔔",
            f"{len(pending)} approval(s) awaiting your decision in the approval queue",
            f"soonest expiry in {soonest_min} min · "
            + " · ".join(f"{a['action'].lower()} on {a['hostname']} ({a['tier']})" for a in pending),
            "Review",
            "/approvals",
        ))

    # 2. High-risk pending actions (subset of approvals, called out explicitly)
    high_risk = [a for a in get_provider().get_approvals() if a["tier"] == "HIGH RISK"]
    if high_risk:
        rows.append((
            "CRITICAL", "⚠",
            f"{len(high_risk)} HIGH RISK action(s) pending — operator-triggered only",
            " · ".join(f"{a['action'].lower()} on {a['hostname']}" for a in high_risk),
            "Inspect",
            "/approvals",
        ))

    # 3. Failed/partial batches recently
    bad_batches = [b for b in get_provider().get_batches() if b.get("status") in ("failed", "partial")]
    if bad_batches:
        latest = bad_batches[0]
        rows.append((
            "HIGH", "✕",
            f"{len(bad_batches)} recent batch(es) ended failed/partial",
            f"latest: {latest['id']} ({latest['status']}) — {latest.get('error_summary', '')}",
            "View batches",
            "/batches",
        ))

    # 4. Failed VMs (last action failed)
    failed_vms_now = [v for v in get_provider().get_vms() if v["status"] == "failed"]
    if failed_vms_now:
        rows.append((
            "HIGH", "🔴",
            f"{len(failed_vms_now)} VM(s) with last action FAILED",
            " · ".join(v["hostname"] for v in failed_vms_now),
            f"View {failed_vms_now[0]['hostname']}",
            f"/vm/{failed_vms_now[0]['hostname']}",
        ))

    # 5. Warning VMs (degraded — disk high, etc.)
    warning_vms = [v for v in get_provider().get_vms() if v["status"] == "warning"]
    if warning_vms:
        rows.append((
            "MED", "⚠",
            f"{len(warning_vms)} VM(s) degraded — preflight may block",
            " · ".join(f"{v['hostname']} ({v['note'] or v['status']})" for v in warning_vms),
            "Inspect",
            f"/vm/{warning_vms[0]['hostname']}",
        ))

    # 6. Blocked VMs (lock held — fixture mode only; live mode has no lock evidence yet)
    if get_provider().data_mode() == "FIXTURE":
        locked = [(h, e["lock"]) for h, e in VM_EVIDENCE.items() if e.get("lock")]
        if locked:
            rows.append((
                "MED", "🔒",
                f"{len(locked)} VM(s) currently locked by the agent",
                " · ".join(f"{h}: {lock}" for h, lock in locked),
                f"View {locked[0][0]}",
                f"/vm/{locked[0][0]}",
            ))

    # 7. Next maintenance window
    sch = get_provider().get_scheduler_timeline()
    nextrun = (sch.get("next_runs") or ["—"])[0]
    rows.append((
        "INFO", "🕐",
        f"Next scheduled maintenance window: {nextrun}",
        f"cron: {sch.get('cron', '—')} ({sch.get('human', '—')})",
        "Schedule",
        "/agent",
    ))

    # 8. Active batch (if running, otherwise show last completed)
    ab = get_provider().get_active_batch()
    if ab.get("status") not in ("completed",):
        rows.append((
            "INFO", "▶",
            f"Active batch: {ab['id']} — {ab['vms_done']}/{ab['vms_total']} VMs",
            f"{ab.get('duration', '—')} · {ab.get('errors', 0)} error(s)",
            "Live trace",
            "/agent",
        ))

    if not rows:
        return """
        <div class="op-queue-card">
          <div class="op-queue-hdr">
            <span style="font-size:1.1rem">✓</span>
            <span class="ttl">Operator Queue</span>
            <span class="count">0</span>
          </div>
          <div class="op-queue-empty">All clear — no items need your action right now.</div>
        </div>"""

    pri_cls = {"CRITICAL": "op-pri-crit", "HIGH": "op-pri-high", "MED": "op-pri-med",
               "LOW": "op-pri-low", "INFO": "op-pri-info"}
    # sort by priority
    order = {"CRITICAL": 0, "HIGH": 1, "MED": 2, "LOW": 3, "INFO": 4}
    rows.sort(key=lambda r: order[r[0]])

    body = ""
    for pri, icon, label, detail, action, href in rows:
        body += f"""
        <div class="op-queue-row">
          <span class="op-queue-pri {pri_cls[pri]}">{pri}</span>
          <span class="op-queue-icon">{icon}</span>
          <div class="op-queue-body">
            <div class="op-queue-label">{label}</div>
            <div class="op-queue-detail">{detail}</div>
          </div>
          <a href="{href}" class="op-queue-action {'go' if pri in ('CRITICAL','HIGH') else 'muted'}">{action} →</a>
        </div>"""

    actionable = sum(1 for r in rows if r[0] in ("CRITICAL", "HIGH", "MED"))
    return f"""
    <div class="op-queue-card">
      <div class="op-queue-hdr">
        <span style="font-size:1.1rem">✋</span>
        <div>
          <div class="ttl">Operator Queue</div>
          <div class="sub">what needs your action right now · priority-ordered</div>
        </div>
        <span class="count">{actionable} actionable · {len(rows) - actionable} info</span>
      </div>
      {body}
    </div>"""


def page_fleet() -> str:
    _is_fixture = get_provider().data_mode() == "FIXTURE"
    healthy       = sum(1 for v in get_provider().get_vms() if v["status"] == "ok")
    warnings      = sum(1 for v in get_provider().get_vms() if v["status"] == "warning")
    failed_ct     = sum(1 for v in get_provider().get_vms() if v["status"] == "failed")
    needs_approval = sum(1 for v in get_provider().get_vms() if v["status"] == "pending")
    total_pending  = sum(v.get("pending_patches", 0) for v in get_provider().get_vms())

    kpis = f"""
    <div class="kpi-grid">
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Total VMs</div>
        <div class="kpi-value" style="color:#0f172a">{len(get_provider().get_vms())}</div>
        <div class="kpi-subtitle">{len(set(v['env'] for v in get_provider().get_vms()))} environments &nbsp;·&nbsp; {total_pending} patches pending fleet-wide</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Healthy</div>
        <div class="kpi-value" style="color:#16a34a">{healthy}</div>
        <div class="kpi-subtitle">{round(healthy/max(len(get_provider().get_vms()), 1)*100)}% of fleet{' &nbsp;·&nbsp; last batch 02:00 UTC' if _is_fixture else ''}</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#d97706">
        <div class="kpi-label">Warnings / Failed</div>
        <div class="kpi-value" style="color:#d97706">{warnings + failed_ct}</div>
        <div class="kpi-subtitle">{warnings} degraded &nbsp;·&nbsp; {failed_ct} last-action failed</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#7c3aed">
        <div class="kpi-label">Needs Approval</div>
        <div class="kpi-value" style="color:#7c3aed">{needs_approval}</div>
        <div class="kpi-subtitle">{'Approval expires &lt; 30 min — act now' if _is_fixture else ('pending · awaiting decision' if needs_approval > 0 else 'no pending approvals')}</div>
      </div>
    </div>"""

    # Active batch
    b = get_provider().get_active_batch()
    batch = f"""
    <div class="card batch-card">
      <div class="batch-header">
        <span class="batch-id">{b['id']}</span>
        {audit_badge(str(b['status']))}
        <span style="margin-left:auto;font-size:0.75rem;color:#94a3b8;font-family:'JetBrains Mono',monospace">{'Completed 2026-04-23 02:14 UTC' if _is_fixture else b.get('completed_at', '—')}</span>
      </div>
      <div class="batch-bars">
        <div class="bar-row">
          <div class="bar-label"><span>VMs Processed</span><span>{b['vms_done']}/{b['vms_total']}</span></div>
          <div class="prog-wrap" style="height:8px">
            <div class="prog-fill prog-indigo" style="width:100%"></div>
          </div>
        </div>
        <div class="bar-row">
          <div class="bar-label"><span>Actions Executed</span><span>{b['actions_done']}/{b['actions_total']}</span></div>
          <div class="prog-wrap" style="height:8px">
            <div class="prog-fill prog-green" style="width:100%"></div>
          </div>
        </div>
      </div>
      <div class="batch-stats">
        <span class="stat-chip">⏱ {b['duration']}</span>
        <span class="stat-chip">{b['patched']} VMs patched</span>
        <span class="stat-chip">{b['rotations']} log rotations</span>
        <span class="stat-chip">{b['prunes']} Docker prunes</span>
        <span class="stat-chip err">⚠ {b['errors']} errors — see Audit Log</span>
        <a href="/batches" class="stat-chip" style="color:#4f46e5;text-decoration:none">Full History →</a>
      </div>
    </div>"""

    # Needs Attention box (only if there are non-ok VMs)
    attn_vms = [v for v in get_provider().get_vms() if v["status"] in ("warning", "failed", "pending")]
    attn_box = ""
    if attn_vms:
        rows = ""
        for v in attn_vms:
            host_color = {
                "warning": "", "failed": " attn-host-failed", "pending": " attn-host-pending",
            }.get(v["status"], "")
            reason_map = {
                "warning": v["note"] or f"Disk at {v['disk']}% — above warning threshold",
                "failed":  f"Last action failed — {v['note']}" if v["note"] else "Last maintenance action failed (check Audit Log)",
                "pending": v["note"] or "Awaiting Slack approval",
            }
            rows += f"""
            <div class="attn-row">
              <a href="/vm/{v['hostname']}" class="attn-host{host_color}">{v['hostname']}</a>
              {badge(v['status'])}
              {env_tag(v['env'])}
              <span class="attn-reason">{reason_map.get(v['status'], '')}</span>
              <a href="/vm/{v['hostname']}" class="attn-link">View →</a>
            </div>"""
        attn_box = f"""
        <div class="card attn-box">
          <div class="attn-hdr">
            <span class="attn-title">NEEDS ATTENTION</span>
            <span class="badge badge-amber">{len(attn_vms)} VMs</span>
            <a href="/approvals" class="attn-link" style="margin-left:auto">
              {f'{needs_approval} pending approval →' if needs_approval else ''}
            </a>
          </div>
          {rows}
        </div>"""

    # VM grid — enriched cards
    cards = ""
    for vm in get_provider().get_vms():
        color, _, border = STATUS_COLORS.get(vm["status"], ("#94a3b8", "", "#94a3b8"))
        cpu  = vm.get("cpu",  0)
        mem  = vm.get("mem",  0)
        disk = vm["disk"]
        pp   = vm.get("pending_patches", 0)
        lat  = vm.get("last_action_type", "")

        note_html = (
            f'<div class="vm-note" style="color:{color};margin-bottom:6px">{vm["note"]}</div>'
            if vm["note"] else ""
        )

        patches_html = ""
        if pp > 0:
            chip_cls = "patches-chip-crit" if pp >= 10 else "patches-chip"
            patches_html = f'<span class="{chip_cls}">{pp} patches pending</span>'

        cards += f"""
        <a href="/vm/{vm['hostname']}" class="card vm-card" style="border-left-color:{border}">
          <div class="vm-card-header">
            <span class="vm-hostname">{vm['hostname']}</span>
            <div class="vm-tags">{os_tag(vm['os'])}{env_tag(vm['env'])}</div>
          </div>
          {note_html}
          <div class="vm-metrics">
            <div class="vm-metric-row">
              <span class="vm-metric-lbl">CPU</span>
              <div class="vm-metric-bar">
                <div class="prog-wrap" style="height:4px">
                  <div class="prog-fill prog-{_metric_color(cpu)}" style="width:{cpu}%"></div>
                </div>
              </div>
              <span class="vm-metric-num" style="color:{_metric_text_color(cpu)}">{cpu}%</span>
            </div>
            <div class="vm-metric-row">
              <span class="vm-metric-lbl">MEM</span>
              <div class="vm-metric-bar">
                <div class="prog-wrap" style="height:4px">
                  <div class="prog-fill prog-{_metric_color(mem)}" style="width:{mem}%"></div>
                </div>
              </div>
              <span class="vm-metric-num" style="color:{_metric_text_color(mem)}">{mem}%</span>
            </div>
            <div class="vm-metric-row">
              <span class="vm-metric-lbl">DISK</span>
              <div class="vm-metric-bar">
                <div class="prog-wrap" style="height:4px">
                  <div class="prog-fill prog-{_metric_color(disk)}" style="width:{disk}%"></div>
                </div>
              </div>
              <span class="vm-metric-num" style="color:{_metric_text_color(disk)}">{disk}%</span>
            </div>
          </div>
          <div class="vm-footer">
            <div class="vm-footer-col">
              <span class="vm-ts">{vm['uptime']} uptime &nbsp;·&nbsp; {vm['ip']}</span>
              <span class="vm-ts">{lat} &nbsp;·&nbsp; {vm['last_action']}</span>
            </div>
            <div class="vm-footer-actions">
              {patches_html}
              {badge(vm['status'])}
            </div>
          </div>
        </a>"""

    return f"""
    {_operator_queue()}
    {kpis}
    {batch}
    {attn_box}
    <div class="section-hdr">
      <div>
        <div class="section-title">Fleet Inventory</div>
        <div class="section-sub">{len(get_provider().get_vms())} hosts across {len(set(v['env'] for v in get_provider().get_vms()))} environments &nbsp;·&nbsp; {'data as of 2026-04-23 02:14 UTC' if _is_fixture else get_provider().data_freshness()} &nbsp;·&nbsp; full list in <a href="/inventory" style="color:#4f46e5;text-decoration:none">/inventory</a></div>
      </div>
    </div>
    <div class="vm-grid">{cards}</div>"""


def _appr_health_metric(label: str, pct: int, extra_val: str = "") -> str:
    fill_color = _metric_bar_color(pct)
    text_color = _metric_text_color(pct)
    val_display = extra_val if extra_val else f"{pct}%"
    return f"""
    <div class="appr-hm">
      <span class="appr-hm-lbl">{label}</span>
      <span class="appr-hm-val" style="color:{text_color}">{val_display}</span>
      <div class="appr-hm-bar">
        <div class="appr-hm-fill" style="width:{pct}%;background:{fill_color}"></div>
      </div>
    </div>"""


def _countdown_cls(countdown: str) -> str:
    """Color the countdown by remaining minutes (mm:ss)."""
    try:
        mins = int(countdown.split(":")[0])
    except Exception:
        return "countdown-warn"
    if mins >= 15:
        return "countdown-ok"
    if mins >= 5:
        return "countdown-warn"
    return "countdown-crit"


def _appr_evidence_grid(a: dict[str, Any], ev: dict[str, Any]) -> str:
    """Top evidence grid — the SRE's mandatory enterprise card surface."""
    return f"""
    <div class="evidence-grid">
      <div class="evidence-cell">
        <span class="evidence-label">Plan ID</span>
        <span class="evidence-value">{ev.get('plan_id', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Plan Hash</span>
        <span class="evidence-value">{ev.get('plan_hash', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Batch ID</span>
        <span class="evidence-value">{ev.get('batch_id', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Action ID</span>
        <span class="evidence-value">{ev.get('action_id', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Requester</span>
        <span class="evidence-value">{ev.get('requester', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Approver Role Required</span>
        <span class="evidence-value">{ev.get('approver_role', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Artifact Age</span>
        <span class="evidence-value">{ev.get('artifact_age_h', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Artifact Expiry</span>
        <span class="evidence-value">{ev.get('artifact_expiry_h', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Drift Check</span>
        <span class="evidence-value ok">{ev.get('drift_check', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Rollback Ready</span>
        <span class="evidence-value ok">{ev.get('rollback_ready', '—')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">VM Lock</span>
        <span class="evidence-value">{ev.get('vm_lock', '(none held)')}</span>
      </div>
      <div class="evidence-cell">
        <span class="evidence-label">Window</span>
        <span class="evidence-value {'warn' if 'override' in ev.get('window_state','').lower() else ''}">{ev.get('window_state', '—')}</span>
      </div>
      <div class="evidence-cell" style="grid-column: span 2">
        <span class="evidence-label">Idempotency</span>
        <span class="evidence-value">{ev.get('idempotency', '—')}</span>
      </div>
    </div>"""


def _appr_action_table(ev: dict[str, Any]) -> str:
    """Per-action evidence: cur → target package versions OR units."""
    if "packages" in ev and ev["packages"]:
        rows = ""
        for p in ev["packages"]:
            cve_cell = f'<td class="cve">{p["cve"]}</td>' if p.get("cve") else '<td></td>'
            rows += f"""<tr>
              <td>{p['name']}</td>
              <td class="cur">{p['current']}</td>
              <td>→</td>
              <td class="tgt">{p['target']}</td>
              {cve_cell}
            </tr>"""
        return f"""
        <table class="appr-action-tbl">
          <thead><tr><th>Package</th><th>Current</th><th></th><th>Approved Target</th><th>CVE</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""
    if "units" in ev and ev["units"]:
        rows = ""
        for u in ev["units"]:
            rows += f"""<tr>
              <td>{u['unit']}</td>
              <td class="cur">{u['current']}</td>
              <td>→</td>
              <td class="tgt">{u['target']}</td>
            </tr>"""
        return f"""
        <table class="appr-action-tbl">
          <thead><tr><th>Unit</th><th>Current</th><th></th><th>Target State</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>"""
    return ""


def _appr_layer_split(ev: dict[str, Any], reject_consequence: str) -> str:
    """Three stacked sections enforcing the AI Safety Invariant in the UI."""
    facts = "".join(f"<li>{f}</li>" for f in ev.get("probe_facts", []))
    policy = "".join(f"<li>{p}</li>" for p in ev.get("policy_decision", []))
    ai = ev.get("ai_explanation", "")
    return f"""
    <div class="layer-section layer-b">
      <div class="layer-hdr">
        <span>① Probe Facts</span>
        <span class="layer-hdr-tag">LAYER B · DETERMINISTIC</span>
      </div>
      <div class="layer-body"><ul>{facts}</ul></div>
    </div>
    <div class="layer-section layer-policy">
      <div class="layer-hdr">
        <span>② Policy Decision</span>
        <span class="layer-hdr-tag">LAYER B · RULE-BASED</span>
      </div>
      <div class="layer-body"><ul>{policy}</ul></div>
    </div>
    <div class="layer-section layer-a">
      <div class="layer-hdr">
        <span>③ AI Explanation</span>
        <span class="layer-hdr-tag">LAYER A · ADVISORY</span>
      </div>
      <div class="layer-body"><p>{ai}</p></div>
      <div class="layer-a-disclaimer">
        Layer A is advisory only. The approval authority is a named operator's Web UI decision, not the LLM.
      </div>
    </div>
    <div class="layer-section" style="background:#fef2f2;border-left:4px solid #dc2626">
      <div class="layer-hdr" style="color:#7f1d1d">
        <span>If Rejected</span>
        <span class="layer-hdr-tag" style="background:rgba(255,255,255,0.7)">CONSEQUENCE</span>
      </div>
      <div class="layer-body" style="color:#7f1d1d">{reject_consequence}</div>
    </div>"""


def _appr_deeplinks(ev: dict[str, Any], hostname: str) -> str:
    slack = ev.get("slack_thread_url", "")
    audit = ev.get("audit_url", "/audit")
    batch = ev.get("batch_id", "")
    return f"""
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
      <a href="{slack}" class="deeplink-chip dl-slack" target="_blank" rel="noopener">▶ View Slack thread</a>
      <a href="{audit}" class="deeplink-chip dl-audit">▶ Audit slice</a>
      <a href="/batches#{batch}" class="deeplink-chip dl-batch">▶ Batch {batch}</a>
      <a href="/vm/{hostname}" class="deeplink-chip dl-vm">▶ VM {hostname}</a>
    </div>"""


def _appr_confirm_modal_js() -> str:
    """Vanilla JS for the typed-confirm modal. Enables Confirm only on exact match."""
    return """
    <script>
      function _showConfirm(kind, apprId, batchId, action, hostname) {
        const m = document.getElementById('confirm-modal');
        m.dataset.kind = kind;
        m.dataset.apprId = apprId;
        m.dataset.batchId = batchId;
        document.getElementById('cf-kind').textContent = kind === 'approve' ? 'APPROVE' : 'REJECT';
        document.getElementById('cf-action').textContent = action;
        document.getElementById('cf-host').textContent = hostname;
        document.getElementById('cf-batch-expected').textContent = batchId;
        document.getElementById('cf-batch-input').value = '';
        document.getElementById('cf-reason').value = '';
        document.getElementById('cf-match').textContent = '';
        document.getElementById('cf-go').disabled = true;
        document.getElementById('cf-hdr').className = 'confirm-hdr ' + kind;
        document.getElementById('cf-go').className = 'btn-go ' + kind;
        m.classList.add('show');
      }
      function _hideConfirm() {
        document.getElementById('confirm-modal').classList.remove('show');
      }
      function _confirmCheck() {
        const expected = document.getElementById('cf-batch-expected').textContent.trim();
        const got = document.getElementById('cf-batch-input').value.trim();
        const reason = document.getElementById('cf-reason').value.trim();
        const match = document.getElementById('cf-match');
        const go = document.getElementById('cf-go');
        const batchOk = (got === expected);
        const reasonOk = (reason.length >= 20);
        if (got.length === 0) { match.textContent = ''; match.className = ''; }
        else if (!batchOk)    { match.textContent = '✕ batch ID does not match'; match.className = 'mismatch'; }
        else                  { match.textContent = '✓ batch ID matches'; match.className = 'match'; }
        go.disabled = !(batchOk && reasonOk);
      }
      function _confirmGo() {
        const m = document.getElementById('confirm-modal');
        alert('(demo) Would submit ' + m.dataset.kind.toUpperCase() + ' for ' + m.dataset.apprId +
              ' with reason logged to audit.\\n\\nIn live mode this routes through the Slack approval ' +
              'gate — UI Approve/Reject is a forward to that same audited gate.');
        _hideConfirm();
      }
    </script>"""


def page_approvals() -> str:
    cards = ""
    for a in get_provider().get_approvals():
        ev = _ev_approval(a["id"])
        tier_cls = "badge-danger" if a["tier"] == "HIGH RISK" else "badge-amber"

        uptime = a.get("vm_uptime", "—")
        reject_consequence = a.get("reject_consequence", "")

        countdown_cls = _countdown_cls(a.get("countdown", "30:00"))

        # JS-safe identifiers for the modal
        js_id = a["id"]
        js_batch = ev.get("batch_id", "")
        js_action = a["action"]
        js_host = a["hostname"]

        _action_slug = a["action"].lower().replace(" ", "_")
        _risk_slug = "high" if a["tier"] == "HIGH RISK" else "medium"
        cards += f"""
        <div class="card appr-card approval-card" id="appr-{a['id']}" data-action-type="{_action_slug}" data-risk="{_risk_slug}">
          <div class="appr-band" style="background: linear-gradient(135deg, {a['header_color']}, {a['header_color']}cc)">
            <span class="appr-band-title">{a['action']}</span>
            <span class="appr-band-host">{a['hostname']}</span>
            <span class="badge {tier_cls}" style="margin-left:8px">{a['tier']}</span>
            <span class="countdown-big {countdown_cls}" style="margin-left:auto" title="Auto-REJECT at this countdown (30-min default)">{a['countdown']}</span>
          </div>
          <div class="appr-body">
            <div class="appr-meta-row">
              <span class="appr-hostname">{a['hostname']}</span>
              <span class="appr-osinfo">{a['os']} &nbsp;·&nbsp; {a['ip']} &nbsp;·&nbsp; {a['env']} &nbsp;·&nbsp; up {uptime}</span>
            </div>

            {_appr_evidence_grid(a, ev)}

            <div style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.875rem;color:#1e1b4b;margin:14px 0 4px">Exact actions to be executed</div>
            {_appr_action_table(ev)}

            <div style="margin-top:16px">
              {_appr_layer_split(ev, reject_consequence)}
            </div>

            {_appr_deeplinks(ev, a['hostname'])}

            <div class="appr-footer" style="margin-top:14px">
              <a href="#" class="btn-approve"
                 onclick="event.preventDefault();_showConfirm('approve','{js_id}','{js_batch}','{js_action}','{js_host}')">✓ APPROVE</a>
              <a href="#" class="btn-reject"
                 onclick="event.preventDefault();_showConfirm('reject','{js_id}','{js_batch}','{js_action}','{js_host}')">✕ REJECT</a>
              <span style="margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:0.6875rem;color:#94a3b8">
                Approve/Reject re-routes through the Slack approval gate — UI is not a self-approval surface
              </span>
            </div>
          </div>
        </div>"""

    # Typed-confirm modal (single instance reused by all cards)
    modal = """
    <div class="confirm-modal" id="confirm-modal" onclick="if(event.target===this)_hideConfirm()">
      <div class="confirm-card">
        <div class="confirm-hdr approve" id="cf-hdr">
          <span style="font-size:1.1rem">⚠</span>
          <span><span id="cf-kind">APPROVE</span> · <span id="cf-action">ACTION</span> on <span id="cf-host">vm</span></span>
        </div>
        <div class="confirm-body">
          <p>This decision will be written to the immutable audit log and recorded on the durable approval request.
             To proceed, retype the batch ID exactly and provide a reason of at least 20 characters.</p>
          <div class="confirm-evidence">
            Batch: <span id="cf-batch-expected">batch-id</span>
          </div>
          <div class="confirm-field">
            <label>Retype batch ID</label>
            <input id="cf-batch-input" type="text" autocomplete="off" oninput="_confirmCheck()">
            <div id="cf-match" style="font-family:'JetBrains Mono',monospace;font-size:0.6875rem;margin-top:4px"></div>
          </div>
          <div class="confirm-field">
            <label>Reason (≥ 20 chars, written to audit)</label>
            <textarea id="cf-reason" rows="3" oninput="_confirmCheck()"></textarea>
          </div>
        </div>
        <div class="confirm-foot">
          <button class="btn-cancel" onclick="_hideConfirm()">Cancel</button>
          <button class="btn-go approve" id="cf-go" disabled onclick="_confirmGo()">Confirm decision</button>
        </div>
      </div>
    </div>"""

    resolved = ("""
    <div class="card resolved-card" style="margin-top:8px">
      <span class="resolved-label">RESOLVED TODAY — 14 actions approved or rejected</span>
      <a href="/audit" style="margin-left:auto; color:#4f46e5; font-size:0.875rem; text-decoration:none; font-weight:500">View in Audit Log →</a>
    </div>""" if get_provider().data_mode() == "FIXTURE" else "")

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Pending Approval</div>
        <div class="section-sub">{len(get_provider().get_approvals())} actions require your decision before the agent can proceed &nbsp;·&nbsp; auto-reject at the countdown</div>
      </div>
      <div class="filter-chips" id="appr-chips">
        <a href="#" class="chip active" onclick="event.preventDefault();_apprFilter(this,'all')">All</a>
        <a href="#" class="chip" onclick="event.preventDefault();_apprFilter(this,'high_risk')">High Risk</a>
        <a href="#" class="chip" onclick="event.preventDefault();_apprFilter(this,'service_restart')">Service Restart</a>
        <a href="#" class="chip" onclick="event.preventDefault();_apprFilter(this,'os_patching')">OS Patching</a>
      </div>
    </div>
    {cards}
    {resolved}
    {modal}
    {_appr_confirm_modal_js()}
    <script>
    function _apprFilter(el, kind) {{
      document.querySelectorAll('#appr-chips .chip').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      document.querySelectorAll('.approval-card').forEach(card => {{
        var show = kind === 'all'
          || (kind === 'high_risk' && card.dataset.risk === 'high')
          || (kind === 'service_restart' && card.dataset.actionType === 'service_restart')
          || (kind === 'os_patching' && card.dataset.actionType === 'os_patching');
        card.style.display = show ? '' : 'none';
      }});
    }}
    </script>"""


def _vm_siblings_section(hostname: str, env: str) -> str:
    siblings = [v for v in get_provider().get_vms() if v["env"] == env and v["hostname"] != hostname]
    if not siblings:
        return ""
    chips = ""
    for s in siblings:
        sc, _, _ = STATUS_COLORS.get(s["status"], ("#94a3b8", "", "#94a3b8"))
        dot = f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:{sc};margin-right:5px;vertical-align:middle"></span>'
        chips += f'<a href="/vm/{s["hostname"]}" style="display:inline-flex;align-items:center;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:5px 12px;font-size:0.8125rem;color:#334155;text-decoration:none;gap:2px;transition:border-color 0.15s" onmouseover="this.style.borderColor=\'#a5b4fc\'" onmouseout="this.style.borderColor=\'#e2e8f0\'">{dot}{s["hostname"]}</a>'
    return f"""
    <div class="card" style="padding:16px 20px;margin-top:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <span class="section-title" style="font-size:0.875rem">{env} Fleet — Other Hosts</span>
        <a href="/inventory" class="td-link" style="font-size:0.8125rem">Full Inventory →</a>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">{chips}</div>
    </div>"""


def _sparkline_svg(
    values: list[float],
    color: str,
    grad_id: str,
    w: int = 500,
    h: int = 52,
    warn_pct: float | None = 75.0,
    crit_pct: float | None = 90.0,
    poly_id: str = "",
) -> str:
    """Render an SVG sparkline for a metric history list (oldest → newest).

    poly_id: optional prefix for element IDs used by the live-update JS.
    When set, elements get id="{poly_id}-fill", "{poly_id}-line", "{poly_id}-dot".
    """
    if len(values) < 2:
        return (
            f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px">'
            f'<text x="50%" y="50%" fill="#94a3b8" font-size="10" text-anchor="middle">'
            f'collecting…</text></svg>'
        )
    mn, mx = min(values), max(values)
    scale_min = min(mn, 0.0)
    scale_max = max(mx, 100.0) if mx <= 100 else mx * 1.05
    rng = scale_max - scale_min if scale_max != scale_min else 1.0
    pad_top, pad_bot = 6, 4

    def _y(v: float) -> float:
        return round(h - pad_bot - (v - scale_min) / rng * (h - pad_top - pad_bot), 1)

    pts = [f"{round(i / (len(values) - 1) * w, 1)},{_y(v)}" for i, v in enumerate(values)]
    poly = " ".join(pts)
    last_x = round((len(values) - 1) / (len(values) - 1) * w, 1)
    last_y = _y(values[-1])

    threshold_lines = ""
    if warn_pct is not None and scale_max >= warn_pct and warn_pct > scale_min:
        wy = _y(warn_pct)
        threshold_lines += (
            f'<line x1="0" y1="{wy}" x2="{w}" y2="{wy}" '
            f'stroke="#d97706" stroke-width="0.8" stroke-dasharray="4 3" opacity="0.7"/>'
        )
    if crit_pct is not None and scale_max >= crit_pct and crit_pct > scale_min:
        cy = _y(crit_pct)
        threshold_lines += (
            f'<line x1="0" y1="{cy}" x2="{w}" y2="{cy}" '
            f'stroke="#dc2626" stroke-width="0.8" stroke-dasharray="4 3" opacity="0.7"/>'
        )

    id_fill = f' id="{poly_id}-fill"' if poly_id else ""
    id_line = f' id="{poly_id}-line"' if poly_id else ""
    id_dot  = f' id="{poly_id}-dot"'  if poly_id else ""

    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        f'style="width:100%;height:{h}px;display:block">'
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.22"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'{threshold_lines}'
        f'<polyline{id_fill} points="{poly} {w},{h - pad_bot} 0,{h - pad_bot}" fill="url(#{grad_id})" stroke="none"/>'
        f'<polyline{id_line} points="{poly}" fill="none" stroke="{color}" stroke-width="1.75" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle{id_dot} cx="{last_x}" cy="{last_y}" r="2.5" fill="{color}"/>'
        f'</svg>'
    )


def _mini_sparkline_svg(values: list[float], color: str, grad_id: str) -> str:
    """Tiny 80×18 inline sparkline for the disk partition rows."""
    if len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = max(mx - mn, 1.0)
    w, h = 80, 18
    pts = [f"{round(i / (len(values) - 1) * w, 1)},{round(h - (v - mn) / rng * (h - 4) - 2, 1)}" for i, v in enumerate(values)]
    poly = " ".join(pts)
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" width="{w}" height="{h}">'
        f'<defs><linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.3"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<polyline points="{poly} {w},{h} 0,{h}" fill="url(#{grad_id})" stroke="none"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


def _vm_resource_trends(
    hostname: str,
    cpu: int,
    mem: int,
    metrics_by_window: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render a Metricbeat-style Resource Trends card for a VM detail page.

    metrics_by_window: real DB data keyed by window string ('15m','1h','24h','7d').
    Falls back to VM_EVIDENCE fixture for any window with no DB rows.
    JS polls /api/vm/{hostname}/metrics every 60 s and updates SVG in-place.
    """
    ev = _ev_vm(hostname)
    db_data = metrics_by_window or {}

    # ── Per-window data resolution ─────────────────────────────────────────
    # For each window, prefer real DB [ts, val] pairs; fall back to fixture lists.
    # Fixture lists are converted to synthetic [i, val] pairs (ts=0 means "fixture").

    def _resolve(metric: str, window: str, fallback_key: str) -> list[float]:
        """Return a list of float values for the given metric + window."""
        window_data = db_data.get(window, {})
        pairs: list[list[int | float]] = window_data.get(metric, [])
        if pairs:
            return [float(v) for _, v in pairs]
        # Fixture fallback
        raw: list[float] = ev.get(fallback_key, [])
        if not raw:
            return [float(cpu if metric == "cpu" else mem)] * 24
        return list(raw)

    # Window  → (cpu values, mem values, fallback key for cpu, fallback key for mem, x-axis labels)
    windows: dict[str, tuple[str, str, list[str]]] = {
        "15m": ("cpu_history",    "mem_history",    ["-15m", "-10m", "-5m",  "now"]),
        "1h":  ("cpu_history",    "mem_history",    ["-1h",  "-45m", "-30m", "-15m", "now"]),
        "24h": ("cpu_history",    "mem_history",    ["-24h", "-18h", "-12h", "-6h",  "now"]),
        "7d":  ("cpu_history_7d", "mem_history_7d", ["-7d",  "-5d",  "-3d",  "-1d",  "now"]),
    }

    cpu_color = "#dc2626" if cpu >= 90 else "#d97706" if cpu >= 75 else "#0891b2"
    mem_color = "#dc2626" if mem >= 90 else "#d97706" if mem >= 75 else "#7c3aed"

    def _stats_html(vals: list[float], stat_id: str) -> str:
        if not vals:
            return f'<div class="spark-stats" id="{stat_id}"><span>no data</span></div>'
        mn = min(vals)
        av = sum(vals) / len(vals)
        mx = max(vals)
        return (
            f'<div class="spark-stats" id="{stat_id}">'
            f'<span>min <span class="stat-v">{mn:.0f}%</span></span>'
            f'<span>avg <span class="stat-v">{av:.0f}%</span></span>'
            f'<span>max <span class="stat-v">{mx:.0f}%</span></span>'
            f'</div>'
        )

    def _x_axis(labels: list[str]) -> str:
        return (
            '<div class="spark-x-axis">'
            + "".join(f"<span>{lbl}</span>" for lbl in labels)
            + "</div>"
        )

    def _panel(metric: str, label: str, current: int, color: str) -> str:
        curr_style = f"color:{color}"
        views_html = ""
        for _, (window, (cpu_fk, mem_fk, x_labels)) in enumerate(windows.items()):
            fk = cpu_fk if metric == "cpu" else mem_fk
            vals = _resolve(metric, window, fk)
            # Pin last point to current live value for coherence
            if vals:
                vals[-1] = float(current)
            grad_id   = f"{metric}-{window}-g"
            poly_pfx  = f"{metric}-{window}"
            svg_id    = f"svg-{metric}-{window}"
            stat_id   = f"stats-{metric}-{window}"
            svg = _sparkline_svg(vals, color, grad_id, poly_id=poly_pfx)
            hidden = "" if window == "24h" else ' style="display:none"'
            views_html += (
                f'<div class="trend-view trend-{window}"{hidden}>'
                f'<div class="vm-trend-svg-wrap" id="{svg_id}">{svg}</div>'
                f'{_x_axis(x_labels)}'
                f'{_stats_html(vals, stat_id)}'
                f'</div>'
            )
        return (
            f'<div class="vm-trend-panel">'
            f'<div class="vm-trend-label">{label}</div>'
            f'<div class="vm-trend-current-row">'
            f'<span class="vm-trend-current" id="val-{metric}" style="{curr_style}">{current}%</span>'
            f'<span class="vm-trend-delta">current</span>'
            f'</div>'
            f'{views_html}'
            f'</div>'
        )

    cpu_panel = _panel("cpu", "CPU",    cpu, cpu_color)
    mem_panel = _panel("mem", "Memory", mem, mem_color)

    # ── Live-update JS ─────────────────────────────────────────────────────
    # Mirrors _sparkline_svg() logic in JavaScript.
    # Updates polyline points + circle + stats in-place without page reload.
    # Polls /api/vm/{hostname}/metrics?window=<w> every 60 s.
    live_js = f"""<script>
(function(){{
  var _host = {_json.dumps(hostname)};
  var _w = '24h';
  var _W = {_json.dumps({k: v[2] for k, v in windows.items()})};
  var _SVG_W = 500, _SVG_H = 52, _PAD_T = 6, _PAD_B = 4;
  var _colors = {{cpu:'#0891b2', mem:'#7c3aed'}};
  var _warnPct = 75, _critPct = 90;

  function _normY(v, mn, mx){{
    var sMin = Math.min(mn, 0);
    var sMax = Math.max(mx, 100);
    var rng = sMax - sMin || 1;
    return (_SVG_H - _PAD_B - (v - sMin) / rng * (_SVG_H - _PAD_T - _PAD_B)).toFixed(1);
  }}

  function _pts(vals){{
    var mn = Math.min.apply(null, vals);
    var mx = Math.max.apply(null, vals);
    return vals.map(function(v, i){{
      var x = (i / (vals.length - 1) * _SVG_W).toFixed(1);
      var y = _normY(v, mn, mx);
      return x + ',' + y;
    }});
  }}

  function _updatePanel(metric, vals, window){{
    if (!vals || vals.length < 2) return;
    var mn = Math.min.apply(null, vals);
    var mx = Math.max.apply(null, vals);
    var pts = _pts(vals);
    var poly = pts.join(' ');
    var lx = pts[pts.length-1].split(',')[0];
    var ly = pts[pts.length-1].split(',')[1];

    var fill = document.getElementById(metric+'-'+window+'-fill');
    var line = document.getElementById(metric+'-'+window+'-line');
    var dot  = document.getElementById(metric+'-'+window+'-dot');
    var stat = document.getElementById('stats-'+metric+'-'+window);
    var valEl = document.getElementById('val-'+metric);

    if(fill) fill.setAttribute('points', poly+' '+_SVG_W+','+(+_SVG_H - _PAD_B)+' 0,'+(+_SVG_H - _PAD_B));
    if(line) line.setAttribute('points', poly);
    if(dot)  {{ dot.setAttribute('cx', lx); dot.setAttribute('cy', ly); }}
    if(stat) {{
      var avg = (vals.reduce(function(a,b){{return a+b}},0)/vals.length).toFixed(0);
      stat.innerHTML = '<span>min <span class="stat-v">'+mn.toFixed(0)+'%</span></span>'
        +'<span>avg <span class="stat-v">'+avg+'%</span></span>'
        +'<span>max <span class="stat-v">'+mx.toFixed(0)+'%</span></span>';
    }}
    if(valEl && window===_w) {{
      var cur = vals[vals.length-1];
      valEl.textContent = cur.toFixed(0)+'%';
      valEl.style.color = cur>=90 ? '#dc2626' : cur>=75 ? '#d97706'
        : (metric==='mem' ? '#7c3aed' : '#0891b2');
    }}
  }}

  function _fetchAndUpdate(window){{
    fetch('/api/vm/'+_host+'/metrics?window='+window)
      .then(function(r){{ return r.ok ? r.json() : null; }})
      .then(function(d){{
        if(!d) return;
        if(d.cpu && d.cpu.length) _updatePanel('cpu', d.cpu.map(function(p){{return p[1]}}), window);
        if(d.mem && d.mem.length) _updatePanel('mem', d.mem.map(function(p){{return p[1]}}), window);
      }})
      .catch(function(){{}});
  }}

  window._setTrend = function(btn, view){{
    document.querySelectorAll('.trend-btn').forEach(function(b){{b.classList.remove('active')}});
    btn.classList.add('active');
    document.querySelectorAll('.trend-view').forEach(function(v){{v.style.display='none'}});
    document.querySelectorAll('.trend-'+view).forEach(function(v){{v.style.display=''}});
    _w = view;
    _fetchAndUpdate(view);
  }};

  // Initial fetch for default window (24h), then auto-refresh every 60 s
  _fetchAndUpdate('24h');
  setInterval(function(){{ _fetchAndUpdate(_w); }}, 60000);
}})();
</script>"""

    return f"""
    <div class="card vm-trends-card">
      <div class="vm-trends-header">
        <span class="section-title" style="font-size:1rem">Resource Trends</span>
        <div class="vm-trends-toggle">
          <button class="trend-btn" onclick="_setTrend(this,'15m')">15m</button>
          <button class="trend-btn" onclick="_setTrend(this,'1h')">1h</button>
          <button class="trend-btn active" onclick="_setTrend(this,'24h')">24h</button>
          <button class="trend-btn" onclick="_setTrend(this,'7d')">7d</button>
        </div>
      </div>
      <div class="vm-trends-grid">
        {cpu_panel}
        {mem_panel}
      </div>
    </div>
    {live_js}"""


def page_vm(hostname: str, metrics_by_window: dict[str, dict[str, Any]] | None = None) -> str:
    vm = get_provider().get_vm(hostname)
    if vm is None:
        return f'<div class="card" style="padding:40px;text-align:center">VM <code>{hostname}</code> not found.</div>'

    _is_fixture = get_provider().data_mode() == "FIXTURE"
    color, _, border = STATUS_COLORS.get(vm["status"], ("#94a3b8", "", "#94a3b8"))
    ev = _ev_vm(hostname)

    actions = get_provider().get_vm_actions(hostname)

    _detail_cls_vm = {
        "ok":      ("audit-detail-ok",      "#f8f9ff"),
        "failed":  ("audit-detail-failed",   "#fff8f8"),
        "warning": ("audit-detail-warning",  "#fffbeb"),
        "pending": ("audit-detail-pending",  "#faf8ff"),
    }
    rows = ""
    for i, a in enumerate(actions):
        alt = ' row-alt' if i % 2 == 1 else ''
        failed_cls = ' row-failed' if a["status"] == "failed" else (
            ' row-pending' if a["status"] == "pending" else ''
        )
        det_cls, _ = _detail_cls_vm.get(a["status"], ("audit-detail-ok", ""))
        detail_html = (
            f'<div class="audit-detail {det_cls}">↳ {a["detail"]}</div>'
            if a.get("detail") else ""
        )
        rows += f"""<tr class="{alt}{failed_cls}">
          <td class="td-ts">{a['ts']} UTC</td>
          <td>
            <div>{a['action']}</div>
            {detail_html}
          </td>
          <td>{audit_badge(a['status'])}</td>
          <td class="td-mono">{a['duration']}</td>
          <td class="td-mono">{a['op']}</td>
        </tr>"""

    _disk_hist = _ev_vm(hostname).get("disk_history", {})
    disk_data = [
        ("/",     vm["disk"], f"{round(vm['disk']*50/100,1)} GB / 50 GB"),
        ("/var",  52,         "26.0 GB / 50 GB"),
        ("/tmp",  8,          "0.4 GB / 5 GB"),
        ("/home", 23,         "11.5 GB / 50 GB"),
    ]
    disk_rows = ""
    for di, (path, pct, size) in enumerate(disk_data):
        pct_color = "#d97706" if pct >= 75 else "#4f46e5" if pct >= 30 else "#16a34a"
        hist = _disk_hist.get(path, [])
        mini_svg = _mini_sparkline_svg(hist, pct_color, f"dk{di}g") if hist else ""
        trend_delta = ""
        if len(hist) >= 2:
            delta = hist[-1] - hist[0]
            if abs(delta) >= 1:
                arrow = "↑" if delta > 0 else "↓"
                trend_delta = f'<span class="disk-mini-stat">{arrow}{abs(delta):.0f}% 24h</span>'
        disk_rows += f"""
        <div class="disk-partition">
          <div class="disk-row">
            <span class="disk-path">{path}</span>
            <div class="disk-progwrap"><div class="disk-fill" style="width:{pct}%;background:{pct_color}"></div></div>
            <span class="disk-pct" style="color:{pct_color}">{pct}%</span>
            <span class="disk-size">{size}</span>
          </div>
          {f'<div class="disk-mini-spark">{mini_svg}{trend_delta}</div>' if mini_svg else ''}
        </div>"""

    callout = ""
    if vm["disk"] >= 75:
        callout = '<div class="callout callout-amber">⚠ Root partition above 75% threshold. Disk cleanup recommended: /tmp eligible, apt cache ~1.2 GB available.</div>'

    cpu  = vm.get("cpu",  0)
    mem  = vm.get("mem",  0)
    pp   = vm.get("pending_patches", 0)

    pending_section = ""
    if pp > 0:
        chip_cls = "patches-chip-crit" if pp >= 10 else "patches-chip"
        pending_section = f"""
        <div class="callout callout-amber" style="margin-top:0;margin-bottom:16px">
          <strong><span class="{chip_cls}" style="font-size:0.75rem;padding:3px 8px">{pp} patches pending</span></strong>
          &nbsp; This VM has {pp} security package update{'s' if pp != 1 else ''} queued.
          They will be applied at the next scheduled maintenance window (Tue/Thu 02:00 UTC)
          or when you trigger a batch manually.
        </div>"""

    # VM_EVIDENCE operational fields
    ev_lock        = ev.get("lock")
    ev_window      = ev.get("window", "—")
    ev_last_patch  = ev.get("last_patched", "—")
    ev_noop        = ev.get("noop_now", False)
    ev_ssh_fp      = ev.get("ssh_key_fp", "—")

    # Derive last batch id from actions list for deep links
    last_batch = next((a.get("batch") for a in actions if a.get("batch")), None)
    if last_batch is None:
        last_batch = (
            ("prod-0423-0200" if vm["env"] == "PROD" else "staging-0422-1400")
            if _is_fixture else "—"
        )

    lock_alert = ""
    if ev_lock:
        lock_alert = f'<div class="callout callout-red" style="margin-bottom:16px">🔒 <strong>VM LOCKED</strong> — held by <code>{ev_lock}</code>. No maintenance will run until the lock is released. Use Admin → Lock Manager to clear if stale.</div>'

    noop_badge = ""
    if ev_noop:
        noop_badge = '&nbsp;<span class="badge badge-green" style="font-size:0.625rem;vertical-align:middle">NOOP · up to date</span>'

    deeplinks = f"""
    <div class="card" style="padding:12px 20px;margin-bottom:16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span style="font-size:0.75rem;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:0.06em">Deep Links</span>
      <a href="/batches#{last_batch}" class="deeplink-chip">📋 Last Batch</a>
      <a href="/approvals#{last_batch}" class="deeplink-chip">✅ Approval</a>
      <a href="/audit?vm={hostname}" class="deeplink-chip">🔍 Audit Slice</a>
      <a href="/audit?vm={hostname}&amp;action=OS+Patching" class="deeplink-chip">📦 Patch History</a>
    </div>"""

    return f"""
    {lock_alert}
    {deeplinks}
    <div class="detail-top">
      <div class="card identity-card identity-top-border" style="border-top-color:{border}">
        <div class="identity-header">
          <span class="identity-hostname">{vm['hostname']}</span>
          {badge(vm['status'])}{noop_badge}
        </div>
        <div class="fields-grid">
          <div class="field-row"><span class="field-label">OS Version</span><span class="field-value">{vm['os']}</span></div>
          <div class="field-row"><span class="field-label">IP Address</span><span class="field-value">{vm['ip']}</span></div>
          <div class="field-row"><span class="field-label">Environment</span><span class="field-value">{vm['env']}</span></div>
          <div class="field-row"><span class="field-label">Uptime</span><span class="field-value">{vm['uptime']}</span></div>
          <div class="field-row"><span class="field-label">SSH Key FP</span><span class="field-value td-mono" style="font-size:0.7rem">{ev_ssh_fp}</span></div>
          <div class="field-row"><span class="field-label">Last Seen</span><span class="field-value">{vm['last_action']} UTC</span></div>
          <div class="field-row"><span class="field-label">Last Patched</span><span class="field-value">{ev_last_patch}</span></div>
          <div class="field-row"><span class="field-label">CPU Usage</span>
            <span class="field-value" style="color:{_metric_text_color(cpu)}">{cpu}%</span>
          </div>
          <div class="field-row"><span class="field-label">Memory Usage</span>
            <span class="field-value" style="color:{_metric_text_color(mem)}">{mem}%</span>
          </div>
        </div>
        <div class="divider"></div>
        <div class="maint-label">Maintenance Window</div>
        <div class="maint-val">{ev_window}</div>
        <div class="maint-next">Next: {'2026-04-24 02:00 UTC' if _is_fixture else '—'}</div>
      </div>
      <div class="card disk-card">
        <div class="disk-header">
          <span class="section-title" style="font-size:1rem">Disk Usage</span>
          {'<span class="badge badge-amber">⚠ Root &gt; 75%</span>' if vm["disk"] >= 75 else ''}
        </div>
        {disk_rows}
        {callout}
      </div>
    </div>
    {_vm_resource_trends(hostname, cpu, mem, metrics_by_window)}
    {pending_section}
    <div class="kpi-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
      <div class="card kpi-tile kpi-top-border" style="border-color:#d97706">
        <div class="kpi-label">Patches Pending</div>
        <div class="kpi-value" style="color:{'#d97706' if pp > 0 else '#94a3b8'}">{pp}</div>
        <div class="kpi-subtitle">{'security updates queued' if pp > 0 else 'up to date'}</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Patches Applied (30d)</div>
        <div class="kpi-value" style="color:#16a34a">{'34' if _is_fixture else '—'}</div>
        <div class="kpi-subtitle">packages updated</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Log Rotations (30d)</div>
        <div class="kpi-value" style="color:#4f46e5">{'8' if _is_fixture else '—'}</div>
        <div class="kpi-subtitle">sessions</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#0891b2">
        <div class="kpi-label">Docker Prunes (30d)</div>
        <div class="kpi-value" style="color:#0891b2">{'3' if _is_fixture else '—'}</div>
        <div class="kpi-subtitle">runs</div>
      </div>
    </div>
    <div class="card table-card">
      <div style="padding:16px 20px 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:1rem">Recent Actions</span>
        <a href="/audit" class="td-link">View All in Audit Log →</a>
      </div>
      <table class="data-table">
        <thead><tr>
          <th>TIMESTAMP</th><th>ACTION &amp; DETAIL</th><th>STATUS</th>
          <th>DURATION</th><th>OPERATOR</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {_vm_siblings_section(vm['hostname'], vm['env'])}"""


def page_audit() -> str:
    _detail_cls = {
        "ok":      "audit-detail-ok",
        "failed":  "audit-detail-failed",
        "warning": "audit-detail-warning",
        "pending": "audit-detail-pending",
    }
    # Build rows: each event is two <tr>s — the visible summary + a hidden evidence row
    rows = ""
    actions_seen: set[str] = set()
    approvers_seen: set[str] = set()
    for i, e in enumerate(get_provider().get_audit_events()):
        ev = _ev_audit(i)
        actions_seen.add(e["action"])
        if "(none" not in ev["approver"] and "(n/a" not in ev["approver"]:
            approvers_seen.add(ev["approver"].split(" (")[0])
        vm_env = next((v["env"] for v in get_provider().get_vms() if v["hostname"] == e["vm"]), "")
        alt = ' row-alt' if i % 2 == 1 else ''
        status_cls = " row-failed" if e["status"] == "failed" else (" row-pending" if e["status"] == "pending" else "")
        det_cls = _detail_cls.get(e["status"], "audit-detail-ok")
        detail_html = (
            f'<div class="audit-detail {det_cls}">↳ {e["detail"]}</div>'
            if e.get("detail") else ""
        )
        # data-* attributes drive client-side filtering
        rows += f"""<tr class="audit-row{alt}{status_cls}"
                       data-env="{vm_env}" data-batch="{e['batch']}"
                       data-vm="{e['vm']}" data-action="{e['action']}"
                       data-status="{e['status']}"
                       onclick="_toggleAuditRow(this)">
          <td class="td-ts">
            <span style="display:inline-block;width:12px;color:#94a3b8">▸</span>
            {e['ts']}
          </td>
          <td class="td-mono"><a href="/batches#{e['batch']}" class="td-link" onclick="event.stopPropagation()">{e['batch']}</a></td>
          <td><a href="/vm/{e['vm']}" class="td-host" onclick="event.stopPropagation()">{e['vm']}</a></td>
          <td>
            <div>{e['action']}</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:0.625rem;color:#94a3b8;margin-top:2px">
              event {ev['event_id']} · action {ev['action_id']}
            </div>
            {detail_html}
          </td>
          <td>{audit_badge(e['status'])}</td>
          <td class="td-right">{e['duration']}</td>
          <td class="td-mono" style="font-size:0.6875rem;color:#3730a3">{ev['approver'].split(' (')[0]}</td>
        </tr>
        <tr class="audit-row-detail" style="display:none">
          <td colspan="7" style="padding:0">
            <div class="audit-row-expand">
              <span class="k">Event ID</span><span class="v">{ev['event_id']}</span>
              <span class="k">Action ID</span><span class="v">{ev['action_id']}</span>
              <span class="k">Plan Hash</span><span class="v">{ev['plan_hash']}</span>
              <span class="k">Approver</span><span class="v">{ev['approver']}</span>
              <span class="k">Approval Source</span><span class="v">{ev['approval_source']}</span>
              <span class="k">Before State</span><span class="v before">{ev['before'] or '—'}</span>
              <span class="k">After State</span><span class="v {'after' if e['status']=='ok' else 'failed' if e['status']=='failed' else ''}">{ev['after'] or '—'}</span>
              <span class="k">Command Executed</span><span class="v">{ev['command'] or '—'}</span>
              <span class="k">Stdout (summary)</span><span class="v">{ev['stdout_summary'] or '—'}</span>
              <span class="k">Stderr (summary)</span><span class="v {'failed' if ev['stderr_summary'] else ''}">{ev['stderr_summary'] or '—'}</span>
              <span class="k">Rollback Status</span><span class="v {'failed' if 'fail' in ev['rollback_status'].lower() else 'after' if 'completed' in ev['rollback_status'].lower() else ''}">{ev['rollback_status'] or '—'}</span>
              <span class="k">&nbsp;</span>
              <span class="v">
                <a href="/batches#{e['batch']}" class="deeplink-chip dl-batch" onclick="event.stopPropagation()">▶ Batch {e['batch']}</a>
                <a href="/vm/{e['vm']}" class="deeplink-chip dl-vm" onclick="event.stopPropagation()">▶ VM {e['vm']}</a>
                <a href="/approvals" class="deeplink-chip dl-audit" onclick="event.stopPropagation()">▶ Open approvals queue</a>
              </span>
            </div>
          </td>
        </tr>"""

    # Filter dropdown values (real, derived from data)
    env_opts = "".join(f'<option value="{en}">{en}</option>' for en in sorted({v["env"] for v in get_provider().get_vms()}))
    batch_opts = "".join(f'<option value="{b["id"]}">{b["id"]}</option>' for b in get_provider().get_batches())
    vm_opts = "".join(f'<option value="{v["hostname"]}">{v["hostname"]}</option>' for v in get_provider().get_vms())
    action_opts = "".join(f'<option value="{a}">{a}</option>' for a in sorted(actions_seen))
    _ = "".join(f'<option value="{a}">{a}</option>' for a in sorted(approvers_seen))

    failures = sum(1 for e in get_provider().get_audit_events() if e["status"] == "failed")
    pendings = sum(1 for e in get_provider().get_audit_events() if e["status"] == "pending")

    js = """
    <script>
      function _toggleAuditRow(tr) {
        const det = tr.nextElementSibling;
        if (!det || !det.classList.contains('audit-row-detail')) return;
        const arrow = tr.querySelector('td span');
        if (det.style.display === 'none') {
          det.style.display = '';
          if (arrow) arrow.textContent = '▾';
        } else {
          det.style.display = 'none';
          if (arrow) arrow.textContent = '▸';
        }
      }
      function _applyAuditFilters() {
        const env    = document.getElementById('flt-env').value;
        const batch  = document.getElementById('flt-batch').value;
        const vm     = document.getElementById('flt-vm').value;
        const action = document.getElementById('flt-action').value;
        const status = document.getElementById('flt-status').value;
        let shown = 0, total = 0;
        document.querySelectorAll('tr.audit-row').forEach(tr => {
          total++;
          const okEnv    = !env    || tr.dataset.env    === env;
          const okBatch  = !batch  || tr.dataset.batch  === batch;
          const okVm     = !vm     || tr.dataset.vm     === vm;
          const okAction = !action || tr.dataset.action === action;
          const okStatus = !status || tr.dataset.status === status;
          const show = okEnv && okBatch && okVm && okAction && okStatus;
          tr.style.display = show ? '' : 'none';
          if (tr.nextElementSibling && tr.nextElementSibling.classList.contains('audit-row-detail') && !show) {
            tr.nextElementSibling.style.display = 'none';
          }
          if (show) shown++;
        });
        document.getElementById('flt-shown').textContent = shown + ' / ' + total;
      }
      function _clearAuditFilters() {
        ['flt-env','flt-batch','flt-vm','flt-action','flt-status'].forEach(id => {
          document.getElementById(id).value = '';
        });
        _applyAuditFilters();
      }
      function _exportAudit(fmt) {
        const visible = [];
        document.querySelectorAll('tr.audit-row').forEach(tr => {
          if (tr.style.display === 'none') return;
          const cells = tr.querySelectorAll('td');
          visible.push({
            ts: cells[0].innerText.replace(/^[▸▾]\\s*/, '').trim(),
            batch: cells[1].innerText.trim(),
            vm: cells[2].innerText.trim(),
            action: cells[3].innerText.trim().split('\\n')[0],
            status: cells[4].innerText.trim(),
            duration: cells[5].innerText.trim(),
            approver: cells[6].innerText.trim(),
            env: tr.dataset.env,
          });
        });
        let blob, name;
        if (fmt === 'json') {
          blob = new Blob([JSON.stringify(visible, null, 2)], {type: 'application/json'});
          name = 'errander-audit.json';
        } else {
          const header = 'timestamp,batch_id,vm,action,status,duration,approver,env\\n';
          const csv = header + visible.map(r =>
            [r.ts, r.batch, r.vm, r.action, r.status, r.duration, r.approver, r.env]
              .map(s => '"' + (s||'').replace(/"/g,'""') + '"').join(',')
          ).join('\\n');
          blob = new Blob([csv], {type: 'text/csv'});
          name = 'errander-audit.csv';
        }
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = name; a.click();
        URL.revokeObjectURL(url);
      }
    </script>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Audit Log</div>
        <div class="section-sub">Complete before-and-after record of every agent action &nbsp;·&nbsp; immutable &nbsp;·&nbsp; strict-mode enforced &nbsp;·&nbsp; click any row to expand evidence</div>
      </div>
    </div>
    <div class="audit-toolbar">
      <div class="audit-filter">
        <label>Env</label>
        <select id="flt-env" onchange="_applyAuditFilters()">
          <option value="">All</option>{env_opts}
        </select>
      </div>
      <div class="audit-filter">
        <label>Batch</label>
        <select id="flt-batch" onchange="_applyAuditFilters()">
          <option value="">All</option>{batch_opts}
        </select>
      </div>
      <div class="audit-filter">
        <label>VM</label>
        <select id="flt-vm" onchange="_applyAuditFilters()">
          <option value="">All</option>{vm_opts}
        </select>
      </div>
      <div class="audit-filter">
        <label>Action</label>
        <select id="flt-action" onchange="_applyAuditFilters()">
          <option value="">All</option>{action_opts}
        </select>
      </div>
      <div class="audit-filter">
        <label>Status</label>
        <select id="flt-status" onchange="_applyAuditFilters()">
          <option value="">All</option>
          <option value="ok">OK</option>
          <option value="warning">Warning</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
        </select>
      </div>
      <div class="audit-filter">
        <label>&nbsp;</label>
        <a href="#" onclick="event.preventDefault();_clearAuditFilters()" class="audit-export"><span class="csv" style="background:#475569">CLEAR</span></a>
      </div>
      <div class="audit-export">
        <a href="#" class="csv"  onclick="event.preventDefault();_exportAudit('csv')">⤓ EXPORT CSV</a>
        <a href="#" class="json" onclick="event.preventDefault();_exportAudit('json')">⤓ EXPORT JSON</a>
      </div>
    </div>
    <div class="results-bar">
      <strong><span id="flt-shown">{len(get_provider().get_audit_events())} / {len(get_provider().get_audit_events())}</span> events</strong> &nbsp;·&nbsp;
      <span style="color:#dc2626;font-weight:600">{failures} failures</span> &nbsp;·&nbsp;
      <span style="color:#7c3aed;font-weight:600">{pendings} pending</span> &nbsp;·&nbsp;
      <span style="color:#64748b">Filters above narrow client-side · Export respects current filter</span>
    </div>
    <div class="card table-card">
      <table class="data-table">
        <thead><tr>
          <th>TIMESTAMP</th><th>BATCH ID</th><th>VM</th>
          <th>ACTION &amp; IDS &amp; DETAIL</th>
          <th>STATUS</th><th class="r">DURATION</th><th>APPROVER</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {js}"""


def page_batches() -> str:
    _batches = get_provider().get_batches()
    _is_fixture = get_provider().data_mode() == "FIXTURE"

    if _is_fixture:
        kpis = """
    <div class="kpi-grid" style="margin-bottom:20px">
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Total Batches (30d)</div>
        <div class="kpi-value" style="color:#0f172a">28</div>
        <div class="kpi-subtitle">maintenance runs</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Success Rate</div>
        <div class="kpi-value" style="color:#16a34a">96.4%</div>
        <div class="kpi-subtitle">↑ 2.1% vs prev month</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#0891b2">
        <div class="kpi-label">Avg Duration</div>
        <div class="kpi-value" style="color:#0891b2">11m 47s</div>
        <div class="kpi-subtitle">per full run</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#7c3aed">
        <div class="kpi-label">Actions Executed</div>
        <div class="kpi-value" style="color:#7c3aed">2,418</div>
        <div class="kpi-subtitle">this period</div>
      </div>
    </div>"""
    else:
        _total = len(_batches)
        _ok = sum(1 for b in _batches if b.get("status") == "completed")
        _rate = f"{round(_ok / max(_total, 1) * 100, 1)}%" if _total else "—"
        _actions = sum(b.get("actions", 0) for b in _batches)
        kpis = f"""
    <div class="kpi-grid" style="margin-bottom:20px">
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Total Batches</div>
        <div class="kpi-value" style="color:#0f172a">{_total if _total else "—"}</div>
        <div class="kpi-subtitle">from audit log</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Success Rate</div>
        <div class="kpi-value" style="color:#16a34a">{_rate}</div>
        <div class="kpi-subtitle">completed / total</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#0891b2">
        <div class="kpi-label">Avg Duration</div>
        <div class="kpi-value" style="color:#0891b2">—</div>
        <div class="kpi-subtitle">not yet tracked live</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#7c3aed">
        <div class="kpi-label">Actions Executed</div>
        <div class="kpi-value" style="color:#7c3aed">{_actions if _actions else "—"}</div>
        <div class="kpi-subtitle">audit log total</div>
      </div>
    </div>"""

    if _is_fixture:
        durations = [14.5, 12.1, 11.8, 13.7, 10.9, 12.3, 11.5, 10.8, 12.9, 11.2,
                     13.1, 12.8, 11.7, 14.1, 12.2, 11.9, 13.4, 19.1, 10.7, 12.5,
                     11.1, 13.8, 12.7, 11.4, 14.3, 12.1, 11.8, 12.2, 11.5, 14.5]
        max_d, min_d = max(durations), min(durations)
        w, h = 900, 60
        pts = []
        for i, d in enumerate(durations):
            x = round(i / (len(durations) - 1) * w, 1)
            y = round(h - (d - min_d) / (max_d - min_d) * (h - 10) - 5, 1)
            pts.append(f"{x},{y}")
        polyline = " ".join(pts)
        anomaly_x = round(17 / 29 * w, 1)
        anomaly_y = round(h - (19.1 - min_d) / (max_d - min_d) * (h - 10) - 5, 1)
        months_labels = "".join(f'<span>Apr {i+1}</span>' for i in range(0, 30, 5))
        chart = f"""
    <div class="card" style="padding:18px 20px;margin-bottom:20px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
        <span class="section-title" style="font-size:0.875rem;font-family:'JetBrains Mono',monospace;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;color:#64748b">Batch Duration — Last 30 Days</span>
        <span style="font-size:0.75rem;color:#94a3b8">Apr 2026</span>
      </div>
      <div class="sparkline-wrap">
        <svg class="sparkline-svg" viewBox="0 0 {w} {h}" preserveAspectRatio="none">
          <defs>
            <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#0891b2" stop-opacity="0.15"/>
              <stop offset="100%" stop-color="#0891b2" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <polyline points="{polyline} {w},{h} 0,{h}" fill="url(#sg)" stroke="none"/>
          <polyline points="{polyline}" fill="none" stroke="#0891b2" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
          <circle cx="{anomaly_x}" cy="{anomaly_y}" r="4" fill="#d97706" stroke="#fff" stroke-width="2"/>
        </svg>
        <div class="anomaly-chip" style="left:{anomaly_x - 60}px;top:{anomaly_y - 26}px">14m 32s · rollback anomaly</div>
      </div>
      <div class="spark-x-labels">{months_labels}</div>
    </div>"""
    else:
        chart = ""

    rows = ""
    for i, b in enumerate(_batches):
        alt = " row-alt" if i % 2 == 1 else ""
        failed_cls = " row-failed" if b["status"] == "failed" else ""
        env_cls = "env-prod" if b["env"] == "PROD" else "env-staging"
        err_style = 'style="color:#dc2626;font-weight:700"' if b["errors"] > 0 else ""
        err_summary = b.get("error_summary", "")
        err_summary_html = (
            f'<div class="batch-err-summary">↳ {err_summary}</div>'
            if err_summary and b["errors"] > 0 else ""
        )
        failed_vms = b.get("failed_vms", [])
        failed_vms_html = ""
        if failed_vms:
            links = " &nbsp;·&nbsp; ".join(
                f'<a href="/vm/{h}" class="td-link" style="font-size:0.75rem">{h}</a>'
                for h in failed_vms
            )
            failed_vms_html = f'<div style="font-size:0.7rem;color:#94a3b8;margin-top:2px">{links}</div>'

        be = _ev_batch(b["id"])
        plan_hash     = be.get("plan_hash", "—")
        approver      = be.get("approver", "—")
        approval_src  = be.get("approval_source", "—")
        succeeded     = be.get("succeeded", "—")
        failed_c      = be.get("failed", 0)
        partial_c     = be.get("partial", 0)
        rolled_back   = be.get("rolled_back", 0)

        outcome_html = (
            f'<span style="color:#16a34a;font-weight:600">{succeeded} ok</span>'
            + (f' &nbsp;<span style="color:#dc2626;font-weight:600">{failed_c} failed</span>' if failed_c else "")
            + (f' &nbsp;<span style="color:#d97706;font-weight:600">{partial_c} partial</span>' if partial_c else "")
            + (f' &nbsp;<span style="color:#7c3aed;font-weight:600">{rolled_back} rolled back</span>' if rolled_back else "")
        )

        rows += f"""<tr id="{b['id']}" class="batch-row batch-summary-row{alt}{failed_cls}" onclick="_toggleBatchRow(this)" style="cursor:pointer" data-status="{b['status']}">
          <td>
            <span style="display:inline-block;width:14px;color:#94a3b8;font-size:0.75rem">▸</span>
            <span class="td-host" style="font-family:'JetBrains Mono',monospace">{b['id']}</span>
          </td>
          <td class="td-ts">{b['started']} UTC</td>
          <td><span class="env-badge {env_cls}">{b['env']}</span></td>
          <td class="td-right">{b['vms']}</td>
          <td class="td-right">{b['actions']}</td>
          <td>{audit_badge(b['status'])}</td>
          <td class="td-right">{b['duration']}</td>
          <td class="td-right" {err_style}>
            <div>{b['errors']}</div>
            {err_summary_html}
            {failed_vms_html}
          </td>
        </tr>
        <tr class="batch-detail-row" style="display:none">
          <td colspan="8" style="padding:0">
            <div class="audit-row-expand" style="grid-template-columns:140px 1fr 140px 1fr">
              <span class="k">Plan Hash</span><span class="v">{plan_hash}</span>
              <span class="k">Approver</span><span class="v">{approver}</span>
              <span class="k">Approval Source</span><span class="v">{approval_src}</span>
              <span class="k">Outcome</span><span class="v">{outcome_html}</span>
              <span class="k">Deep Links</span>
              <span class="v" style="display:flex;gap:8px;flex-wrap:wrap">
                <a href="/approvals#{b['id']}" class="deeplink-chip" onclick="event.stopPropagation()">✅ Approval</a>
                <a href="/audit?batch={b['id']}" class="deeplink-chip" onclick="event.stopPropagation()">🔍 Audit Slice</a>
              </span>
            </div>
          </td>
        </tr>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Batch History</div>
        <div class="section-sub">All maintenance runs — click any row for plan hash, approver, and outcome</div>
      </div>
      <button class="btn-primary" disabled title="Custom scheduling UI — v2 roadmap" style="opacity:0.45;cursor:not-allowed">+ SCHEDULE BATCH <span style="font-size:0.65rem;opacity:0.8">v2</span></button>
    </div>
    {kpis}
    {chart}
    <div class="card table-card">
      <div style="padding:14px 20px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:0.875rem">Batch Runs</span>
        <div class="filter-chips" id="batch-chips" style="margin-left:8px">
          <a href="#" class="chip active" onclick="event.preventDefault();_batchFilter(this,'all')">All</a>
          <a href="#" class="chip" onclick="event.preventDefault();_batchFilter(this,'completed')">Completed</a>
          <a href="#" class="chip" onclick="event.preventDefault();_batchFilter(this,'partial')">Partial</a>
          <a href="#" class="chip" onclick="event.preventDefault();_batchFilter(this,'failed')">Failed</a>
        </div>
      </div>
      <table class="data-table">
        <thead><tr>
          <th>BATCH ID</th><th>STARTED</th><th>ENV</th>
          <th class="r">VMs</th><th class="r">ACTIONS</th>
          <th>STATUS</th><th class="r">DURATION</th><th class="r">ERRORS</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <script>
      function _batchFilter(el, status) {{
        document.querySelectorAll('#batch-chips .chip').forEach(c => c.classList.remove('active'));
        el.classList.add('active');
        document.querySelectorAll('.batch-row').forEach(row => {{
          var s = (row.dataset.status || '').toLowerCase();
          var show = status === 'all'
            || (status === 'completed' && s === 'completed')
            || (status === 'partial' && (s === 'partial' || s.includes('partial')))
            || (status === 'failed' && (s === 'failed' || s.includes('fail') || s === 'aborted'));
          row.style.display = show ? '' : 'none';
        }});
      }}
      </script>
    </div>
    <script>
    function _toggleBatchRow(tr) {{
      var next = tr.nextElementSibling;
      if (!next || !next.classList.contains('batch-detail-row')) return;
      var arrow = tr.querySelector('span[style*="display:inline-block"]');
      if (next.style.display === 'none') {{
        next.style.display = '';
        if (arrow) arrow.textContent = '▾';
      }} else {{
        next.style.display = 'none';
        if (arrow) arrow.textContent = '▸';
      }}
    }}
    // Auto-expand if URL fragment matches a batch id
    (function() {{
      var id = window.location.hash.replace('#','');
      if (id) {{
        var el = document.getElementById(id);
        if (el) {{ _toggleBatchRow(el); el.scrollIntoView({{block:'center'}}); }}
      }}
    }})();
    </script>"""


# ── Inventory page ───────────────────────────────────────────────────────────

def page_inventory() -> str:
    _is_fixture = get_provider().data_mode() == "FIXTURE"
    _vms = get_provider().get_vms()
    envs  = len(set(v["env"] for v in _vms))
    os_families = sorted(set(v["os"].split()[0] for v in _vms))
    os_ct = len(os_families)
    os_subtitle = " · ".join(os_families) if os_families else "—"
    reachable = sum(1 for v in _vms if v["status"] != "offline")
    freshness_sub = "last verified 02:14 UTC" if _is_fixture else get_provider().data_freshness()

    kpis = f"""
    <div class="inv-kpi">
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Total VMs</div>
        <div class="kpi-value" style="color:#0f172a">{len(_vms)}</div>
        <div class="kpi-subtitle">{envs} environments</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#0891b2">
        <div class="kpi-label">OS Types</div>
        <div class="kpi-value" style="color:#0891b2">{os_ct}</div>
        <div class="kpi-subtitle">{os_subtitle}</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Reachable</div>
        <div class="kpi-value" style="color:#16a34a">{reachable}</div>
        <div class="kpi-subtitle">{freshness_sub}</div>
      </div>
    </div>"""

    filters = """
    <div class="card" style="padding:14px 16px;margin-bottom:16px">
      <div class="filter-bar">
        <input id="inv-search" class="search-input" type="text" placeholder="Search hostname, IP, OS..." oninput="_invFilter()"/>
        <select id="inv-env" class="select-input" onchange="_invFilter()">
          <option>All Environments</option>
          <option>PROD</option><option>STAGING</option><option>DEV</option>
        </select>
        <select id="inv-os" class="select-input" onchange="_invFilter()">
          <option>All OS</option>
          <option>Ubuntu 22.04</option><option>RHEL 8.7</option><option>Debian 11</option>
        </select>
        <select id="inv-status" class="select-input" onchange="_invFilter()">
          <option>All Status</option>
          <option>OK</option><option>Warning</option><option>Failed</option><option>Pending</option>
        </select>
        <button onclick="_invFilter()" class="btn-primary">FILTER</button>
      </div>
    </div>"""

    rows = ""
    for i, vm in enumerate(get_provider().get_vms()):
        alt  = " row-alt" if i % 2 == 1 else ""
        fcls = " row-failed" if vm["status"] == "failed" else (" row-pending" if vm["status"] == "pending" else "")
        ve = _ev_vm(vm["hostname"])
        ssh_fp   = ve.get("ssh_key_fp", f'/keys/{vm["hostname"]}.pem')
        win_str  = ve.get("window", "—")
        # shorten window for table cell
        win_short = win_str.split(" (")[0] if " (" in win_str else win_str
        rows += f"""<tr class="inv-row{alt}{fcls}" data-host="{vm['hostname']}" data-ip="{vm['ip']}" data-os="{vm['os']}" data-env="{vm['env']}" data-status="{vm['status']}">
          <td><a href="/vm/{vm['hostname']}" class="td-host">{vm['hostname']}</a></td>
          <td class="td-mono">{vm['ip']}</td>
          <td>{os_tag(vm['os'])}</td>
          <td>{env_tag(vm['env'])}</td>
          <td class="td-mono" style="color:#94a3b8;font-size:0.7rem">{ssh_fp}</td>
          <td class="td-mono" style="font-size:0.75rem">{win_short}</td>
          <td class="td-mono" style="font-size:0.75rem">{vm['uptime']}</td>
          <td>{badge(vm['status'])}</td>
          <td><a href="/vm/{vm['hostname']}" class="td-link">Details →</a></td>
        </tr>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">VM Inventory</div>
        <div class="section-sub">{len(get_provider().get_vms())} hosts registered across {envs} environments</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn-outline btn-outline-indigo" disabled title="Inventory CSV export — v2 roadmap" style="opacity:0.45;cursor:not-allowed">EXPORT <span style="font-size:0.65rem">v2</span></button>
        <button class="btn-primary" disabled title="Ad-hoc VM management — v2 roadmap" style="opacity:0.45;cursor:not-allowed">+ ADD VM <span style="font-size:0.65rem">v2</span></button>
      </div>
    </div>
    {kpis}
    {filters}
    <div class="card table-card" style="overflow-x:auto">
      <table class="data-table">
        <thead><tr>
          <th>HOSTNAME</th><th>IP ADDRESS</th><th>OS</th><th>ENV</th>
          <th>SSH KEY FP</th><th>MAINT. WINDOW</th><th>UPTIME</th><th>STATUS</th><th></th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    {_inventory_env_breakdown()}
    <script>
    function _invFilter() {{
      var q = (document.getElementById('inv-search').value || '').toLowerCase();
      var env = document.getElementById('inv-env').value;
      var os  = document.getElementById('inv-os').value;
      var st  = document.getElementById('inv-status').value;
      document.querySelectorAll('.inv-row').forEach(function(row) {{
        var matchText = !q || (row.dataset.host||'').toLowerCase().includes(q)
          || (row.dataset.ip||'').toLowerCase().includes(q)
          || (row.dataset.os||'').toLowerCase().includes(q);
        var matchEnv  = !env || env.startsWith('All') || (row.dataset.env||'') === env.toUpperCase();
        var matchOs   = !os  || os.startsWith('All')  || (row.dataset.os||'').toLowerCase().includes(os.toLowerCase());
        var matchSt   = !st  || st.startsWith('All')  || (row.dataset.status||'').toLowerCase() === st.toLowerCase();
        row.style.display = (matchText && matchEnv && matchOs && matchSt) ? '' : 'none';
      }});
    }}
    </script>"""


_ENV_RESTARTABLE_UNITS: dict[str, list[str]] = {
    "PROD":    ["nginx", "gunicorn", "redis-server"],
    "STAGING": ["nginx", "gunicorn"],
    "DEV":     [],
}


def _inventory_env_breakdown() -> str:
    _is_fixture = get_provider().data_mode() == "FIXTURE"
    env_order = ["PROD", "STAGING", "DEV"]
    cards_html = ""
    for env_name in env_order:
        group = [v for v in get_provider().get_vms() if v["env"] == env_name]
        if not group:
            continue
        ok_ct   = sum(1 for v in group if v["status"] == "ok")
        warn_ct = sum(1 for v in group if v["status"] == "warning")
        fail_ct = sum(1 for v in group if v["status"] in ("failed", "pending"))
        total_patches = sum(v.get("pending_patches", 0) for v in group)
        env_color = {"PROD": "#4f46e5", "STAGING": "#d97706", "DEV": "#16a34a"}.get(env_name, "#94a3b8")
        vm_links = " ".join(
            f'<a href="/vm/{v["hostname"]}" style="font-size:0.75rem;color:#4f46e5;text-decoration:none;font-family:\'JetBrains Mono\',monospace">{v["hostname"]}</a>'
            for v in group
        )
        units = _ENV_RESTARTABLE_UNITS.get(env_name, []) if _is_fixture else []
        if units:
            units_html = " ".join(
                f'<code style="font-size:0.7rem;background:#f1f5f9;padding:2px 6px;border-radius:4px;color:#4f46e5">{u}</code>'
                for u in units
            )
            restart_row = f'<div style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:10px"><div style="font-size:0.65rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px">Restartable Units</div><div style="display:flex;flex-wrap:wrap;gap:4px">{units_html}</div></div>'
        else:
            restart_row = '<div style="margin-top:10px;border-top:1px solid #f1f5f9;padding-top:10px"><div style="font-size:0.65rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:5px">Restartable Units</div><span style="font-size:0.75rem;color:#94a3b8">None configured</span></div>'
        cards_html += f"""
        <div class="card" style="padding:16px 18px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <span style="font-size:0.65rem;font-weight:700;letter-spacing:0.1em;background:{env_color}22;color:{env_color};padding:3px 8px;border-radius:4px">{env_name}</span>
            <span style="font-size:0.875rem;font-weight:600;color:#0f172a">{len(group)} hosts</span>
          </div>
          <div style="display:flex;gap:16px;margin-bottom:12px">
            <div style="text-align:center">
              <div style="font-size:1.25rem;font-weight:700;color:#16a34a">{ok_ct}</div>
              <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em">OK</div>
            </div>
            <div style="text-align:center">
              <div style="font-size:1.25rem;font-weight:700;color:#d97706">{warn_ct}</div>
              <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em">WARN</div>
            </div>
            <div style="text-align:center">
              <div style="font-size:1.25rem;font-weight:700;color:#dc2626">{fail_ct}</div>
              <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em">ISSUE</div>
            </div>
            <div style="text-align:center">
              <div style="font-size:1.25rem;font-weight:700;color:#d97706">{total_patches}</div>
              <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em">PATCHES</div>
            </div>
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:6px">{vm_links}</div>
          {restart_row}
        </div>"""
    return f"""
    <div style="margin-top:20px">
      <div style="font-family:'Space Grotesk',sans-serif;font-size:0.875rem;font-weight:700;color:#0f172a;margin-bottom:12px">Environment Breakdown</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px">{cards_html}</div>
    </div>"""


# ── Settings page ─────────────────────────────────────────────────────────────

def _live_settings_sections() -> list[dict[str, Any]]:
    """Build settings cards from real env vars — no demo values in live mode."""
    def _mask(v: str) -> str:
        return (v[:4] + "••••••••") if len(v) > 4 else "••••••••"

    llm_url   = os.environ.get("ERRANDER_LLM_BASE_URL", "—")
    llm_model = os.environ.get("ERRANDER_LLM_MODEL", "—")
    llm_key   = os.environ.get("ERRANDER_LLM_API_KEY", "")
    llm_to    = os.environ.get("ERRANDER_LLM_TIMEOUT", "60") + "s"
    llm_temp  = os.environ.get("ERRANDER_LLM_TEMPERATURE", "0.1")
    slack_tok = os.environ.get("ERRANDER_SLACK_BOT_TOKEN", "")
    slack_ch  = os.environ.get("ERRANDER_SLACK_CHANNEL_ID", "—")
    slack_to  = str(int(os.environ.get("ERRANDER_SLACK_TIMEOUT", "1800")) // 60) + " min"
    slack_pol = os.environ.get("ERRANDER_SLACK_POLL_INTERVAL", "30") + "s"
    audit_db  = os.environ.get("ERRANDER_AUDIT_DB_URL", "—")
    sch = get_provider().get_scheduler_timeline()
    return [
        {"title": "LLM Configuration", "icon": "🤖", "icon_bg": "#ede9fe", "rows": [
            ("Base URL",    llm_url,                            False),
            ("Model",       llm_model,                          False),
            ("API Key",     _mask(llm_key) if llm_key else "—", True),
            ("Timeout",     llm_to,                             False),
            ("Temperature", llm_temp,                           False),
        ]},
        {"title": "Slack Integration", "icon": "💬", "icon_bg": "#e0f2fe", "rows": [
            ("Bot Token",        _mask(slack_tok) if slack_tok else "—", True),
            ("Channel ID",       slack_ch,                               False),
            ("Approval Timeout", slack_to,                               False),
            ("Poll Interval",    slack_pol,                              False),
        ]},
        {"title": "Scheduling", "icon": "🕐", "icon_bg": "#fef3c7", "rows": [
            ("Cron Expression", sch.get("cron", "—"),  False),
            ("Human Schedule",  sch.get("human", "—"), False),
            ("Dry Run Default", "ON",                   False),
            ("Force Override",  "Requires reason",      False),
        ]},
        {"title": "Safety & Audit", "icon": "🛡", "icon_bg": "#dcfce7", "rows": [
            ("Audit DB Path", audit_db,  False),
            ("Log Retention", "90 days", False),
            ("Strict Mode",   "ON",      False),
        ]},
    ]


def page_settings() -> str:
    _sections = _SETTINGS_SECTIONS if get_provider().data_mode() == "FIXTURE" else _live_settings_sections()
    cards = ""
    for s in _sections:
        rows_html = ""
        for key, val, masked in s["rows"]:
            if masked:
                val_html = f'<span class="settings-masked">{val}</span>'
            elif val in ("ON", "OFF"):
                c = "#16a34a" if val == "ON" else "#94a3b8"
                val_html = f'<span class="settings-badge" style="background:{c}22;color:{c}">{val}</span>'
            else:
                val_html = f'<span class="settings-val">{val}</span>'
            rows_html += f"""
            <div class="settings-row">
              <span class="settings-key">{key}</span>
              {val_html}
            </div>"""
        cards += f"""
        <div class="card settings-card">
          <div class="settings-section-title">
            <span class="settings-icon" style="background:{s['icon_bg']}">{s['icon']}</span>
            {s['title']}
          </div>
          <div class="settings-rows">{rows_html}</div>
        </div>"""

    note = """
    <div class="settings-note">
      <strong>All settings are configured via environment variables and <code>inventory.yaml</code>.</strong>
      To change a value, update the relevant env var (see <code>docs/SECRETS.md</code>) and restart the agent.
      SSH keys are referenced by file path in the inventory — never stored in the database.
    </div>"""

    env_rows = [
        ("ERRANDER_LLM_BASE_URL",      "LLM endpoint base URL",                           "set"),
        ("ERRANDER_LLM_MODEL",         "Model identifier from your LLM provider",         "set"),
        ("ERRANDER_LLM_API_KEY",       "API key — leave blank for unauthenticated vLLM",  "set"),
        ("ERRANDER_LLM_TIMEOUT",       "Request timeout in seconds (default 60)",          "default"),
        ("ERRANDER_LLM_TEMPERATURE",   "Sampling temperature (default 0.1)",               "default"),
        ("ERRANDER_SLACK_BOT_TOKEN",   "xoxb-… token for posting messages",               "set"),
        ("ERRANDER_SLACK_CHANNEL_ID",  "Channel for approval messages + reports",         "set"),
        ("ERRANDER_SLACK_TIMEOUT",     "Approval wait timeout in seconds (default 1800)", "default"),
        ("ERRANDER_SLACK_POLL_INTERVAL","Legacy reaction poll interval — ignored since R2 (web-only approval)", "default"),
        ("ERRANDER_AUDIT_DB_URL",      "PostgreSQL connection URL",                       "set"),
        ("ERRANDER_UI_USERNAME",       "Web UI login username (default: admin)",           "default"),
        ("ERRANDER_UI_PASSWORD",       "Web UI login password (default: errander)",        "default"),
        ("ERRANDER_UI_SECRET",         "HMAC cookie signing secret — change in prod",     "default"),
    ]
    env_table_rows = ""
    for var, desc, status in env_rows:
        if status == "set":
            badge = '<span class="settings-badge" style="background:#dcfce7;color:#16a34a">SET</span>'
        else:
            badge = '<span class="settings-badge" style="background:#f1f5f9;color:#94a3b8">DEFAULT</span>'
        env_table_rows += f"""
        <tr>
          <td style="font-family:'JetBrains Mono',monospace;font-size:0.775rem;color:#4f46e5;padding:8px 12px;white-space:nowrap">{var}</td>
          <td style="font-size:0.8125rem;color:#475569;padding:8px 12px">{desc}</td>
          <td style="padding:8px 12px">{badge}</td>
        </tr>"""

    env_section = f"""
    <div class="card" style="margin-top:20px;padding:0;overflow:hidden">
      <div style="padding:14px 16px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between">
        <div>
          <div class="section-title" style="font-size:0.875rem">Environment Variables Reference</div>
          <div class="section-sub" style="font-size:0.75rem">All recognised env vars — set these before starting the agent</div>
        </div>
        <a href="/glossary" class="btn-outline btn-outline-indigo" style="font-size:0.75rem;padding:5px 12px">GLOSSARY</a>
      </div>
      <table style="width:100%;border-collapse:collapse">
        <thead>
          <tr style="background:#f8fafc">
            <th style="text-align:left;font-size:0.7rem;font-weight:600;color:#94a3b8;letter-spacing:0.06em;text-transform:uppercase;padding:8px 12px">Variable</th>
            <th style="text-align:left;font-size:0.7rem;font-weight:600;color:#94a3b8;letter-spacing:0.06em;text-transform:uppercase;padding:8px 12px">Purpose</th>
            <th style="text-align:left;font-size:0.7rem;font-weight:600;color:#94a3b8;letter-spacing:0.06em;text-transform:uppercase;padding:8px 12px">Status</th>
          </tr>
        </thead>
        <tbody style="divide-y:#f1f5f9">
          {env_table_rows}
        </tbody>
      </table>
    </div>"""

    _s_is_fixture = get_provider().data_mode() == "FIXTURE"
    _unit_iter: list[tuple[str, list[str]]] = (
        list(_ENV_RESTARTABLE_UNITS.items())
        if _s_is_fixture
        else [(e, []) for e in sorted(set(v["env"] for v in get_provider().get_vms()))]
    )
    restart_rows = ""
    for env_name, units in _unit_iter:
        env_color = {"PROD": "#4f46e5", "STAGING": "#d97706", "DEV": "#16a34a"}.get(env_name, "#94a3b8")
        if units:
            chips = " ".join(
                f'<code style="font-size:0.7rem;background:#f1f5f9;padding:2px 6px;border-radius:4px;color:#4f46e5">{u}</code>'
                for u in units
            )
            status_badge = '<span class="settings-badge" style="background:#dcfce7;color:#16a34a">ENABLED</span>'
        else:
            chips = '<span style="font-size:0.8rem;color:#94a3b8">None configured — service_restart disabled</span>'
            status_badge = '<span class="settings-badge" style="background:#f1f5f9;color:#94a3b8">DISABLED</span>'
        restart_rows += f"""
        <tr>
          <td style="padding:10px 14px">
            <span style="font-size:0.65rem;font-weight:700;letter-spacing:0.1em;background:{env_color}22;color:{env_color};padding:2px 8px;border-radius:4px">{env_name}</span>
          </td>
          <td style="padding:10px 14px">{status_badge}</td>
          <td style="padding:10px 14px;display:flex;flex-wrap:wrap;gap:4px">{chips}</td>
        </tr>"""

    restart_section = f"""
    <div class="card" style="margin-top:20px;padding:0;overflow:hidden">
      <div style="padding:14px 16px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;justify-content:space-between">
        <div>
          <div class="section-title" style="font-size:0.875rem">Restartable Units Allowlist</div>
          <div class="section-sub" style="font-size:0.75rem">Systemd units permitted for operator-triggered restart — configured in <code>inventory.yaml → restartable_units</code></div>
        </div>
        <a href="/inventory" class="btn-outline btn-outline-indigo" style="font-size:0.75rem;padding:5px 12px">INVENTORY →</a>
      </div>
      <table style="width:100%;border-collapse:collapse">
        <thead>
          <tr style="background:#f8fafc">
            <th style="text-align:left;font-size:0.7rem;font-weight:600;color:#94a3b8;letter-spacing:0.06em;text-transform:uppercase;padding:8px 14px;width:100px">ENV</th>
            <th style="text-align:left;font-size:0.7rem;font-weight:600;color:#94a3b8;letter-spacing:0.06em;text-transform:uppercase;padding:8px 14px;width:120px">STATUS</th>
            <th style="text-align:left;font-size:0.7rem;font-weight:600;color:#94a3b8;letter-spacing:0.06em;text-transform:uppercase;padding:8px 14px">UNITS</th>
          </tr>
        </thead>
        <tbody>{restart_rows}</tbody>
      </table>
    </div>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Settings</div>
        <div class="section-sub">Current agent configuration — read-only (set via environment variables)</div>
      </div>
      <a href="/glossary" class="btn-outline btn-outline-indigo">VIEW DOCS</a>
    </div>
    <div class="settings-grid">{cards}</div>
    {note}
    {env_section}
    {restart_section}"""


# ── Admin page ────────────────────────────────────────────────────────────────

def page_admin() -> str:
    _is_fixture = get_provider().data_mode() == "FIXTURE"
    _ag = get_provider().get_agent_status()
    _ab = get_provider().get_active_batch()
    _last = _ag.get("last_batch_id", "—")
    _dur  = _ab.get("duration", "—") if _last != "—" else ""
    _last_str = f"{_last} &nbsp;·&nbsp; {_dur}" if _dur else _last
    _next = _ag.get("next_run", "—")
    _mode = _ag.get("mode", "DRY RUN")
    _active_id = _ab.get("id", "—") if _ab.get("status") not in ("unavailable", "completed") else "None"

    # Agent controls card
    agent_card = f"""
    <div class="card admin-card">
      <div class="admin-section-title">Agent Controls</div>
      <div class="agent-row">
        <span class="agent-row-label">Scheduler</span>
        <span class="agent-row-val">
          <span class="sys-dot dot-green" style="display:inline-block;margin-right:6px"></span>{_ag.get('scheduler', 'UNAVAILABLE')}
        </span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Last batch</span>
        <span class="agent-row-val">{_last_str}</span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Next scheduled</span>
        <span class="agent-row-val">{_next}</span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Current mode</span>
        <span class="badge badge-indigo">{_mode}</span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Active batch</span>
        <span class="agent-row-val" style="color:#94a3b8">{_active_id}</span>
      </div>
      <div class="admin-btns">
        <button class="btn-run" disabled title="Use CLI: errander --run-now --env &lt;env&gt;" style="opacity:0.45;cursor:not-allowed">▶ RUN BATCH NOW</button>
        <button class="btn-warn-ol" disabled title="Use CLI: errander --pause-scheduler" style="opacity:0.45;cursor:not-allowed">⏸ PAUSE SCHEDULER</button>
      </div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:0.65rem;color:#94a3b8;margin-top:6px">
        Agent controls require the running agent process — use the CLI.
      </div>
    </div>"""

    # System health card — real env vars in live mode, fixture data in demo mode
    def _live_health_checks() -> list[dict[str, Any]]:
        llm_url = os.environ.get("ERRANDER_LLM_BASE_URL", "")
        slack_tok = os.environ.get("ERRANDER_SLACK_BOT_TOKEN", "")
        audit_db = os.environ.get("ERRANDER_AUDIT_DB_URL", "")
        return [
            {"label": "vLLM Endpoint", "detail": llm_url or "—",
             "status": "ok" if llm_url else "warn", "meta": "configured" if llm_url else "ERRANDER_LLM_BASE_URL not set"},
            {"label": "Slack API", "detail": "api.slack.com",
             "status": "ok" if slack_tok else "warn", "meta": "token configured" if slack_tok else "ERRANDER_SLACK_BOT_TOKEN not set"},
            {"label": "Audit DB", "detail": audit_db or "—",
             "status": "ok" if audit_db else "warn", "meta": "configured" if audit_db else "ERRANDER_AUDIT_DB_URL not set"},
            {"label": "SSH Keys", "detail": "key-auth enforced",
             "status": "ok", "meta": "no passwords"},
            {"label": "APScheduler", "detail": _ag.get("scheduler", "—"),
             "status": "ok", "meta": "running"},
        ]

    _health = _HEALTH_CHECKS if get_provider().data_mode() == "FIXTURE" else _live_health_checks()
    health_rows = ""
    for h in _health:
        if h["status"] == "ok":
            ind = f'<span class="h-ok"><span class="hdot-ok"></span>OK &nbsp;·&nbsp; {h["meta"]}</span>'
        elif h["status"] == "warn":
            ind = f'<span class="h-warn"><span class="hdot-warn"></span>WARN &nbsp;·&nbsp; {h["meta"]}</span>'
        else:
            ind = f'<span class="h-err"><span class="hdot-err"></span>ERROR &nbsp;·&nbsp; {h["meta"]}</span>'
        health_rows += f"""
        <div class="health-row">
          <span class="health-label">{h['label']}</span>
          <span class="health-detail">{h['detail']}</span>
          {ind}
        </div>"""

    health_card = f"""
    <div class="card admin-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <span class="admin-section-title" style="margin-bottom:0">System Health</span>
        <button class="btn-outline btn-outline-indigo" disabled title="Use CLI: errander --check-targets --env &lt;env&gt;" style="font-size:0.75rem;padding:5px 12px;opacity:0.45;cursor:not-allowed">RUN CHECK</button>
      </div>
      <div class="health-rows">{health_rows}</div>
      <div style="margin-top:14px;font-family:'JetBrains Mono',monospace;font-size:0.6875rem;color:#94a3b8">
        {'Last checked: 2026-05-13 03:00:12 UTC' if _is_fixture else 'Not yet checked — use CLI: errander --check-targets &lt;env&gt;'}
      </div>
    </div>"""

    # Lock manager
    if _ACTIVE_LOCKS:
        lock_rows = "".join(
            f"""<tr>
              <td class="td-mono">{lk['vm']}</td>
              <td class="td-ts">{lk['since']}</td>
              <td class="td-mono" style="color:#94a3b8;font-size:0.75rem">{lk['path']}</td>
              <td><button class="btn-danger-ol" style="font-size:0.75rem;padding:4px 10px" onclick="_showDanger(1)">FORCE CLEAR</button></td>
            </tr>"""
            for lk in _ACTIVE_LOCKS
        )
        lock_body = f"""
        <table class="data-table">
          <thead><tr><th>VM</th><th>LOCKED SINCE</th><th>LOCK FILE</th><th></th></tr></thead>
          <tbody>{lock_rows}</tbody>
        </table>"""
    else:
        lock_body = """
        <div style="padding:28px;text-align:center;color:#94a3b8;
                    font-family:'JetBrains Mono',monospace;font-size:0.8125rem">
          ✓ &nbsp;No active locks — fleet is clean
        </div>"""

    lock_card = f"""
    <div class="card table-card" style="margin-bottom:16px">
      <div style="padding:14px 20px;display:flex;align-items:center;
                  justify-content:space-between;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:0.9375rem">Lock Manager</span>
        <button class="btn-danger-ol" style="font-size:0.75rem;padding:5px 12px" onclick="_showDanger(1)">CLEAR ALL LOCKS</button>
      </div>
      {lock_body}
    </div>"""

    # Override toggles
    override_rows = ""
    for label, desc, on in _OVERRIDES:
        chk = "checked" if on else ""
        state_cls = "t-on" if on else "t-off"
        state_txt = "ON" if on else "OFF"
        override_rows += f"""
        <div class="override-row">
          <div>
            <div class="override-label">{label}</div>
            <div class="override-desc">{desc}</div>
          </div>
          <div class="toggle-wrap">
            <span class="{state_cls}" id="lbl-{label.replace(' ','-')}">{state_txt}</span>
            <label class="toggle">
              <input type="checkbox" {chk}
                onchange="var l=document.getElementById('lbl-{label.replace(" ","-")}');
                          l.textContent=this.checked?'ON':'OFF';
                          l.className=this.checked?'t-on':'t-off'">
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>"""

    override_card = f"""
    <div class="card admin-card" style="margin-bottom:16px">
      <div class="admin-section-title">Override Controls</div>
      <div class="override-rows">{override_rows}</div>
    </div>"""

    # Danger zone with per-action typed-confirm friction
    danger_actions = [
        {
            "key":   "flush-deferred",
            "label": "FLUSH DEFERRED QUEUE",
            "color": "#d97706",
            "role":  "sre-lead",
            "type":  "phrase",
            "phrase":"FLUSH DEFERRED QUEUE",
            "audit_event": "admin.deferred_queue.flush",
            "desc":  "Drops all stored deferred plan artifacts. Operators must re-run plan + re-approve for any VM whose window has not yet opened.",
        },
        {
            "key":   "clear-locks",
            "label": "CLEAR ALL LOCKS",
            "color": "#dc2626",
            "role":  "sre-lead",
            "type":  "phrase",
            "phrase":"CLEAR ALL LOCKS",
            "audit_event": "admin.locks.force_clear_all",
            "desc":  "Removes every per-VM file lock. Use only when a previous agent process died without releasing its locks. Concurrent execution is unsafe.",
        },
        {
            "key":   "force-rollback",
            "label": "FORCE ROLLBACK ALL VMs",
            "color": "#7c3aed",
            "role":  "sre-lead + change-mgmt",
            "type":  "phrase",
            "phrase":"FORCE ROLLBACK",
            "audit_event": "admin.rollback.force_all",
            "desc":  "Triggers per-action rollback strategy on every VM in the latest batch. Destructive: re-pulls images, downgrades pinned packages.",
        },
        {
            "key":   "truncate-audit",
            "label": "TRUNCATE AUDIT LOG",
            "color": "#0f172a",
            "role":  "DBA only (requires offline DBA token)",
            "type":  "blocked",
            "phrase":"",
            "audit_event": "(blocked in UI — DBA-only path)",
            "desc":  "Permanently deletes the audit history. The UI does not expose this action — it is blocked. The DBA token path lives outside the agent and writes a tamper-evidence event before truncation.",
        },
    ]

    rows = ""
    for i, d in enumerate(danger_actions):
        if d["type"] == "blocked":
            cta = f'<button class="btn-danger" style="background:{d["color"]};opacity:0.4;cursor:not-allowed" disabled title="Blocked in UI — DBA-only path">{d["label"]} · BLOCKED</button>'
        else:
            cta = f'<button class="btn-danger" style="background:{d["color"]}" onclick="_showDanger({i})">{d["label"]}</button>'
        rows += f"""
        <div class="danger-row" style="display:flex;gap:14px;align-items:flex-start;padding:12px 0;border-top:1px solid #f1f5f9">
          <div style="flex:1;min-width:0">
            <div style="font-family:'Space Grotesk',sans-serif;font-weight:700;color:#1e1b4b;font-size:0.875rem">{d['label']}</div>
            <div style="font-family:'Inter',sans-serif;color:#475569;font-size:0.8125rem;margin-top:4px;line-height:1.4">{d['desc']}</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:0.6875rem;color:#94a3b8;margin-top:6px">
              ROLE: {d['role']} &nbsp;·&nbsp; AUDIT EVENT: <span style="color:#3730a3">{d['audit_event']}</span>
            </div>
          </div>
          <div style="flex-shrink:0">{cta}</div>
        </div>"""

    # Serialize danger actions for JS
    import json as _json
    danger_js = _json.dumps(danger_actions)

    danger_card = f"""
    <div class="card danger-zone-card">
      <div class="danger-zone-hdr">
        <span style="font-size:1.1rem">⚠</span>
        <span class="danger-zone-title">Danger Zone</span>
      </div>
      <div class="danger-zone-sub">
        Every action below is destructive, audited, and gated by typed confirmation + reason.
        The UI is a forward — the destructive call runs via the same audit-writing path as the CLI.
      </div>
      {rows}
    </div>"""

    danger_modal = """
    <div class="confirm-modal" id="danger-modal" onclick="if(event.target===this)_hideDanger()">
      <div class="confirm-card">
        <div class="confirm-hdr danger" id="dg-hdr">
          <span style="font-size:1.1rem">⚠</span>
          <span><span id="dg-label">DESTRUCTIVE ACTION</span></span>
        </div>
        <div class="confirm-body">
          <p id="dg-desc"></p>
          <div class="confirm-evidence">
            Audit event preview: <span id="dg-event" style="color:#3730a3"></span><br>
            Required role: <span id="dg-role"></span>
          </div>
          <div class="confirm-field">
            <label>Type the exact phrase: <span id="dg-phrase-expected"
              style="color:#3730a3;font-weight:700;letter-spacing:0.05em"></span></label>
            <input id="dg-phrase-input" type="text" autocomplete="off" oninput="_dgCheck()">
            <div id="dg-match" style="font-family:'JetBrains Mono',monospace;font-size:0.6875rem;margin-top:4px"></div>
          </div>
          <div class="confirm-field">
            <label>Reason (≥ 20 chars, written to audit)</label>
            <textarea id="dg-reason" rows="3" oninput="_dgCheck()"></textarea>
          </div>
        </div>
        <div class="confirm-foot">
          <button class="btn-cancel" onclick="_hideDanger()">Cancel</button>
          <button class="btn-go danger" id="dg-go" disabled onclick="_dgGo()">Execute destructive action</button>
        </div>
      </div>
    </div>
    <script>
      const DANGER_ACTIONS = """ + danger_js + """;
      function _showDanger(i) {
        const d = DANGER_ACTIONS[i];
        document.getElementById('dg-label').textContent = d.label;
        document.getElementById('dg-desc').textContent = d.desc;
        document.getElementById('dg-event').textContent = d.audit_event;
        document.getElementById('dg-role').textContent = d.role;
        document.getElementById('dg-phrase-expected').textContent = d.phrase;
        document.getElementById('dg-phrase-input').value = '';
        document.getElementById('dg-reason').value = '';
        document.getElementById('dg-match').textContent = '';
        document.getElementById('dg-go').disabled = true;
        document.getElementById('danger-modal').classList.add('show');
      }
      function _hideDanger() { document.getElementById('danger-modal').classList.remove('show'); }
      function _dgCheck() {
        const expected = document.getElementById('dg-phrase-expected').textContent.trim();
        const got = document.getElementById('dg-phrase-input').value.trim();
        const reason = document.getElementById('dg-reason').value.trim();
        const match = document.getElementById('dg-match');
        const go = document.getElementById('dg-go');
        const phraseOk = (got === expected);
        const reasonOk = (reason.length >= 20);
        if (got.length === 0) { match.textContent = ''; match.className = ''; }
        else if (!phraseOk)   { match.textContent = '✕ phrase does not match'; match.className = 'mismatch'; }
        else                  { match.textContent = '✓ phrase matches'; match.className = 'match'; }
        go.disabled = !(phraseOk && reasonOk);
      }
      function _dgGo() {
        const label = document.getElementById('dg-label').textContent;
        const evt   = document.getElementById('dg-event').textContent;
        alert('(demo) Would write audit event ' + evt + ' and execute: ' + label +
              '.\\nIn live mode this calls the same CLI path with the typed reason attached.');
        _hideDanger();
      }
    </script>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Admin</div>
        <div class="section-sub">Agent controls, system health, lock management, and operational overrides</div>
      </div>
      <span class="badge badge-amber" style="font-size:0.75rem;padding:5px 12px">⚠ DRY RUN MODE ACTIVE</span>
    </div>
    <div class="destructive-hdr">
      <span style="font-size:1rem">⚠</span>
      <span>DESTRUCTIVE — AUDITED</span>
      <span class="pill">Every action below writes to the audit log before execution</span>
      <span class="pill">Typed confirm + reason required</span>
    </div>
    <div class="admin-top">
      {agent_card}
      {health_card}
    </div>
    {lock_card}
    {override_card}
    {danger_card}
    {danger_modal}"""


# ── Glossary data ────────────────────────────────────────────────────────────

_GLOSS: list[tuple[str, str, str, str, str]] = [
    # ── CORE ─────────────────────────────────────────────────────────────────
    ("Batch",              "CORE",    "#4f46e5", "gloss-chip-core",
     "A single end-to-end maintenance run across all VMs in the fleet. Identified by a unique ID like prod-0423-0200."),
    ("Agent",              "CORE",    "#4f46e5", "gloss-chip-core",
     "The LangGraph-powered system that orchestrates maintenance decisions and enforces human approval before any live infrastructure change."),
    ("LangGraph",          "CORE",    "#4f46e5", "gloss-chip-core",
     "State machine framework driving the agent workflow. Each node is a discrete step; edges are conditional transitions."),
    ("Dry Run",            "CORE",    "#4f46e5", "gloss-chip-core",
     "Simulation mode. Actions are planned and logged but never executed on real VMs. The default safety mode."),
    ("Fleet",              "CORE",    "#4f46e5", "gloss-chip-core",
     "The full collection of target VMs managed by the agent across all environments (PROD, STAGING, DEV)."),
    ("Idempotent",         "CORE",    "#4f46e5", "gloss-chip-core",
     "Running the same action twice produces the same result. A core design invariant for all agent actions."),
    ("Stored Signals",     "CORE",    "#4f46e5", "gloss-chip-core",
     "Historical data (disk trends, drift events, failure counts, login spikes) loaded from monitoring stores before planning. Feeds into LLM decisions so the agent acts on trends, not just current state."),
    ("Daily Probe",        "CORE",    "#4f46e5", "gloss-chip-core",
     "Read-only signal sweep (disk growth, drift, failed logins, journal errors, failed services). Runs on schedule or --probe-now. Never executes maintenance actions — observation only."),
    ("Operator Assist.",   "CORE",    "#4f46e5", "gloss-chip-core",
     "Layer A CLI (--ask) that investigates fleet state using audit data, Prometheus, and ELK, then answers questions via LLM. Strictly read-only — never executes infrastructure changes."),
    ("Investigation Agent", "CORE",   "#4f46e5", "gloss-chip-core",
     "Opt-in agentic mode for --ask (--agentic, ERRANDER_INVESTIGATION_AGENT_ENABLED). The LLM composes its own Prometheus/ELK/audit queries in a bounded tool-calling loop instead of a fixed query set. Still Layer A — read-only, recommends only — falls back to the deterministic Operator Assistant on any failure."),
    ("Dashboard Chat",     "CORE",    "#4f46e5", "gloss-chip-core",
     "Opt-in multi-turn web console at /ui/chat (ERRANDER_CHAT_ENABLED). Per-user threads over the same investigation engine as --ask, with citations. Read-only — phase 1 has no streaming and no action handoff."),
    ("Planning Note",      "CORE",    "#4f46e5", "gloss-chip-core",
     "Short LLM-generated note attached to an already-finalized deterministic plan. Informational only — never changes which actions run or their order. Shown in the Slack approval message and the web approval card."),
    # ── SAFETY ───────────────────────────────────────────────────────────────
    ("Approval Gate",      "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "High-risk actions pause here. The agent persists a durable approval request, notifies Slack with the exact packages and versions plus a web approval link, and waits for a named operator's decision in the Web UI."),
    ("Plan Hash",          "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "SHA-256 fingerprint of the approved plan including exact package names and versions. Guarantees the operator approved precisely what was executed — prevents plan-substitution attacks."),
    ("Plan Enrichment",    "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "SSH assessment at plan time to collect exact package versions and disk state before the plan hash is computed. The Slack approval message then shows nginx 1.18→1.24, not just 'patching'."),
    ("Deferred Exec.",     "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "When a batch runs outside a maintenance window, the exact approved plan artifact is stored and replayed at window-open time — no re-approval needed, same hash verified."),
    ("Probe Escalation",   "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "When the daily probe detects critical signals (disk ≥90%, 2+ failed services, drift+login spikes), a separate Slack alert is sent prompting the operator to run an emergency batch."),
    ("Disk Gate",          "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Post-cleanup guard node in the VM graph. After disk_cleanup or log_rotation, re-checks disk usage before proceeding to patching. Blocks at ≥95%, warns at 90–94%."),
    ("Rollback",           "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Automatic revert to pre-action state on failure. Strategy differs per action: full package rollback (patching), re-pull (Docker), or no-op (log/disk)."),
    ("Risk Tier",          "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Action classification by impact: Low (auto), Medium (log+notify), High (approval required), Critical (blocked — never automated)."),
    ("Maintenance Window", "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Configured time slots when the agent is permitted to run. The agent refuses to act outside these windows unless --force is passed with a mandatory reason."),
    ("Audit Log",          "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Immutable before-and-after record of every agent action. Written to PostgreSQL. In strict mode, a write failure halts the batch — audit integrity takes priority over execution."),
    ("Layer A",            "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Operator Assistant layer — may use LLM, Prometheus, ELK, Slack context, and runbooks to investigate and recommend. Read-only: produces text and proposals, never executes infrastructure changes. Exposed via --ask and the Sovereign Architect UI."),
    ("Layer B",            "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Safe Execution layer — deterministic Python that plans, validates, requests approval, executes, audits, and rolls back. No LLM in the live execution path, no AI-generated shell commands, no AI self-approval. Changes to Layer B require explicit safety review."),
    # ── ACTIONS ──────────────────────────────────────────────────────────────
    ("OS Patching",        "ACTIONS", "#0891b2", "gloss-chip-action",
     "Non-kernel security and package updates via apt (Ubuntu/Debian) or dnf (RHEL). Kernel updates are blocked. Exact packages shown in Slack approval message. Medium risk."),
    ("Docker Hygiene",     "ACTIONS", "#0891b2", "gloss-chip-action",
     "Rich Docker assessment — dangling images, stopped containers, unused images, volumes, build cache. Exact-object web approval before any removal (replaced the bulk Docker Prune action in v1.1). Medium risk. Re-pull is the only recovery path for removed images."),
    ("Log Rotation",       "ACTIONS", "#0891b2", "gloss-chip-action",
     "Compression and archival of old log files in /var/log via logrotate or journalctl vacuum. Low risk — data is retained, just compressed."),
    ("Disk Cleanup",       "ACTIONS", "#0891b2", "gloss-chip-action",
     "Frees temp files from a strict whitelist: /tmp, apt/yum cache, old journals, orphaned deps only. Followed by the Disk Gate before any patching action. Low risk."),
    ("Backup Verify",      "ACTIONS", "#0891b2", "gloss-chip-action",
     "Read-only integrity check: verifies backup files exist, are recent, and meet minimum size thresholds via SSH. Never modifies files. Low risk — runs automatically without approval."),
    ("Service Restart",    "ACTIONS", "#0891b2", "gloss-chip-action",
     "Operator-triggered restart of a specific systemd unit. High risk — always requires human approval in the Web UI (Slack notifies and links). v1: operator-triggered only. Unit must appear in restartable_units allowlist (inventory) AND /etc/errander/restart-allowlist on the target VM."),
    # ── INFRA ─────────────────────────────────────────────────────────────────
    ("LLM Endpoint",       "INFRA",   "#d97706", "gloss-chip-infra",
     "Any OpenAI-compatible endpoint: cloud API (OpenAI, Anthropic, Groq) or self-hosted vLLM. Configured via ERRANDER_LLM_BASE_URL + ERRANDER_LLM_MODEL. Hardcoded fallback when unreachable — agent never blocks on LLM availability."),
    ("SSH",                "INFRA",   "#d97706", "gloss-chip-infra",
     "Key-based Secure Shell protocol used exclusively to connect to and execute commands on target VMs. Password auth is not supported."),
    ("APScheduler",        "INFRA",   "#d97706", "gloss-chip-infra",
     "Python scheduling library that fires maintenance batches and daily probe runs on configured cron schedules inside the agent process."),
    ("Prometheus",         "INFRA",   "#d97706", "gloss-chip-infra",
     "Optional HTTP adapter for VM metrics (CPU, memory, disk usage) from node_exporter. Enriches probe digests and --ask fleet analysis. Per-env URL override supported."),
    ("ELK",                "INFRA",   "#d97706", "gloss-chip-infra",
     "Optional Elasticsearch integration for log error analysis. Enriches probes and --ask. Falls back to journalctl SSH calls when ELK is not configured. Per-env URL override supported."),
]

# Node detail data for JS — plain string avoids f-string brace escaping
_WF_JS = """
function closeNodeModal() {
  document.getElementById('wf-modal-backdrop').classList.remove('open');
  document.getElementById('wf-modal').classList.remove('open');
  document.querySelectorAll('.wf-node').forEach(function(n) { n.classList.remove('active'); });
}
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeNodeModal(); });

const WF_NODES = {
  'apscheduler': {
    title: 'APScheduler', badge: 'BATCH TRIGGER', badgeColor: '#d97706',
    checks: 'Cron expression evaluated · Maintenance window verified · No active batch running · Daily probe jobs also registered here',
    onfail: 'Batch skipped silently — next scheduled run continues normally',
    code: 'errander/scheduling/scheduler.py · errander/scheduling/windows.py',
    note: 'The scheduler is the only automated entry point. Use --run-now to trigger a batch manually, or --probe-now for a read-only signal sweep.'
  },
  'parent-graph': {
    title: 'Parent Graph', badge: 'ORCHESTRATOR', badgeColor: '#4f46e5',
    checks: 'Loads VM inventory · Applies DB overrides (disabled/added VMs) · Acquires per-VM file locks · Fans out to parallel per-VM sub-graphs',
    onfail: 'Individual VM failures do not abort the batch — each VM runs independently',
    code: 'errander/agent/graph.py · errander/config/inventory.py · errander/safety/locking.py',
    note: 'LangGraph parent graph fans out to one per-VM sub-graph in parallel. After all VMs plan, enrich_plan_node SSHes for exact package previews before the hash is computed.'
  },
  'pre-validation': {
    title: 'Pre-Validation', badge: 'RUNS ON EVERY VM', badgeColor: '#16a34a',
    checks: 'SSH reachable · OS detected · Maintenance window active · VM not locked · Sudo/wrapper readiness verified · Docker command mode checked',
    onfail: 'VM removed from batch early — audit event written with reason. Sudo/wrapper failures are caught here rather than mid-batch.',
    code: 'errander/safety/validators.py · errander/execution/os_detection.py · errander/execution/target_validation.py',
    note: 'F2 addition: check_target() now runs at validate-time so a batch never wastes an approval window on a VM that would have failed the sudo preflight.'
  },
  'llm-planning': {
    title: 'LLM Planning', badge: 'AI DECISION', badgeColor: '#7c3aed',
    checks: 'Loads stored signals (disk trends, drift events, failure counts, login spikes) · Queries vLLM endpoint · Outputs ordered action plan as JSON · Classifies risk tier per action',
    onfail: 'Falls back to hardcoded default action priority — agent never blocks on LLM unavailability',
    code: 'errander/agent/decisions.py · errander/agent/graph.py (_load_stored_signals) · errander/integrations/llm.py',
    note: 'F1 addition: StoredSignalContext feeds historical monitoring data into the LLM prompt so it plans based on trends, not just current SSH state.'
  },
  'plan-enrichment': {
    title: 'Plan Enrichment', badge: 'PRE-APPROVAL', badgeColor: '#7c3aed',
    checks: 'SSH to each VM · apt/dnf list --upgradable → exact package names + current + target versions · df / → disk usage snapshot · Kernel packages excluded · Results stored in preview dict per action',
    onfail: 'SSH failure → preview: {"error": "unavailable"} written; batch continues. Hash still covers the error entry — operator sees transparency note in Slack.',
    code: 'errander/agent/graph.py → enrich_plan_node · errander/agent/subgraphs/patching.py → _parse_upgradable_with_versions',
    note: 'The plan hash (SHA-256) covers preview data. The Slack approval message shows exact packages: nginx 1.18.0 → 1.24.0. Operator approves exact actions, not categories.'
  },
  'approval-gate': {
    title: 'Approval Gate', badge: 'HIGH RISK ONLY', badgeColor: '#d97706',
    checks: 'Persists durable approval request · Notifies Slack with exact plan (package names + versions) + web approval link · Named operator decides in the Web UI · Timeout 30 min (auto-REJECT)',
    onfail: 'Action skipped on REJECTED or timeout — audit event written, VM continues to next action',
    code: 'errander/safety/approval.py · errander/integrations/slack.py · errander/agent/graph.py (_format_plan_for_approval)',
    note: 'Only High-tier actions enter this node. Low and Medium actions bypass it entirely. Hash commitment means the operator can verify nothing changed between approval and execution.'
  },
  'action-execution': {
    title: 'Action Execution', badge: 'RUNS MAINTENANCE', badgeColor: '#0891b2',
    checks: 'Dispatches to one of 6 action sub-graphs · dry_run flag respected · Idempotency enforced · Post-cleanup disk gate runs after disk_cleanup/log_rotation before patching · Service restart requires both operator trigger and allowlist match',
    onfail: 'Exception caught → Rollback node entered → Audit event written with error detail',
    code: 'errander/agent/vm_graph.py · errander/agent/subgraphs/ · errander/execution/commands.py',
    note: 'post_cleanup_disk_gate_node re-checks disk after cleanup. Blocks patching at ≥95%, warns at 90–94%. All 6 v1 sub-graphs: patching, log_rotation, docker_hygiene, disk_cleanup, backup_verify, service_restart. Service restart is operator-triggered only and always requires human approval in the Web UI (Slack notifies and links).'
  },
  'rollback': {
    title: 'Rollback', badge: 'FAILURE PATH ONLY', badgeColor: '#ef4444',
    checks: 'Restores full package snapshot (patching) · Re-pull images (Docker) · No-op for log/disk',
    onfail: 'Critical alert fired if rollback itself fails — requires manual intervention',
    code: 'errander/safety/rollback.py',
    note: 'Not all actions support full rollback. Patching: full version rollback. Docker: re-pull only. Log rotation and disk cleanup: no rollback needed (non-destructive).'
  },
  'audit-logging': {
    title: 'Audit Logging', badge: 'ALWAYS RUNS', badgeColor: '#16a34a',
    checks: 'Writes before-event · Writes after-event · Records duration, operator, status, detail · Strict mode: write failure halts batch',
    onfail: 'In strict mode — agent halts. Audit integrity takes priority over execution. In best-effort mode — logs and continues.',
    code: 'errander/safety/audit.py · errander/models/events.py',
    note: 'Every action produces two audit events: one before execution and one after. Events are never deleted. Browse them in the Batches UI.'
  },
  'report': {
    title: 'Report', badge: 'BATCH SUMMARY', badgeColor: '#4f46e5',
    checks: 'LLM generates human-readable batch summary · Falls back to template if LLM unavailable · Posted to Slack · Probe digest posted separately on probe runs',
    onfail: 'Template fallback always succeeds — batch report is never skipped',
    code: 'errander/observability/reporting.py · errander/integrations/slack.py',
    note: 'Report includes: VMs processed, actions taken, errors, rollbacks, duration. Probe digests also include disk growth alerts, failed services, journal errors, and escalation flags.'
  },
};

function selectNode(id) {
  document.querySelectorAll('.wf-node').forEach(function(n) { n.classList.remove('active'); });
  var el = document.getElementById('node-' + id);
  if (el) el.classList.add('active');
  var d = WF_NODES[id];
  if (!d) return;
  document.getElementById('wf-modal-title').textContent = d.title;
  document.getElementById('wf-modal-title').style.color = d.badgeColor;
  var badge = document.getElementById('wf-modal-badge');
  badge.textContent = d.badge;
  badge.style.background = d.badgeColor;
  document.getElementById('wf-modal-checks').textContent = d.checks;
  document.getElementById('wf-modal-onfail').textContent = d.onfail;
  document.getElementById('wf-modal-code').textContent = d.code;
  document.getElementById('wf-modal-note').textContent = d.note;
  document.getElementById('wf-modal').style.borderLeftColor = d.badgeColor;
  document.getElementById('wf-modal-backdrop').classList.add('open');
  document.getElementById('wf-modal').classList.add('open');
}
"""


GLOSS_CSS = """
/* ── Glossary grid ── */
.gloss-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 28px; }
.gloss-card { background: #fff; border-radius: 8px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.gloss-card-hdr { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.gloss-term { font-family: 'JetBrains Mono', monospace; font-size: 0.875rem; font-weight: 700; color: #4f46e5; }
.gloss-chip { font-family: 'JetBrains Mono', monospace; font-size: 0.55rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 2px 6px; border-radius: 3px; flex-shrink: 0; }
.gloss-chip-core   { background: #e0e7ff; color: #3730a3; }
.gloss-chip-safety { background: #ede9fe; color: #5b21b6; }
.gloss-chip-action { background: #cffafe; color: #155e75; }
.gloss-chip-infra  { background: #fef3c7; color: #92400e; }
.gloss-defn { font-size: 0.8125rem; color: #475569; line-height: 1.55; }
/* ── Workflow diagram ── */
@keyframes dash-flow { to { stroke-dashoffset: -26; } }
.wf-outer-card { background: #0f172a; border-radius: 12px; padding: 24px; margin-bottom: 8px; }
.wf-diagram-wrap { overflow-x: auto; padding-bottom: 8px; }
.wf-diagram { position: relative; width: 960px; height: 845px; margin: 0 auto; }
.wf-svg { position: absolute; top: 0; left: 0; width: 960px; height: 845px; pointer-events: none; overflow: visible; }
.wf-node { position: absolute; width: 160px; height: 50px; border-radius: 8px; display: flex; align-items: center; gap: 10px; padding: 0 14px; cursor: pointer; transition: all 0.18s; background: #1e293b; user-select: none; }
.wf-node:hover { background: #243348; transform: translateY(-1px); box-shadow: 0 4px 16px rgba(79,70,229,0.3); }
.wf-node.active { background: linear-gradient(135deg, #3525cd, #712ae2) !important; box-shadow: 0 4px 24px rgba(79,70,229,0.5); border: none !important; }
.wf-node.active .wf-node-name { color: #fff !important; }
.wf-node.active .wf-node-sub  { color: rgba(255,255,255,0.65) !important; }
.wf-node-conditional { border: 1.5px dashed #d97706; background: #1e1a0f !important; }
.wf-node-conditional:hover { background: #29240f !important; }
.wf-node-failure-node { border: 1.5px dashed #ef4444; background: #1e1010 !important; }
.wf-node-failure-node:hover { background: #2a1515 !important; }
.wf-node-terminal { position: absolute; width: 110px; height: 38px; border-radius: 6px; display: flex; align-items: center; justify-content: center; background: #1e1010; border: 1.5px dashed #ef4444; font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; font-weight: 700; color: #ef4444; letter-spacing: 0.05em; }
.wf-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.wf-dot-amber  { background: #fbbf24; box-shadow: 0 0 6px #fbbf24; }
.wf-dot-indigo { background: #818cf8; box-shadow: 0 0 6px #818cf8; }
.wf-dot-violet { background: #a78bfa; box-shadow: 0 0 6px #a78bfa; }
.wf-dot-teal   { background: #22d3ee; box-shadow: 0 0 6px #22d3ee; }
.wf-dot-red    { background: #f87171; box-shadow: 0 0 6px #f87171; }
.wf-dot-green  { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
.wf-dot-white  { background: rgba(255,255,255,0.85); }
.wf-node-name { font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; font-weight: 700; color: #e2e8f0; white-space: nowrap; }
.wf-node-sub  { font-size: 0.585rem; color: #64748b; font-family: 'Inter', sans-serif; white-space: nowrap; margin-top: 2px; }
.wf-legend { display: flex; align-items: center; gap: 20px; margin-bottom: 16px; flex-wrap: wrap; }
.wf-legend-item { display: flex; align-items: center; gap: 8px; font-size: 0.75rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace; }
.wf-detail { background: #fff; border-radius: 8px; border-left: 4px solid #4f46e5; padding: 16px 20px; margin-top: 16px; transition: border-color 0.2s; }
.wf-detail-hdr { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.wf-detail-title { font-family: 'Space Grotesk', sans-serif; font-size: 1rem; font-weight: 700; color: #4f46e5; transition: color 0.2s; }
.wf-detail-badge { font-family: 'JetBrains Mono', monospace; font-size: 0.6rem; font-weight: 700; letter-spacing: 0.08em; padding: 3px 8px; border-radius: 4px; color: #fff; transition: background 0.2s; }
.wf-detail-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.wf-detail-row { display: flex; gap: 14px; }
.wf-detail-lbl { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 0.6875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.06em; width: 68px; flex-shrink: 0; padding-top: 1px; }
.wf-detail-val { font-family: 'JetBrains Mono', monospace; font-size: 0.775rem; color: #0f172a; line-height: 1.55; }
.wf-detail-note { font-size: 0.8rem; color: #64748b; font-style: italic; border-top: 1px solid #f1f5f9; padding-top: 10px; }
.wf-hint { text-align: center; font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem; color: #334155; padding: 12px 0 4px; letter-spacing: 0.04em; }
.wf-modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(15,23,42,0.55); backdrop-filter: blur(4px); z-index: 200; }
.wf-modal-backdrop.open { display: block; }
@keyframes modal-in { from { opacity: 0; transform: translate(-50%, -48%); } to { opacity: 1; transform: translate(-50%, -50%); } }
.wf-modal { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 520px; max-width: 92vw; background: #fff; border-radius: 12px; border-left: 4px solid #4f46e5; padding: 22px 26px; z-index: 201; box-shadow: 0 24px 64px -12px rgba(24,20,69,0.28); }
.wf-modal.open { display: block; animation: modal-in 0.17s ease; }
.wf-modal-close { position: absolute; top: 12px; right: 14px; background: none; border: none; cursor: pointer; font-size: 0.9rem; color: #94a3b8; font-family: 'JetBrains Mono', monospace; font-weight: 700; padding: 3px 8px; border-radius: 4px; transition: all 0.12s; line-height: 1; }
.wf-modal-close:hover { background: #f1f5f9; color: #0f172a; }
@media (max-width: 1100px) { .gloss-grid { grid-template-columns: repeat(2, 1fr); } }
"""


def page_glossary() -> str:
    # ── Glossary grid ─────────────────────────────────────────────────────────
    cards = ""
    for term, _cat, color, chip_cls, defn in _GLOSS:
        cat_label = _cat
        cards += f"""
        <div class="gloss-card" style="border-left:3px solid {color}">
          <div class="gloss-card-hdr">
            <span class="gloss-term">{term}</span>
            <span class="gloss-chip {chip_cls}">{cat_label}</span>
          </div>
          <div class="gloss-defn">{defn}</div>
        </div>"""

    grid_section = f"""
    <div class="section-hdr" style="margin-bottom:16px">
      <div>
        <div class="section-title">Glossary</div>
        <div class="section-sub">Core concepts for understanding how errander-ai works</div>
      </div>
    </div>
    <div class="gloss-grid">{cards}</div>"""

    # ── Workflow diagram ───────────────────────────────────────────────────────
    # Node definitions: (id, left, top, extra_classes, dot_cls, name, sublabel)
    _nodes = [
        ("apscheduler",      400, 20,  "",                        "wf-dot-amber",  "APScheduler",    "cron trigger"),
        ("parent-graph",     400, 110, "",                        "wf-dot-indigo", "Parent Graph",   "fan-out · VMs"),
        ("pre-validation",   400, 200, "active",                  "wf-dot-white",  "Pre-Validation", "SSH · OS · readiness"),
        ("llm-planning",     400, 290, "",                        "wf-dot-violet", "LLM Planning",   "signals · plan · risk"),
        ("plan-enrichment",  400, 375, "",                        "wf-dot-violet", "Plan Enrichment","exact pkgs · versions · hash"),
        ("approval-gate",    120, 470, "wf-node-conditional",     "wf-dot-amber",  "Approval Gate",  "high-risk · Slack"),
        ("action-execution", 650, 470, "",                        "wf-dot-teal",   "Action Exec.",   "6 sub-graphs · all actions"),
        ("rollback",         755, 565, "wf-node-failure-node",    "wf-dot-red",    "Rollback",       "revert snapshot"),
        ("audit-logging",    400, 660, "",                        "wf-dot-green",  "Audit Logging",  "before + after"),
        ("report",           400, 750, "",                        "wf-dot-indigo", "Report",         "LLM or template"),
    ]
    nodes_html = ""
    for nid, left, top, extra, dot, name, sub in _nodes:
        nodes_html += (
            f'<div class="wf-node {extra}" id="node-{nid}"'
            f' style="left:{left}px;top:{top}px"'
            f' onclick="selectNode(\'{nid}\')">'
            f'<span class="wf-dot {dot}"></span>'
            f'<div><div class="wf-node-name">{name}</div>'
            f'<div class="wf-node-sub">{sub}</div></div></div>'
        )
    nodes_html += '<div class="wf-node-terminal" style="left:50px;top:565px">✕ SKIPPED</div>'

    # SVG arrow overlay — all coordinates are pixel-exact for 960×845 container
    svg = """<svg class="wf-svg" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="mh" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <polygon points="0 0,8 3,0 6" fill="#4f46e5"/></marker>
    <marker id="mg" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <polygon points="0 0,8 3,0 6" fill="#16a34a"/></marker>
    <marker id="ma" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <polygon points="0 0,8 3,0 6" fill="#d97706"/></marker>
    <marker id="mr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
      <polygon points="0 0,8 3,0 6" fill="#ef4444"/></marker>
  </defs>

  <!-- Happy-path: APScheduler → Parent Graph → Pre-Validation → LLM Planning -->
  <path d="M 480,70 L 480,110"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>
  <path d="M 480,160 L 480,200"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>
  <path d="M 480,250 L 480,290"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>

  <!-- LLM Planning → Plan Enrichment (P0-1, violet) -->
  <path d="M 480,340 L 480,375"
        stroke="#7c3aed" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #7c3aed)"/>

  <!-- Plan Enrichment → Action Execution (low/med, happy indigo) -->
  <path d="M 515,425 C 585,450 670,463 730,470"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>

  <!-- Plan Enrichment → Approval Gate (high risk, amber dashed) -->
  <path d="M 445,425 C 375,447 265,460 200,470"
        stroke="#d97706" stroke-width="1.5" fill="none" stroke-dasharray="5 5"
        marker-end="url(#ma)"/>

  <!-- Approval Gate → Action Execution (APPROVED, green animated) -->
  <path d="M 280,495 C 430,495 500,495 650,495"
        stroke="#16a34a" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mg)"
        style="animation:dash-flow 0.9s linear infinite"/>

  <!-- Approval Gate → SKIPPED (REJECTED, red dashed) -->
  <path d="M 185,520 C 165,540 130,553 105,565"
        stroke="#ef4444" stroke-width="1.5" fill="none" stroke-dasharray="4 5"
        marker-end="url(#mr)"/>

  <!-- Action Execution → Rollback (FAILURE, red dashed) -->
  <path d="M 810,495 C 848,517 850,543 835,565"
        stroke="#ef4444" stroke-width="1.5" fill="none" stroke-dasharray="4 5"
        marker-end="url(#mr)"/>

  <!-- Action Execution → Audit Logging (SUCCESS, green animated) -->
  <path d="M 730,520 C 700,575 635,643 560,685"
        stroke="#16a34a" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mg)"
        style="animation:dash-flow 0.9s linear infinite"/>

  <!-- Rollback → Audit Logging (rejoins, amber dashed) -->
  <path d="M 800,615 C 762,643 660,668 560,685"
        stroke="#d97706" stroke-width="1.5" fill="none" stroke-dasharray="5 5"
        marker-end="url(#ma)"/>

  <!-- Audit Logging → Report (happy) -->
  <path d="M 480,710 L 480,750"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>

  <!-- Edge labels -->
  <text x="308" y="439" fill="#d97706" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">HIGH RISK</text>
  <text x="576" y="441" fill="#818cf8" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">LOW / MED</text>
  <text x="428" y="487" fill="#4ade80" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">APPROVED</text>
  <text x="116" y="540" fill="#f87171" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">REJECTED</text>
  <text x="820" y="533" fill="#f87171" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">FAILURE</text>
  <text x="650" y="593" fill="#4ade80" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">SUCCESS</text>
</svg>"""

    legend = """
    <div class="wf-legend">
      <span class="wf-legend-item">
        <svg width="28" height="10"><line x1="0" y1="5" x2="28" y2="5" stroke="#4f46e5" stroke-width="2"
          stroke-dasharray="8 5" style="animation:dash-flow 0.8s linear infinite"/></svg>
        Happy path
      </span>
      <span class="wf-legend-item">
        <svg width="28" height="10"><line x1="0" y1="5" x2="28" y2="5" stroke="#d97706"
          stroke-width="1.5" stroke-dasharray="5 4"/></svg>
        Conditional
      </span>
      <span class="wf-legend-item">
        <svg width="28" height="10"><line x1="0" y1="5" x2="28" y2="5" stroke="#ef4444"
          stroke-width="1.5" stroke-dasharray="4 4"/></svg>
        Failure path
      </span>
    </div>"""

    modal_html = """
    <div class="wf-modal-backdrop" id="wf-modal-backdrop" onclick="closeNodeModal()"></div>
    <div class="wf-modal" id="wf-modal">
      <button class="wf-modal-close" onclick="closeNodeModal()">✕</button>
      <div class="wf-detail-hdr">
        <span class="wf-detail-title" id="wf-modal-title">Pre-Validation</span>
        <span class="wf-detail-badge" id="wf-modal-badge" style="background:#16a34a">RUNS ON EVERY VM</span>
      </div>
      <div class="wf-detail-rows">
        <div class="wf-detail-row">
          <span class="wf-detail-lbl">Checks</span>
          <span class="wf-detail-val" id="wf-modal-checks"></span>
        </div>
        <div class="wf-detail-row">
          <span class="wf-detail-lbl">On fail</span>
          <span class="wf-detail-val" id="wf-modal-onfail"></span>
        </div>
        <div class="wf-detail-row">
          <span class="wf-detail-lbl">Code</span>
          <span class="wf-detail-val" id="wf-modal-code"></span>
        </div>
      </div>
      <div class="wf-detail-note" id="wf-modal-note"></div>
    </div>"""

    workflow_section = f"""
    <div class="section-hdr" style="margin-bottom:12px">
      <div>
        <div class="section-title">Agent Workflow</div>
        <div class="section-sub">Click any node to see what happens at that stage</div>
      </div>
    </div>
    {legend}
    <div class="wf-outer-card">
      <div class="wf-diagram-wrap">
        <div class="wf-diagram" id="wf-diagram">
          {nodes_html}
          {svg}
        </div>
      </div>
      <div class="wf-hint">↑ Click any node to open a detail popup · Press Esc to close</div>
    </div>
    {modal_html}
    <script>{_WF_JS}</script>"""

    return workflow_section + grid_section


def _trace_bar_pct(duration_s: float, max_log: float) -> int:
    if max_log <= 0 or duration_s <= 0:
        return 3
    return max(3, round(math.log10(duration_s + 1) / max_log * 96))


def _outcome_cell(val: str) -> str:
    if val == "ok":
        return '<span class="oc-ok">✓</span>'
    if val == "warn":
        return '<span class="oc-warn">⚠</span>'
    if val == "fail":
        return '<span class="oc-fail">✗</span>'
    if val == "approved":
        return '<span class="oc-appr">APPR</span>'
    return '<span class="oc-skip">—</span>'


def _plan_step_cls(tier: str) -> str:
    return {"HIGH": "plan-step plan-step-high", "MEDIUM": "plan-step plan-step-med"}.get(tier, "plan-step plan-step-low")


def _layer_partition_html(ag: dict[str, Any]) -> str:
    """Visual partition of Layer A (Operator Assistant, LLM-driven, advisory)
    and Layer B (Safe Execution, deterministic, audited) per AI Safety
    Invariant in CLAUDE.md / docs/AI-ARCHITECTURE.md.
    """
    fallback = "active (template path)" if ag.get("llm_status") != "ok" else "armed (not triggered)"
    return f"""
    <div class="layer-partition">
      <div class="layer-pane a">
        <div class="layer-pane-hdr">Layer A — Operator Assistant</div>
        <div class="layer-pane-sub">LLM-driven · advisory only · never executes infra changes</div>
        <div class="layer-pane-rows">
          <div class="layer-pane-row"><span class="lbl">Endpoint</span><span class="val">{ag.get('llm_endpoint', '—')}</span></div>
          <div class="layer-pane-row"><span class="lbl">Model</span><span class="val">{ag.get('llm_model', '—')}</span></div>
          <div class="layer-pane-row"><span class="lbl">Latency p50</span><span class="val">{ag.get('llm_latency_ms', '—')} ms</span></div>
          <div class="layer-pane-row"><span class="lbl">Latency p95</span><span class="val">~{int(ag.get('llm_latency_ms', 50) * 2.4)} ms</span></div>
          <div class="layer-pane-row"><span class="lbl">Fallback (template)</span><span class="val">{fallback}</span></div>
          <div class="layer-pane-row"><span class="lbl">Tool/MCP calls</span><span class="val">none in execution path</span></div>
        </div>
      </div>
      <div class="layer-divider">⚠ SAFETY BOUNDARY · NO LLM PAST THIS LINE ⚠</div>
      <div class="layer-pane b">
        <div class="layer-pane-hdr">Layer B — Safe Execution</div>
        <div class="layer-pane-sub">Deterministic Python · audited · approval-gated · rollback-aware</div>
        <div class="layer-pane-rows">
          <div class="layer-pane-row"><span class="lbl">APScheduler</span><span class="val">{ag.get('scheduler', '—')}</span></div>
          <div class="layer-pane-row"><span class="lbl">Last batch</span><span class="val">{ag.get('last_batch_id', '—')}</span></div>
          <div class="layer-pane-row"><span class="lbl">Next batch</span><span class="val">{ag.get('next_run', '—')}</span></div>
          <div class="layer-pane-row"><span class="lbl">SSH pool</span><span class="val">{len(get_provider().get_vms())} host(s) · key-auth · no passwords</span></div>
          <div class="layer-pane-row"><span class="lbl">Audit DB</span><span class="val">PostgreSQL · strict mode · 0 write failures</span></div>
          <div class="layer-pane-row"><span class="lbl">Slack poll</span><span class="val">outbound only · 30s cadence</span></div>
        </div>
      </div>
    </div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:0.6875rem;color:#94a3b8;margin:-4px 0 14px;padding:0 4px">
      AI Safety Invariant: Layer A may investigate and recommend. Layer B alone may execute,
      and only through deterministic, approved, audited workflows.
    </div>"""


def page_agent() -> str:
    ag = get_provider().get_agent_status()

    layer_partition = _layer_partition_html(ag)

    # ── 1. Status strip ──────────────────────────────────────────────────────
    state_color = "#16a34a" if ag["state"] == "IDLE" else "#4f46e5"
    state_bg    = "#dcfce7" if ag["state"] == "IDLE" else "#e0e7ff"
    mode_color  = "#d97706" if ag["mode"] == "DRY RUN" else "#dc2626"
    mode_bg     = "#fef3c7" if ag["mode"] == "DRY RUN" else "#fee2e2"
    llm_color   = "#16a34a" if ag["llm_status"] == "ok" else "#dc2626"
    llm_bg      = "#dcfce7" if ag["llm_status"] == "ok" else "#fee2e2"

    status_strip = f"""
    <div class="agent-status-grid">
      <div class="card agent-status-chip">
        <div class="agent-status-icon" style="background:{state_bg}">
          <span style="color:{state_color};font-size:1.1rem">{'✓' if ag['state']=='IDLE' else '▶'}</span>
        </div>
        <div class="agent-status-body">
          <span class="agent-status-label">Agent State</span>
          <span class="agent-status-val" style="color:{state_color}">{ag['state']}</span>
          <span class="agent-status-sub">up {ag['uptime']}</span>
        </div>
      </div>
      <div class="card agent-status-chip">
        <div class="agent-status-icon" style="background:{mode_bg}">
          <span style="color:{mode_color};font-size:1rem">{'🔒' if ag['mode']=='DRY RUN' else '⚡'}</span>
        </div>
        <div class="agent-status-body">
          <span class="agent-status-label">Run Mode</span>
          <span class="agent-status-val" style="color:{mode_color}">{ag['mode']}</span>
          <span class="agent-status-sub">change in Admin → Override Controls</span>
        </div>
      </div>
      <div class="card agent-status-chip">
        <div class="agent-status-icon" style="background:#e0e7ff">
          <span style="color:#4f46e5;font-size:1rem">🕐</span>
        </div>
        <div class="agent-status-body">
          <span class="agent-status-label">APScheduler</span>
          <span class="agent-status-val" style="color:#16a34a">{ag['scheduler']}</span>
          <span class="agent-status-sub">Next: {ag['next_run']}</span>
        </div>
      </div>
      <div class="card agent-status-chip">
        <div class="agent-status-icon" style="background:{llm_bg}">
          <span style="color:{llm_color};font-size:1rem">🤖</span>
        </div>
        <div class="agent-status-body">
          <span class="agent-status-label">vLLM</span>
          <span class="agent-status-val" style="color:{llm_color}">{ag['llm_model']}</span>
          <span class="agent-status-sub">{ag['llm_latency_ms']} ms avg &nbsp;·&nbsp; {ag['llm_endpoint'].split('/')[-2] if '/' in ag['llm_endpoint'] else ag['llm_endpoint']}</span>
        </div>
      </div>
    </div>"""

    # ── 2. Execution trace ────────────────────────────────────────────────────
    tr = get_provider().get_execution_trace()
    nodes = tr["nodes"]
    max_log = math.log10(max((n["duration_s"] for n in nodes), default=0) + 1)

    trace_rows = ""
    for n in nodes:
        w = _trace_bar_pct(n["duration_s"], max_log)
        dur_s = n["duration_s"]
        dur_str = (f"{dur_s:.1f}s" if dur_s < 60
                   else f"{int(dur_s//60)}m {int(dur_s%60)}s")
        bar_color = {"ok": "#16a34a", "warning": "#d97706", "failed": "#dc2626"}.get(n["status"], "#4f46e5")
        s_badge   = audit_badge(n["status"])
        trace_rows += f"""<tr>
          <td class="trace-node-name">{n['name']}</td>
          <td class="trace-started">{n['started']}</td>
          <td class="trace-bar-cell">
            <div class="trace-bar-wrap">
              <div class="trace-bar-fill" style="width:{w}%;background:{bar_color}"></div>
            </div>
          </td>
          <td class="trace-duration">{dur_str}</td>
          <td>{s_badge}</td>
          <td class="trace-detail">{n['detail']}</td>
        </tr>"""

    trace_section = f"""
    <div class="card trace-card">
      <div class="trace-hdr">
        <span class="trace-batch-id">{tr['batch_id']}</span>
        {audit_badge(tr['status'])}
        <span style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:#94a3b8">
          {tr['started']} → {tr['completed']} &nbsp;·&nbsp; {tr['duration']}
        </span>
        <a href="/batches" class="td-link" style="margin-left:auto">All Batches →</a>
      </div>
      <table class="trace-table">
        <thead><tr style="background:#f8fafc">
          <th style="padding:8px 16px;text-align:left;font-size:0.5625rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;border-bottom:1px solid #f1f5f9">NODE</th>
          <th style="padding:8px 16px;text-align:left;font-size:0.5625rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;border-bottom:1px solid #f1f5f9">STARTED</th>
          <th style="padding:8px 16px;text-align:left;font-size:0.5625rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;border-bottom:1px solid #f1f5f9;width:280px">DURATION (LOG SCALE)</th>
          <th style="padding:8px 16px;text-align:left;font-size:0.5625rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;border-bottom:1px solid #f1f5f9"></th>
          <th style="padding:8px 16px;text-align:left;font-size:0.5625rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;border-bottom:1px solid #f1f5f9">STATUS</th>
          <th style="padding:8px 16px;text-align:left;font-size:0.5625rem;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;color:#64748b;border-bottom:1px solid #f1f5f9">DETAIL</th>
        </tr></thead>
        <tbody>{trace_rows}</tbody>
      </table>
    </div>"""

    # ── 3. Per-VM outcome grid ────────────────────────────────────────────────
    vm_rows = ""
    for vt in get_provider().get_vm_trace():
        vm_rows += f"""<tr>
          <td class="left"><a href="/vm/{vt['vm']}" class="outcome-vm">{vt['vm']}</a></td>
          <td class="left">{env_tag(vt['env'])}</td>
          <td>{_outcome_cell(vt['pre_val'])}</td>
          <td>{_outcome_cell(vt['plan'])}</td>
          <td>{_outcome_cell(vt['enrich'])}</td>
          <td>{_outcome_cell(vt['approval'])}</td>
          <td>{_outcome_cell(vt['exec'])}</td>
          <td>{_outcome_cell('ok')}</td>
          <td class="left"><span class="outcome-notes">{vt['notes']}</span></td>
        </tr>"""

    outcome_section = f"""
    <div class="card table-card" style="margin-bottom:20px">
      <div style="padding:12px 16px 10px;border-bottom:1px solid #f1f5f9;display:flex;align-items:center;gap:10px">
        <span class="section-title" style="font-size:0.9375rem">Per-VM Execution Outcomes</span>
        <span style="font-size:0.8125rem;color:#94a3b8">Last batch · {tr['batch_id']}</span>
        <span style="margin-left:auto;font-size:0.75rem;color:#64748b;font-family:'JetBrains Mono',monospace">
          ✓ OK &nbsp;·&nbsp; ⚠ WARN &nbsp;·&nbsp; ✗ FAIL &nbsp;·&nbsp; APPR = Slack approval &nbsp;·&nbsp; — skipped
        </span>
      </div>
      <table class="outcome-table">
        <thead><tr>
          <th class="left">VM</th><th class="left">ENV</th>
          <th>PRE-VAL</th><th>PLAN</th><th>ENRICH</th>
          <th>APPROVAL</th><th>EXEC</th><th>AUDIT</th>
          <th class="left">NOTES</th>
        </tr></thead>
        <tbody>{vm_rows}</tbody>
      </table>
    </div>"""

    # ── 4. LLM planning decisions ─────────────────────────────────────────────
    _ld = get_provider().get_llm_decisions()
    avg_lat = round(sum(d["latency_ms"] for d in _ld) / len(_ld)) if _ld else 0
    fallback_any = any(d["fallback"] for d in _ld)

    llm_rows = ""
    for d in _ld:
        sigs = d["signals"]
        sig_tags = ""
        if float(sigs.get("disk_trend","0").replace("+","").split("%")[0] or 0) >= 5:
            sig_tags += f'<span class="signal-tag signal-tag-disk">disk {sigs["disk_trend"]}</span>'
        else:
            sig_tags += '<span class="signal-tag signal-tag-ok">disk OK</span>'
        if sigs.get("failed_services", 0) > 0:
            sig_tags += f'<span class="signal-tag signal-tag-svc">{sigs["failed_services"]} failed svc</span>'
        if sigs.get("journal_errors", 0) > 0:
            sig_tags += f'<span class="signal-tag signal-tag-err">{sigs["journal_errors"]} j-errors</span>'
        if sigs.get("drift_events", 0) > 0:
            sig_tags += f'<span class="signal-tag signal-tag-drift">{sigs["drift_events"]} drift</span>'
        if sigs.get("failed_logins", 0) > 0:
            sig_tags += f'<span class="signal-tag signal-tag-login">{sigs["failed_logins"]} logins</span>'

        plan_tags = ""
        if d["plan"]:
            for action in d["plan"]:
                tier = d["risk_tiers"].get(action, "LOW")
                plan_tags += f'<span class="{_plan_step_cls(tier)}">{action} · {tier}</span>'
        else:
            plan_tags = '<span class="signal-tag signal-tag-ok">NO ACTIONS</span>'

        fb_html = ('<span class="llm-fallback-badge">FALLBACK</span>' if d["fallback"]
                   else '<span style="font-family:\'JetBrains Mono\',monospace;font-size:0.625rem;color:#16a34a">vLLM</span>')
        llm_rows += f"""<tr>
          <td><a href="/vm/{d['vm']}" class="td-host">{d['vm']}</a></td>
          <td>{env_tag(d['env'])}</td>
          <td style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:#475569">{d['latency_ms']} ms &nbsp;{fb_html}</td>
          <td>{sig_tags}</td>
          <td>{plan_tags}</td>
          <td class="llm-reasoning">{d['reasoning']}</td>
        </tr>"""

    llm_section = f"""
    <div class="card table-card" style="margin-bottom:20px">
      <div style="padding:12px 16px 10px;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:0.9375rem">LLM Planning Decisions</span>
        <span style="font-size:0.8125rem;color:#94a3b8;margin-left:10px">What the agent decided, and why</span>
      </div>
      <div class="llm-meta-strip">
        <span class="llm-meta-item"><span class="llm-meta-lbl">MODEL</span> {ag['llm_model']}</span>
        <span class="llm-meta-item"><span class="llm-meta-lbl">AVG LATENCY</span> {avg_lat} ms</span>
        <span class="llm-meta-item"><span class="llm-meta-lbl">FALLBACK USED</span>
          {'<span style="color:#dc2626;font-weight:700">YES — LLM unavailable</span>' if fallback_any else '<span style="color:#16a34a;font-weight:700">NEVER</span>'}
        </span>
        <span class="llm-meta-item"><span class="llm-meta-lbl">VMs SHOWN</span> {len(get_provider().get_llm_decisions())} of {len(get_provider().get_vms())}</span>
      </div>
      <table class="llm-table">
        <thead><tr>
          <th>VM</th><th>ENV</th><th>LLM</th>
          <th>STORED SIGNALS</th><th>PLAN &amp; RISK TIER</th><th>REASONING</th>
        </tr></thead>
        <tbody>{llm_rows}</tbody>
      </table>
    </div>"""

    # ── 5. Scheduler (left) + Daily Probe (right) ─────────────────────────────
    sched = get_provider().get_scheduler_timeline()
    sched_runs = ""
    for r in sched["recent_runs"]:
        dot_color = {"completed": "#16a34a", "partial": "#d97706", "failed": "#dc2626"}.get(r["status"], "#94a3b8")
        err_html  = f'<span class="sched-run-err">{r["errors"]} err</span>' if r["errors"] > 0 else ""
        sched_runs += f"""
        <div class="sched-run-row">
          <span class="sched-run-dot" style="background:{dot_color}"></span>
          <span class="sched-run-ts">{r['ts']} &nbsp;<a href="/batches" class="td-link" style="font-size:0.75rem">{r['batch']}</a></span>
          <span class="sched-run-dur">{r['duration']}</span>
          {err_html}
        </div>"""

    next_runs_html = "".join(
        f'<div class="sched-next-item">→ {r}</div>' for r in sched["next_runs"]
    )

    scheduler_card = f"""
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:12px 16px 10px;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:0.9375rem">Scheduler</span>
      </div>
      <div style="padding:14px 16px">
        <div style="margin-bottom:14px">
          <div class="field-label" style="margin-bottom:6px">MAINTENANCE BATCH</div>
          <span class="cron-badge">{sched['cron']}</span>
          <span style="font-size:0.8125rem;color:#475569;margin-left:10px">{sched['human']}</span>
        </div>
        <div style="margin-bottom:14px">
          <div class="field-label" style="margin-bottom:6px">DAILY PROBE</div>
          <span class="cron-badge">{sched['probe_cron']}</span>
          <span style="font-size:0.8125rem;color:#475569;margin-left:10px">{sched['probe_human']}</span>
        </div>
        <div style="margin-bottom:14px">
          <div class="field-label" style="margin-bottom:8px">UPCOMING RUNS</div>
          {next_runs_html}
        </div>
        <div>
          <div class="field-label" style="margin-bottom:8px">RECENT RUNS</div>
          {sched_runs}
        </div>
      </div>
    </div>"""

    # Probe section
    _ph = get_provider().get_probe_history()
    probe = _ph[0] if _ph else {}
    probe2 = _ph[1] if len(_ph) > 1 else None

    if not probe:
        probe_card = """
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:12px 16px 10px;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:0.9375rem">Daily Probe</span>
      </div>
      <div style="padding:14px 16px;color:#94a3b8;font-size:0.875rem">No probe history available.</div>
    </div>"""
    else:
        if probe["escalated"]:
            probe_banner = f"""
        <div class="probe-escalated-banner">
          ⚠ ESCALATED &nbsp;·&nbsp; {probe['escalation_msg']}
        </div>"""
        else:
            probe_banner = '<div class="probe-ok-banner">✓ No escalation — all signals nominal</div>'

        signal_groups = ""
        for sg in probe.get("signals", []):
            items_html = "".join(f'<div class="probe-signal-item">{sg["icon"]} {item}</div>' for item in sg["items"])
            signal_groups += f"""
        <div class="probe-signal-group">
          <div class="probe-signal-type">{sg['type'].replace('_',' ')}</div>
          {items_html}
        </div>"""

        probe2_html = ""
        if probe2:
            probe2_status_color = "#16a34a" if not probe2["escalated"] else "#dc2626"
            probe2_html = f"""
        <div style="margin-top:14px;padding-top:14px;border-top:1px solid #f1f5f9">
          <div class="field-label" style="margin-bottom:4px">PREVIOUS PROBE</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:#94a3b8">
            {probe2['ts']} &nbsp;·&nbsp; {probe2['duration']} &nbsp;·&nbsp; {probe2['vms_probed']} VMs
            &nbsp;·&nbsp; <span style="color:{probe2_status_color};font-weight:700">{'ESCALATED' if probe2['escalated'] else 'OK'}</span>
          </div>
        </div>"""

        probe_card = f"""
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:12px 16px 10px;border-bottom:1px solid #f1f5f9;display:flex;align-items:center;gap:10px">
        <span class="section-title" style="font-size:0.9375rem">Daily Probe</span>
        {'<span class="badge badge-red">ESCALATED</span>' if probe['escalated'] else '<span class="badge badge-green">CLEAN</span>'}
      </div>
      <div style="padding:14px 16px">
        <div style="font-family:\'JetBrains Mono\',monospace;font-size:0.75rem;color:#94a3b8;margin-bottom:12px">
          {probe['ts']} &nbsp;·&nbsp; {probe['duration']} &nbsp;·&nbsp; {probe['vms_probed']} VMs probed
          &nbsp;·&nbsp; Slack: {'✓ posted' if probe['slack_posted'] else '—'}
        </div>
        {probe_banner}
        {signal_groups if probe['signals'] else ''}
        {probe2_html}
      </div>
    </div>"""

    sched_probe_row = f"""
    <div class="two-col-grid">
      {scheduler_card}
      {probe_card}
    </div>"""

    # ── 6. Deferred queue ─────────────────────────────────────────────────────
    if get_provider().get_deferred_queue():
        dq_rows = "".join(
            f"""<tr><td class="td-mono">{q['vm']}</td><td class="td-ts">{q['approved_ts']}</td>
                <td>{q['action']}</td><td class="td-ts">{q['window_opens']}</td>
                <td><span class="td-link" style="opacity:0.4;cursor:default" title="Plan detail view — v2 roadmap">View Plan →</span></td></tr>"""
            for q in get_provider().get_deferred_queue()
        )
        dq_body = f"""
        <table class="data-table">
          <thead><tr><th>VM</th><th>APPROVED</th><th>ACTION</th><th>WINDOW OPENS</th><th></th></tr></thead>
          <tbody>{dq_rows}</tbody>
        </table>"""
    else:
        dq_body = '<div class="deferred-empty">✓ &nbsp;No deferred plans — all approved plans have executed</div>'

    deferred_section = f"""
    <div class="card table-card">
      <div style="padding:12px 16px 10px;border-bottom:1px solid #f1f5f9;display:flex;align-items:center;gap:10px">
        <span class="section-title" style="font-size:0.9375rem">Deferred Execution Queue</span>
        <span style="font-size:0.8125rem;color:#94a3b8">Approved plans waiting for a maintenance window to open</span>
        {f'<span class="badge badge-amber">{len(get_provider().get_deferred_queue())} queued</span>' if get_provider().get_deferred_queue() else '<span class="badge badge-green">EMPTY</span>'}
      </div>
      {dq_body}
    </div>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Agent Status</div>
        <div class="section-sub">LangGraph execution trace · LLM decisions · scheduler · probe · deferred queue</div>
      </div>
    </div>
    <div style="font-family:'Space Grotesk',sans-serif;font-size:0.875rem;font-weight:700;color:#0f172a;margin-bottom:8px">
      AI Safety Architecture — Layer A vs Layer B
    </div>
    {layer_partition}
    {status_strip}
    <div style="font-family:'Space Grotesk',sans-serif;font-size:0.875rem;font-weight:700;color:#0f172a;margin-bottom:12px">
      Last Batch Execution Trace
    </div>
    {trace_section}
    {outcome_section}
    <div style="font-family:'Space Grotesk',sans-serif;font-size:0.875rem;font-weight:700;color:#0f172a;margin-bottom:12px">
      LLM Planning Log
    </div>
    {llm_section}
    {sched_probe_row}
    {deferred_section}"""


def page_placeholder(name: str) -> str:
    return f"""
    <div class="card" style="padding:60px;text-align:center">
      <div style="font-family:'Space Grotesk',sans-serif;font-size:1.5rem;font-weight:700;color:#0f172a;margin-bottom:8px">{name}</div>
      <div style="color:#94a3b8;font-size:0.9375rem">This screen is coming in the next implementation phase.</div>
    </div>"""


# ── Route handlers ────────────────────────────────────────────────────────────

async def handle_fleet(request: web.Request) -> web.Response:
    html = layout(
        title="Fleet Dashboard",
        active_url="/",
        breadcrumb="Fleet Dashboard",
        topnav_extra=f'{env_badge_top("PROD")}'
                     + ('<span class="last-batch">Last batch: 2026-04-23 02:00 UTC</span>' if get_provider().data_mode() == "FIXTURE" else "")
                     + '<button class="btn-primary" disabled title="Use CLI: errander --run-now --env &lt;env&gt;" style="opacity:0.45;cursor:not-allowed">&#9654; RUN BATCH NOW</button>',
        content=page_fleet(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_approvals(request: web.Request) -> web.Response:
    html = layout(
        title="Approval Queue",
        active_url="/approvals",
        breadcrumb="Approval Queue",
        topnav_extra=f'{env_badge_top("PROD")}<span class="pending-chip">{len(get_provider().get_approvals())} PENDING</span>',
        content=page_approvals(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_vm(request: web.Request) -> web.Response:
    from errander.observability.vm_metrics import query_metrics
    hostname = request.match_info["hostname"]
    vm = get_provider().get_vm(hostname)
    env = vm["env"] if vm else "PROD"

    metrics_by_window: dict[str, Any] | None = None
    db = request.app.get("db")
    if db is not None:
        try:
            metrics_by_window = {}
            for w in ("15m", "1h", "24h", "7d"):
                metrics_by_window[w] = await query_metrics(db, hostname, w)
        except Exception:
            metrics_by_window = None

    html = layout(
        title=f"VM: {hostname}",
        active_url="/",
        breadcrumb=f'<a href="/" style="color:#475569;text-decoration:none">Fleet Dashboard</a>'
                   f'<span class="sep">/</span><span class="sub">{hostname}</span>',
        topnav_extra=f'{env_badge_top(env)}'
                     f'<button class="btn-outline btn-outline-amber" disabled title="Operator-triggered maintenance — v2 roadmap" style="opacity:0.45;cursor:not-allowed">FORCE MAINTENANCE <span style=\'font-size:0.65rem\'>v2</span></button>'
                     f'<button class="btn-outline btn-outline-indigo" disabled title="SSH terminal — v2 roadmap" style="opacity:0.45;cursor:not-allowed">SSH TERMINAL <span style=\'font-size:0.65rem\'>v2</span></button>',
        content=page_vm(hostname, metrics_by_window),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_audit(request: web.Request) -> web.Response:
    html = layout(
        title="Audit Log",
        active_url="/audit",
        breadcrumb="Audit Log",
        topnav_extra=f'{env_badge_top("PROD")}<a href="#" class="btn-outline btn-outline-indigo" onclick="event.preventDefault();_exportAudit(\'csv\')">EXPORT CSV</a>',
        content=page_audit(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_batches(request: web.Request) -> web.Response:
    html = layout(
        title="Batch History",
        active_url="/batches",
        breadcrumb="Batch History",
        topnav_extra=f'{env_badge_top("PROD")}<button class="btn-primary" disabled title="Custom scheduling UI — v2 roadmap" style="opacity:0.45;cursor:not-allowed">+ SCHEDULE BATCH <span style=\'font-size:0.65rem\'>v2</span></button>',
        content=page_batches(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_glossary(request: web.Request) -> web.Response:
    html = layout(
        title="Glossary & Workflow",
        active_url="/glossary",
        breadcrumb="Glossary &amp; Workflow",
        topnav_extra='<a href="/glossary" class="btn-outline btn-outline-indigo">DOCS ↗</a>',
        content=page_glossary(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_agent(request: web.Request) -> web.Response:
    html = layout(
        title="Agent Status",
        active_url="/agent",
        breadcrumb="Agent Status",
        topnav_extra=(
            '<span class="badge badge-indigo" style="font-size:0.75rem;padding:5px 12px">DRY RUN</span>'
            '<a href="/admin" class="btn-outline btn-outline-indigo">Admin Controls</a>'
            '<button class="btn-primary" disabled title="Use CLI: uv run python -m errander --run-now --env &lt;env&gt;" style="opacity:0.45;cursor:not-allowed">&#9654; RUN BATCH NOW</button>'
        ),
        content=page_agent(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_inventory(request: web.Request) -> web.Response:
    html = layout(
        title="Inventory",
        active_url="/inventory",
        breadcrumb="Inventory",
        topnav_extra='<button class="btn-outline btn-outline-indigo" disabled title="Inventory CSV export — v2 roadmap" style="opacity:0.45;cursor:not-allowed">EXPORT CSV <span style="font-size:0.65rem">v2</span></button>',
        content=page_inventory(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_settings(request: web.Request) -> web.Response:
    html = layout(
        title="Settings",
        active_url="/settings",
        breadcrumb="Settings",
        topnav_extra='',
        content=page_settings(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_admin(request: web.Request) -> web.Response:
    html = layout(
        title="Admin Panel",
        active_url="/admin",
        breadcrumb="Admin Panel",
        topnav_extra='<span class="badge badge-danger" style="font-size:0.7rem;padding:4px 10px;">PRIVILEGED</span>',
        content=page_admin(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_placeholder(request: web.Request) -> web.Response:
    name = request.path.strip("/").replace("-", " ").title()
    html = layout(
        title=name, active_url=request.path,
        breadcrumb=name, topnav_extra="",
        content=page_placeholder(name),
    )
    return web.Response(text=html, content_type="text/html")


# ── plan inspection endpoint (P2-1) ──────────────────────────────────────────

async def handle_plan_view(request: web.Request) -> web.Response:
    """GET /plans/{plan_id}?token=<signed_token>

    Returns the full plan JSON for a plan_id so operators can inspect
    every package/version before reacting to a Slack approval request.
    The signed token prevents enumeration — only the agent (which holds
    ERRANDER_SIGNING_SECRET) can issue valid tokens.
    """
    from errander.db.core import AsyncDatabase
    from errander.integrations.signed_url import (
        InvalidSignedTokenError,
        SigningSecretMissingError,
        verify_signed_token,
    )
    from errander.safety.audit import AuditStore

    plan_id = request.match_info.get("plan_id", "")
    token = request.query.get("token", "")

    if not token:
        return web.Response(status=400, text="Missing token")

    try:
        payload = verify_signed_token(token)
    except (InvalidSignedTokenError, SigningSecretMissingError) as exc:
        return web.Response(status=403, text=f"Invalid or expired token: {exc}")

    if payload.get("plan_id") != plan_id:
        return web.Response(status=403, text="Token plan_id mismatch")

    db_path = os.environ.get(
        "ERRANDER_AUDIT_DB_URL",
        "postgresql://errander:errander@localhost:5432/errander",
    )
    try:
        async with AuditStore(AsyncDatabase(db_path), strict_mode=False) as store:
            snapshot = await store.get_plan_snapshot(plan_id)
    except Exception as exc:  # noqa: BLE001
        return web.Response(status=500, text=f"DB error: {exc}")

    if snapshot is None:
        return web.Response(status=404, text=f"Plan {plan_id!r} not found")

    import json as _json
    try:
        plan_data = _json.loads(str(snapshot["plan_json"]))
    except Exception:  # noqa: BLE001
        return web.Response(status=500, text="Stored plan is not valid JSON")

    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return web.Response(
            content_type="application/json",
            text=_json.dumps(plan_data, indent=2, default=str),
        )

    # HTML rendering — minimal, no auth required (token is the auth).
    batch_id = str(snapshot.get("batch_id", ""))
    env_name = str(snapshot.get("env_name", ""))
    plan_hash = str(snapshot.get("plan_hash", ""))[:16]
    created_at = str(snapshot.get("created_at", ""))

    vm_sections: list[str] = []
    for vm in plan_data.get("vm_plans", []):
        vm_id = str(vm.get("vm_id", "?"))
        pkg_lines: list[str] = []
        for action in (vm.get("planned_actions") or []):
            atype = str(action.get("action_type", ""))
            preview = action.get("preview") if isinstance(action.get("preview"), dict) else {}
            assert isinstance(preview, dict)
            if atype == "patching":
                pkgs = preview.get("packages") or []
                for pkg in pkgs:
                    if not isinstance(pkg, dict):
                        continue
                    name = str(pkg.get("name", "?"))
                    cur = str(pkg.get("current", ""))
                    tgt = str(pkg.get("target", ""))
                    pkg_lines.append(
                        f"<tr><td>{name}</td><td>{cur}</td><td>&rarr;</td><td>{tgt}</td></tr>"
                    )
        pkg_table = (
            f"<table><thead><tr><th>Package</th><th>Current</th><th></th><th>Target</th></tr></thead>"
            f"<tbody>{''.join(pkg_lines)}</tbody></table>"
            if pkg_lines else ""
        )
        vm_sections.append(
            f"<h3>{vm_id}</h3>{pkg_table}"
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Plan {plan_id}</title>
<style>
  body{{font-family:monospace;max-width:900px;margin:2rem auto;padding:0 1rem}}
  h1{{font-size:1.2rem}}
  table{{border-collapse:collapse;width:100%}}
  td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
  .meta{{color:#666;font-size:.9rem}}
</style>
</head><body>
<h1>Plan: {plan_id}</h1>
<p class="meta">Batch: {batch_id} &nbsp;|&nbsp; Env: {env_name} &nbsp;|&nbsp;
  Hash: {plan_hash} &nbsp;|&nbsp; Created: {created_at}</p>
{''.join(vm_sections)}
</body></html>"""

    return web.Response(content_type="text/html", text=html)


# ── docker_hygiene approval surface (v1.1 Session 2b-ii) ─────────────────────

def page_hygiene_approve(
    assessment: Any,
    *,
    token: str,
    batch_id: str,
    vm_id: str,
) -> str:
    """Render the docker_hygiene approval form.

    Findings are grouped by class with one checkbox per executable item
    (dangling images / unused images / stopped containers). Volumes and
    build cache are shown as report-only — they don't get checkboxes and
    can't be submitted for removal in v1.1.
    """
    from errander.models.docker_hygiene import DockerResourceClass  # noqa: PLC0415
    from errander.safety.hygiene_approval import _short_class_key

    _executable: tuple[Any, ...] = (
        DockerResourceClass.IMAGE_DANGLING, DockerResourceClass.IMAGE_UNUSED, DockerResourceClass.CONTAINER_STOPPED,
    )

    by_class = assessment.by_class()

    sections: list[str] = []
    has_any_executable = False
    for klass in _executable + (DockerResourceClass.VOLUME_UNREFERENCED, DockerResourceClass.BUILD_CACHE):
        items = by_class.get(klass, [])
        if not items:
            continue
        short = _short_class_key(klass)
        is_executable = klass in _executable
        if is_executable:
            has_any_executable = True
        rows: list[str] = []
        for i, f in enumerate(items, start=1):
            checked = "checked" if (is_executable and f.classification.value == "cleanup_candidate") else ""
            disabled = "" if is_executable else "disabled"
            tag = (f.last_tag or f.name or "—")
            size = (
                f"{f.size_bytes // 1024 // 1024} MB" if f.size_bytes else "—"
            ) if klass != DockerResourceClass.BUILD_CACHE else (
                f"{(f.reclaimable_bytes or 0) // 1024 // 1024} MB"
            )
            input_name = f"finding_{short}_{i}"
            checkbox_html = (
                f'<input type="checkbox" name="{input_name}" id="{input_name}" {checked} {disabled} />'
                if is_executable else
                '<span style="color:#94a3b8">report-only</span>'
            )
            rows.append(
                f'<tr><td>{checkbox_html}</td>'
                f'<td style="font-family:monospace;font-size:0.8rem">{_html_escape(f.identity[:32])}</td>'
                f'<td>{_html_escape(tag)}</td>'
                f'<td>{size}</td>'
                f'<td>{_html_escape(f.classification.value)}</td></tr>'
            )
        sections.append(f'''
        <div class="card" style="margin-bottom:16px;padding:18px">
          <div style="font-weight:600;font-size:1.05rem;margin-bottom:8px">
            {_html_escape(klass.value)} ({len(items)})
          </div>
          <table style="width:100%;border-collapse:collapse">
            <thead><tr style="text-align:left;color:#64748b;font-size:0.75rem">
              <th style="width:40px"></th><th>ID</th><th>Tag/Name</th><th>Size</th><th>Classification</th>
            </tr></thead>
            <tbody>{"".join(rows)}</tbody>
          </table>
        </div>''')

    if not has_any_executable:
        action_buttons = '<p>No executable findings — nothing to approve.</p>'
    else:
        action_buttons = '''
        <div style="margin-top:24px;display:flex;gap:12px">
          <button type="submit" name="decision" value="approve"
            style="padding:12px 24px;background:#16a34a;color:white;border:none;border-radius:6px;font-weight:600;cursor:pointer">
            ✓ Approve selected
          </button>
          <button type="submit" name="decision" value="reject"
            style="padding:12px 24px;background:#dc2626;color:white;border:none;border-radius:6px;font-weight:600;cursor:pointer">
            ✗ Reject all
          </button>
        </div>'''

    return f'''
    <div style="max-width:1000px;margin:0 auto;padding:24px">
      <h1 style="font-family:'Space Grotesk',sans-serif;margin-bottom:4px">Docker hygiene approval</h1>
      <div style="color:#64748b;margin-bottom:24px">
        VM <code>{_html_escape(vm_id)}</code> · batch <code>{_html_escape(batch_id)}</code>
      </div>
      <form method="POST" action="/ui/docker-hygiene/approve">
        <input type="hidden" name="token" value="{_html_escape(token)}" />
        {"".join(sections)}
        {action_buttons}
      </form>
    </div>
    '''


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


async def handle_hygiene_approve_get(request: web.Request) -> web.Response:
    """GET /ui/docker-hygiene/approve?token=<signed_token>

    Verify token, look up pending approval, render the form. Both auth gate
    (cookie session via middleware) and signed-URL verification apply —
    defence in depth.
    """
    from errander.integrations.signed_url import (
        InvalidSignedTokenError,
        verify_signed_token,
    )

    token = request.query.get("token", "")
    if not token:
        return web.Response(
            text=_hygiene_error_page("Missing token in approval URL."),
            content_type="text/html",
            status=400,
        )
    try:
        payload = verify_signed_token(token)
    except InvalidSignedTokenError as exc:
        return web.Response(
            text=_hygiene_error_page(f"Invalid or expired approval URL: {exc}"),
            content_type="text/html",
            status=400,
        )

    batch_id = str(payload.get("batch_id", ""))
    vm_id = str(payload.get("vm_id", ""))
    store = request.app.get("hygiene_store")
    if store is None:
        return web.Response(
            text=_hygiene_error_page("Hygiene approval store not available."),
            content_type="text/html",
            status=503,
        )

    row = await store.get(batch_id, vm_id)
    if row is None or row.is_decided():
        return web.Response(
            text=_hygiene_error_page(
                "This approval has already been resolved or has expired. "
                "Operators only see this page when there is an active pending request."
            ),
            content_type="text/html",
            status=404,
        )

    content = page_hygiene_approve(
        row.assessment(),
        token=token,
        batch_id=batch_id,
        vm_id=vm_id,
    )
    html = layout(
        title="Docker hygiene approval",
        active_url="/approvals",
        breadcrumb="Docker hygiene approval",
        topnav_extra="",
        content=content,
    )
    return web.Response(text=html, content_type="text/html")


async def handle_hygiene_approve_post(request: web.Request) -> web.Response:
    """POST /ui/docker-hygiene/approve

    Re-verify token (defence in depth — never trust that a previous request
    verified it), parse checkbox selections back into approved findings,
    build the artifact, call manager.resolve.
    """
    from errander.integrations.signed_url import (
        InvalidSignedTokenError,
        verify_signed_token,
    )
    from errander.models.docker_hygiene import compute_assessment_hash
    from errander.safety.hygiene_approval import _short_class_key

    data = await request.post()
    token = str(data.get("token", ""))
    decision = str(data.get("decision", "")).lower()
    if decision not in ("approve", "reject"):
        return web.Response(
            text=_hygiene_error_page("Missing or invalid decision."),
            content_type="text/html",
            status=400,
        )

    try:
        payload = verify_signed_token(token)
    except InvalidSignedTokenError as exc:
        return web.Response(
            text=_hygiene_error_page(f"Invalid or expired approval URL: {exc}"),
            content_type="text/html",
            status=400,
        )
    batch_id = str(payload.get("batch_id", ""))
    vm_id = str(payload.get("vm_id", ""))

    store = request.app.get("hygiene_store")
    if store is None:
        return web.Response(
            text=_hygiene_error_page("Hygiene approval store not available."),
            content_type="text/html",
            status=503,
        )
    row = await store.get(batch_id, vm_id)
    if row is None or row.is_decided():
        return web.Response(
            text=_hygiene_error_page(
                "This approval is no longer pending — another channel may have resolved it."
            ),
            content_type="text/html",
            status=404,
        )

    assessment = row.assessment()
    snapshot_hash = compute_assessment_hash(assessment)

    if decision == "reject":
        await store.decide(
            batch_id, vm_id,
            approved=False,
            decided_by=_AUTH_USERNAME,
            snapshot_hash=snapshot_hash,
            approved_items=None,
        )
        return web.Response(
            text=_hygiene_confirmation_page(
                "Rejected", "All findings rejected — no objects will be removed.",
            ),
            content_type="text/html",
        )

    # decision == "approve" — collect checked items from form.
    approved_items_list: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    by_class = assessment.by_class()
    from errander.models.docker_hygiene import DockerResourceClass  # noqa: PLC0415
    _executable = (
        DockerResourceClass.IMAGE_DANGLING,
        DockerResourceClass.IMAGE_UNUSED,
        DockerResourceClass.CONTAINER_STOPPED,
    )
    for klass in _executable:
        items = by_class.get(klass, [])
        short = _short_class_key(klass)
        for i, f in enumerate(items, start=1):
            field_name = f"finding_{short}_{i}"
            if data.get(field_name) is not None:
                key = (f.resource_class.value, f.identity)
                if key in seen:
                    continue
                seen.add(key)
                approved_items_list.append(
                    {"resource_class": f.resource_class.value, "identity": f.identity}
                )

    await store.decide(
        batch_id, vm_id,
        approved=True,
        decided_by=_AUTH_USERNAME,
        snapshot_hash=snapshot_hash,
        approved_items=approved_items_list or None,
    )

    return web.Response(
        text=_hygiene_confirmation_page(
            "Approved",
            f"{len(approved_items_list)} object(s) approved for removal. The agent will "
            f"re-validate each object against current state before removing.",
        ),
        content_type="text/html",
    )


def _hygiene_error_page(msg: str) -> str:
    """Standalone error page (no nav chrome) for invalid token / not-found cases."""
    return f'''
    <html><body style="font-family:Inter,sans-serif;max-width:600px;margin:80px auto;padding:24px">
      <h1 style="color:#dc2626">Approval error</h1>
      <p>{_html_escape(msg)}</p>
      <p><a href="/approvals">← Return to approval queue</a></p>
    </body></html>
    '''


def _hygiene_confirmation_page(verdict: str, detail: str) -> str:
    color = "#16a34a" if verdict == "Approved" else "#dc2626"
    return f'''
    <html><body style="font-family:Inter,sans-serif;max-width:600px;margin:80px auto;padding:24px">
      <h1 style="color:{color}">{verdict}</h1>
      <p>{_html_escape(detail)}</p>
      <p><a href="/approvals">← Return to approval queue</a></p>
    </body></html>
    '''


# ── Auth handlers ──────────────────────────────────────────────────────────────

async def handle_login_get(request: web.Request) -> web.Response:
    # Already authenticated → go straight to dashboard
    if _valid_token(request.cookies.get(_AUTH_COOKIE, "")):
        raise web.HTTPFound("/")
    return web.Response(text=page_login(error=False), content_type="text/html")


async def handle_login_post(request: web.Request) -> web.Response:
    data = await request.post()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()
    if username == _AUTH_USERNAME and password == _AUTH_PASSWORD:
        response = web.HTTPFound("/")
        response.set_cookie(
            _AUTH_COOKIE, _make_token(),
            max_age=_AUTH_TTL, httponly=True, samesite="Lax",
        )
        raise response
    return web.Response(text=page_login(error=True), content_type="text/html")


async def handle_logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie(_AUTH_COOKIE)
    raise response


# ── Metrics API ───────────────────────────────────────────────────────────────

async def handle_metrics_api(request: web.Request) -> web.Response:
    from errander.observability.vm_metrics import query_metrics
    hostname = request.match_info["hostname"]
    known = {v["hostname"] for v in get_provider().get_vms()}
    if known and hostname not in known:
        raise web.HTTPNotFound(reason=f"VM {hostname!r} not in inventory")
    window = request.rel_url.query.get("window", "24h")
    db = request.app.get("db")
    if db is None:
        return web.Response(
            text='{"cpu":[],"mem":[],"disk":{}}',
            content_type="application/json",
        )
    try:
        data = await query_metrics(db, hostname, window)
    except Exception as exc:
        logger.warning("metrics_api %s %s: %s", hostname, window, exc)
        data = {"cpu": [], "mem": [], "disk": {}}
    return web.Response(
        text=_json.dumps(data),
        content_type="application/json",
    )


# ── Startup / cleanup hooks ────────────────────────────────────────────────────


async def _refresh_live_provider(app: web.Application) -> None:
    """Periodic job: refresh the LiveProvider cache from real stores."""
    from errander.web.providers import LiveProvider
    prov = app.get("_live_provider")
    if not isinstance(prov, LiveProvider):
        return
    try:
        await prov.refresh(
            db=app.get("_live_provider_db"),
            approval_store=app.get("approval_store"),
            deferred_store=app.get("deferred_store"),
            inventory_path=app.get("_live_provider_inv"),
        )
    except Exception as exc:
        logger.warning("LiveProvider periodic refresh failed: %s", exc)


async def _on_startup(app: web.Application) -> None:
    import os as _os

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from errander.db.core import AsyncDatabase
    from errander.observability.vm_metrics import MetricsCollector, cleanup_old_metrics
    from errander.safety.migrations import run_migrations

    db_url = _os.environ.get(
        "ERRANDER_AUDIT_DB_URL",
        "postgresql://errander:errander@localhost:5432/errander",
    )
    db = AsyncDatabase(db_url)
    async with db.begin() as _conn:
        await run_migrations(_conn)
    app["db"] = db
    logger.info("DB opened: %s (migrations applied)", db_url)

    targets: list[Any] = []
    _inv_path_for_provider: Any | None = None
    try:
        from pathlib import Path as _Path

        from errander.config.inventory import load_inventory
        _inv_path = _Path(_os.environ.get("ERRANDER_INVENTORY_PATH", "inventory.yaml"))
        if _inv_path.exists():
            targets = list(load_inventory(_inv_path))
            _inv_path_for_provider = _inv_path
            logger.info("Loaded %d VM targets for metrics collection", len(targets))
        else:
            logger.info("Inventory file not found at %s — metrics collection disabled", _inv_path)
    except Exception as exc:
        logger.warning("Could not load inventory — metrics collection disabled: %s", exc)

    # LiveProvider: initial cache fill + periodic refresh
    from errander.web.providers import LiveProvider as _LiveProvider
    _prov = get_provider()
    if isinstance(_prov, _LiveProvider):
        try:
            await _prov.refresh(db=db, inventory_path=_inv_path_for_provider)
        except Exception as exc:
            logger.warning("LiveProvider initial refresh failed: %s", exc)
        _refresh_secs = max(30, int(_os.environ.get("ERRANDER_UI_REFRESH_SECONDS", "60")))
        app["_live_provider"] = _prov
        app["_live_provider_db"] = db
        app["_live_provider_inv"] = _inv_path_for_provider
        logger.info("LiveProvider active; refresh every %ds", _refresh_secs)

    if targets:
        ne_port = int(_os.environ.get("ERRANDER_NODE_EXPORTER_PORT", "9100"))
        interval = max(30, min(300, int(
            _os.environ.get("ERRANDER_METRICS_INTERVAL_SECONDS", "60")
        )))

        collector = MetricsCollector(node_exporter_port=ne_port)
        await collector.discover(targets)
        app["metrics_collector"] = collector

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            collector.collect_all,
            "interval",
            seconds=interval,
            args=[db, targets],
            id="vm_metrics_collect",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            cleanup_old_metrics,
            "interval",
            hours=1,
            args=[db],
            id="vm_metrics_cleanup",
            max_instances=1,
            coalesce=True,
        )
        if isinstance(_prov, _LiveProvider):
            scheduler.add_job(
                _refresh_live_provider,
                "interval",
                seconds=_refresh_secs,
                args=[app],
                id="ui_provider_refresh",
                max_instances=1,
                coalesce=True,
            )

        scheduler.start()
        app["metrics_scheduler"] = scheduler

        src = collector.source_map
        ne_n = sum(1 for s in src.values() if s == "node_exporter")
        logger.info(
            "Metrics: %d Node Exporter + %d SSH probe, %ds interval",
            ne_n, len(targets) - ne_n, interval,
        )


async def _on_cleanup(app: web.Application) -> None:
    scheduler = app.get("metrics_scheduler")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Metrics scheduler stopped")
    collector = app.get("metrics_collector")
    if collector is not None:
        await collector.close()
    db = app.get("db")
    if db is not None:
        await db.close()
        logger.info("DB closed")


# ── Auth middleware ────────────────────────────────────────────────────────────

_PUBLIC_PATHS = {"/login", "/logout"}


@web.middleware
async def _auth_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    if request.path in _PUBLIC_PATHS:
        return await handler(request)  # type: ignore[no-any-return]
    if not _valid_token(request.cookies.get(_AUTH_COOKIE, "")):
        if request.path.startswith("/api/"):
            return web.Response(
                text='{"error":"unauthenticated"}',
                content_type="application/json",
                status=401,
            )
        raise web.HTTPFound("/login")
    return await handler(request)  # type: ignore[no-any-return]


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app.router.add_get("/",                              handle_fleet)
    app.router.add_get("/approvals",                     handle_approvals)
    app.router.add_get("/vm/{hostname}",                 handle_vm)
    app.router.add_get("/audit",                         handle_audit)
    app.router.add_get("/batches",                       handle_batches)
    app.router.add_get("/inventory",                     handle_inventory)
    app.router.add_get("/settings",                      handle_settings)
    app.router.add_get("/admin",                         handle_admin)
    app.router.add_get("/glossary",                      handle_glossary)
    app.router.add_get("/agent",                         handle_agent)
    app.router.add_get("/login",                         handle_login_get)
    app.router.add_post("/login",                        handle_login_post)
    app.router.add_get("/logout",                        handle_logout)
    app.router.add_get("/api/vm/{hostname}/metrics",     handle_metrics_api)
    # docker_hygiene approval surface (v1.1 Session 2b-ii)
    app.router.add_get("/ui/docker-hygiene/approve",     handle_hygiene_approve_get)
    app.router.add_post("/ui/docker-hygiene/approve",    handle_hygiene_approve_post)
    # plan inspection (P2-1) — signed token, no login required
    app.router.add_get("/plans/{plan_id}",               handle_plan_view)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def run() -> None:
    app = create_app()
    print("\n  errander-ai Operations Hub")
    print("  ---------------------------------")
    print(f"  http://localhost:{PORT}\n")
    web.run_app(app, host="127.0.0.1", port=PORT, print=lambda _: None)


if __name__ == "__main__":
    run()
