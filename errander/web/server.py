"""errander — Operations Hub development UI server."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from .data import ACTIVE_BATCH, APPROVALS, AUDIT_EVENTS, BATCHES, VM_ACTIONS, VMS

PORT = 8099

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

/* ── Pagination ── */
.pagination { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 14px; border-top: 1px solid #f1f5f9; font-size: 0.875rem; color: #475569; }
.pg-btn { padding: 5px 12px; border: 1.5px solid #e2e8f0; border-radius: 6px; background: #fff; cursor: pointer; font-size: 0.8125rem; color: #475569; text-decoration: none; }
.pg-btn:hover { border-color: #4f46e5; color: #4f46e5; }
.pg-current { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: #0f172a; padding: 0 8px; }

/* ── Responsive tweaks ── */
@media (max-width: 1100px) { .vm-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 900px)  { .kpi-grid { grid-template-columns: repeat(2, 1fr); } .detail-top { grid-template-columns: 1fr; } }

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
.wf-diagram { position: relative; width: 960px; height: 760px; margin: 0 auto; }
.wf-svg { position: absolute; top: 0; left: 0; width: 960px; height: 760px; pointer-events: none; overflow: visible; }
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
"""

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

APPROVAL_COUNT = len(APPROVALS)

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
            ("Audit DB Path",  "errander.sqlite", False),
            ("DB Size",        "2.4 MB",          False),
            ("Log Retention",  "90 days",         False),
            ("Strict Mode",    "ON",              False),
        ],
    },
]

_HEALTH_CHECKS = [
    {"label": "vLLM Endpoint",    "detail": "http://10.0.0.100:8000/v1",  "status": "ok",   "meta": "42 ms"},
    {"label": "Slack API",        "detail": "api.slack.com",               "status": "ok",   "meta": "outbound HTTPS"},
    {"label": "Audit DB",         "detail": "errander.sqlite · 2.4 MB",   "status": "ok",   "meta": "writable"},
    {"label": "SSH Keys",         "detail": "11 / 11 key files present",   "status": "ok",   "meta": "/keys/"},
    {"label": "APScheduler",      "detail": "next: 2026-05-14 02:00 UTC",  "status": "ok",   "meta": "running"},
]

_ACTIVE_LOCKS: list[dict[str, Any]] = []  # empty = clean state; add dicts with vm/since/path to simulate stuck locks

_OVERRIDES = [
    ("Dry Run Mode",            "All batches simulate actions without executing on real VMs.",        True),
    ("Force Maintenance Window","Allow batches outside configured windows. Requires --force reason.", False),
    ("Skip Approval Gate",      "Bypass Slack approval for High-risk actions. Emergency use only.",  False),
    ("Strict Audit Mode",       "Halt agent if any audit write fails — integrity over execution.",    True),
]


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
            ab = f'<span class="nav-badge">{APPROVAL_COUNT}</span>' if label == "Approval Queue" and APPROVAL_COUNT else ""
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
      <div class="sys-version">v1.0.0 &nbsp;·&nbsp; SQLite audit</div>
    </div>
  </aside>
  <div class="shell">
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

def page_fleet() -> str:
    # KPI
    healthy = sum(1 for v in VMS if v["status"] == "ok")
    warnings = sum(1 for v in VMS if v["status"] == "warning")
    needs_approval = sum(1 for v in VMS if v["status"] == "pending")
    kpis = f"""
    <div class="kpi-grid">
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Total VMs</div>
        <div class="kpi-value" style="color:#0f172a">{len(VMS)}</div>
        <div class="kpi-subtitle">{len(set(v['env'] for v in VMS))} environments</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Healthy</div>
        <div class="kpi-value" style="color:#16a34a">{healthy}</div>
        <div class="kpi-subtitle">{round(healthy/len(VMS)*100)}% of fleet</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#d97706">
        <div class="kpi-label">Warnings</div>
        <div class="kpi-value" style="color:#d97706">{warnings}</div>
        <div class="kpi-subtitle">Action recommended</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#7c3aed">
        <div class="kpi-label">Needs Approval</div>
        <div class="kpi-value" style="color:#7c3aed">{needs_approval}</div>
        <div class="kpi-subtitle">Expires in &lt; 30 min</div>
      </div>
    </div>"""

    # Active batch
    b = ACTIVE_BATCH
    batch = f"""
    <div class="card batch-card">
      <div class="batch-header">
        <span class="batch-id">{b['id']}</span>
        {audit_badge(b['status'])}
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
        <span class="stat-chip">🔧 {b['patched']} VMs patched</span>
        <span class="stat-chip">📋 {b['rotations']} log rotations</span>
        <span class="stat-chip">🐳 {b['prunes']} Docker prunes</span>
        <span class="stat-chip err">⚠ {b['errors']} errors</span>
      </div>
    </div>"""

    # VM grid
    cards = ""
    for vm in VMS:
        color, _, border = STATUS_COLORS.get(vm["status"], ("#94a3b8", "", "#94a3b8"))
        disk_color = "amber" if vm["disk"] >= 75 else ("red" if vm["disk"] >= 90 else "indigo")
        note_html = f'<div class="vm-note" style="color:{color}">{vm["note"]}</div>' if vm["note"] else ""
        cards += f"""
        <a href="/vm/{vm['hostname']}" class="card vm-card" style="border-left-color:{border}">
          <div class="vm-card-header">
            <span class="vm-hostname">{vm['hostname']}</span>
            <div class="vm-tags">{os_tag(vm['os'])}{env_tag(vm['env'])}</div>
          </div>
          {note_html}
          <div class="vm-disk-row">
            <div class="vm-disk-label"><span>Disk</span><span>{vm['disk']}%</span></div>
            <div class="prog-wrap">
              <div class="prog-fill prog-{disk_color}" style="width:{vm['disk']}%"></div>
            </div>
          </div>
          <div class="vm-footer">
            <span class="vm-ts">{vm['last_action']}</span>
            {badge(vm['status'])}
          </div>
        </a>"""

    return f"""
    {kpis}
    {batch}
    <div class="section-hdr">
      <div>
        <div class="section-title">VM Fleet</div>
        <div class="section-sub">{len(VMS)} hosts across {len(set(v['env'] for v in VMS))} environments</div>
      </div>
    </div>
    <div class="vm-grid">{cards}</div>"""


def page_approvals() -> str:
    cards = ""
    for a in APPROVALS:
        if a["action"] == "SERVICE RESTART":
            commands_html = "".join(
                f'<div class="comment">{c}</div>' if c.startswith("#") else f"<div>{c}</div>"
                for c in a["commands"]
            )
            extra = f'<div class="terminal">{commands_html}</div>'
        else:
            pills = "".join(f'<span class="pkg-pill">{p}</span>' for p in a.get("packages", []))
            extra = f'<div class="pkg-pills">{pills}</div>'

        tier_cls = "badge-danger" if a["tier"] == "HIGH RISK" else "badge-amber"
        cards += f"""
        <div class="card appr-card">
          <div class="appr-band" style="background: linear-gradient(135deg, {a['header_color']}, {a['header_color']}cc)">
            <span class="appr-band-title">{a['action']}</span>
            <span class="appr-band-host">{a['hostname']}</span>
            <span class="badge {tier_cls}" style="margin-left:8px">{a['tier']}</span>
          </div>
          <div class="appr-body">
            <div class="appr-meta-row">
              <span class="appr-hostname">{a['hostname']}</span>
              <span class="appr-osinfo">{a['os']} &nbsp;·&nbsp; {a['ip']} &nbsp;·&nbsp; {a['env']}</span>
              <span class="appr-countdown">⏱ {a['countdown']}</span>
            </div>
            <div class="appr-reasoning">{a['reasoning']}</div>
            {extra}
            <div class="appr-footer">
              <a href="#" class="btn-approve">APPROVE</a>
              <a href="#" class="btn-reject">REJECT</a>
              <a href="#" class="appr-details">View Full Details →</a>
            </div>
          </div>
        </div>"""

    resolved = """
    <div class="card resolved-card" style="margin-top:8px">
      <span class="resolved-label">RESOLVED TODAY — 14 actions</span>
      <span style="margin-left:auto; color:#94a3b8; font-size:1rem">›</span>
    </div>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Pending Approval</div>
        <div class="section-sub">2 actions require your decision before the agent can proceed</div>
      </div>
      <div class="filter-chips">
        <a href="#" class="chip active">All</a>
        <a href="#" class="chip">High Risk</a>
        <a href="#" class="chip">Service Restart</a>
        <a href="#" class="chip">OS Patching</a>
      </div>
    </div>
    {cards}
    {resolved}"""


def page_vm(hostname: str) -> str:
    vm = next((v for v in VMS if v["hostname"] == hostname), None)
    if vm is None:
        return f'<div class="card" style="padding:40px;text-align:center">VM <code>{hostname}</code> not found.</div>'

    color, _, border = STATUS_COLORS.get(vm["status"], ("#94a3b8", "", "#94a3b8"))

    actions = VM_ACTIONS.get(hostname, [
        {"ts": "2026-04-23 02:14", "action": "Log Rotation",   "status": "ok",  "duration": "9s",     "op": "agent", "detail": "Rotated 0.7 GB /var/log"},
        {"ts": "2026-04-23 02:10", "action": "OS Patching",    "status": "ok",  "duration": "3m 22s", "op": "agent", "detail": "6 packages updated"},
        {"ts": "2026-04-23 02:05", "action": "Pre-Validation", "status": "ok",  "duration": "3s",     "op": "agent", "detail": "SSH OK, OS verified"},
    ])

    rows = ""
    for i, a in enumerate(actions):
        alt = ' row-alt' if i % 2 == 1 else ''
        failed_cls = ' row-failed' if a["status"] == "failed" else ''
        rows += f"""<tr class="{alt}{failed_cls}">
          <td class="td-ts">{a['ts']} UTC</td>
          <td>{a['action']}</td>
          <td>{audit_badge(a['status'])}</td>
          <td class="td-mono">{a['duration']}</td>
          <td class="td-mono">{a['op']}</td>
          <td style="color:#475569;font-size:0.8125rem">{a['detail']}</td>
        </tr>"""

    disk_data = [
        ("/",     vm["disk"], f"{round(vm['disk']*50/100,1)} GB / 50 GB"),
        ("/var",  52,         "26.0 GB / 50 GB"),
        ("/tmp",  8,          "0.4 GB / 5 GB"),
        ("/home", 23,         "11.5 GB / 50 GB"),
    ]
    disk_rows = ""
    for path, pct, size in disk_data:
        pct_color = "#d97706" if pct >= 75 else "#4f46e5" if pct >= 30 else "#16a34a"
        disk_rows += f"""
        <div class="disk-partition">
          <div class="disk-row">
            <span class="disk-path">{path}</span>
            <div class="disk-progwrap"><div class="disk-fill" style="width:{pct}%;background:{pct_color}"></div></div>
            <span class="disk-pct" style="color:{pct_color}">{pct}%</span>
            <span class="disk-size">{size}</span>
          </div>
        </div>"""

    callout = ""
    if vm["disk"] >= 75:
        callout = '<div class="callout callout-amber">⚠ Root partition above 75% threshold. Disk cleanup recommended: /tmp eligible, apt cache ~1.2 GB available.</div>'

    return f"""
    <div class="detail-top">
      <div class="card identity-card identity-top-border" style="border-top-color:{border}">
        <div class="identity-header">
          <span class="identity-hostname">{vm['hostname']}</span>
          {badge(vm['status'])}
        </div>
        <div class="fields-grid">
          <div class="field-row"><span class="field-label">OS Version</span><span class="field-value">{vm['os']}</span></div>
          <div class="field-row"><span class="field-label">IP Address</span><span class="field-value">{vm['ip']}</span></div>
          <div class="field-row"><span class="field-label">Environment</span><span class="field-value">{vm['env']}</span></div>
          <div class="field-row"><span class="field-label">Uptime</span><span class="field-value">{vm['uptime']}</span></div>
          <div class="field-row"><span class="field-label">SSH Key</span><span class="field-value">/keys/{vm['hostname']}.pem</span></div>
          <div class="field-row"><span class="field-label">Last Seen</span><span class="field-value">{vm['last_action']} UTC</span></div>
        </div>
        <div class="divider"></div>
        <div class="maint-label">Maintenance Window</div>
        <div class="maint-val">Tue/Thu 02:00–04:00 UTC</div>
        <div class="maint-next">Next: 2026-04-24 02:00 UTC</div>
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
    <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:16px">
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Patches Applied (30d)</div>
        <div class="kpi-value" style="color:#16a34a">34</div>
        <div class="kpi-subtitle">packages updated</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Log Rotations (30d)</div>
        <div class="kpi-value" style="color:#4f46e5">8</div>
        <div class="kpi-subtitle">sessions</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#0891b2">
        <div class="kpi-label">Docker Prunes (30d)</div>
        <div class="kpi-value" style="color:#0891b2">3</div>
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
          <th>TIMESTAMP</th><th>ACTION</th><th>STATUS</th>
          <th>DURATION</th><th>OPERATOR</th><th>DETAIL</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""


def page_audit() -> str:
    rows = ""
    for i, e in enumerate(AUDIT_EVENTS):
        alt = ' row-alt' if i % 2 == 1 else ''
        status_cls = " row-failed" if e["status"] == "failed" else (" row-pending" if e["status"] == "pending" else "")
        rows += f"""<tr class="{alt}{status_cls}">
          <td class="td-ts">{e['ts']}</td>
          <td class="td-ts">{e['batch']}</td>
          <td><a href="/vm/{e['vm']}" class="td-host">{e['vm']}</a></td>
          <td>{e['action']}</td>
          <td>{audit_badge(e['status'])}</td>
          <td class="td-right">{e['duration']}</td>
          <td class="td-mono">{e['op']}</td>
          <td><a href="#" class="td-link">Details →</a></td>
        </tr>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Audit Log</div>
        <div class="section-sub">Complete record of all agent actions</div>
      </div>
      <a href="#" class="btn-outline btn-outline-indigo">EXPORT CSV</a>
    </div>
    <div class="card" style="padding:16px;margin-bottom:16px">
      <div class="filter-bar">
        <input class="search-input" type="text" placeholder="Search hostname, batch ID, action..." />
        <select class="select-input"><option>All VMs</option>{"".join(f"<option>{v['hostname']}</option>" for v in VMS)}</select>
        <select class="select-input"><option>All Actions</option><option>OS Patching</option><option>Log Rotation</option><option>Docker Prune</option><option>Disk Cleanup</option><option>Pre-Validation</option></select>
        <select class="select-input"><option>All Status</option><option>OK</option><option>Failed</option><option>Pending</option><option>Warning</option></select>
        <a href="#" class="btn-primary">APPLY</a>
      </div>
    </div>
    <div class="results-bar"><strong>247 events</strong> &nbsp;·&nbsp; filtered: last 7 days &nbsp;·&nbsp; all environments &nbsp;&nbsp;<a href="#" class="td-link" style="font-size:0.8125rem">Clear Filters</a></div>
    <div class="card table-card">
      <table class="data-table">
        <thead><tr>
          <th>TIMESTAMP</th><th>BATCH ID</th><th>VM</th><th>ACTION</th>
          <th>STATUS</th><th class="r">DURATION</th><th>OPERATOR</th><th></th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="pagination">
        <a href="#" class="pg-btn">← Prev</a>
        <span class="pg-current">Page 1 of 21</span>
        <a href="#" class="pg-btn">Next →</a>
      </div>
    </div>"""


def page_batches() -> str:
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

    # Simple SVG sparkline
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

    rows = ""
    for i, b in enumerate(BATCHES):
        alt = " row-alt" if i % 2 == 1 else ""
        failed_cls = " row-failed" if b["status"] == "failed" else ""
        env_cls = "env-prod" if b["env"] == "PROD" else "env-staging"
        err_style = 'style="color:#dc2626;font-weight:700"' if b["errors"] > 0 else ""
        rows += f"""<tr class="{alt}{failed_cls}">
          <td><span class="td-host" style="cursor:default">{b['id']}</span></td>
          <td class="td-ts">{b['started']} UTC</td>
          <td><span class="env-badge {env_cls}">{b['env']}</span></td>
          <td class="td-right">{b['vms']}</td>
          <td class="td-right">{b['actions']}</td>
          <td>{audit_badge(b['status'])}</td>
          <td class="td-right">{b['duration']}</td>
          <td class="td-right" {err_style}>{b['errors']}</td>
          <td><a href="#" class="td-link">Details →</a></td>
        </tr>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Batch History</div>
        <div class="section-sub">All maintenance runs — click any batch for full per-VM breakdown</div>
      </div>
      <a href="#" class="btn-primary">+ SCHEDULE BATCH</a>
    </div>
    {kpis}
    {chart}
    <div class="card table-card">
      <div style="padding:14px 20px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #f1f5f9">
        <span class="section-title" style="font-size:0.875rem">Batch Runs</span>
        <div class="filter-chips" style="margin-left:8px">
          <a href="#" class="chip active">All</a>
          <a href="#" class="chip">Completed</a>
          <a href="#" class="chip">Partial</a>
          <a href="#" class="chip">Failed</a>
        </div>
      </div>
      <table class="data-table">
        <thead><tr>
          <th>BATCH ID</th><th>STARTED</th><th>ENV</th>
          <th class="r">VMs</th><th class="r">ACTIONS</th>
          <th>STATUS</th><th class="r">DURATION</th><th class="r">ERRORS</th><th></th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="pagination">
        <a href="#" class="pg-btn">← Prev</a>
        <span class="pg-current">Page 1 of 4</span>
        <a href="#" class="pg-btn">Next →</a>
      </div>
    </div>"""


# ── Inventory page ───────────────────────────────────────────────────────────

def page_inventory() -> str:
    envs  = len(set(v["env"] for v in VMS))
    os_ct = len(set(v["os"].split()[0] for v in VMS))
    reachable = sum(1 for v in VMS if v["status"] != "offline")

    kpis = f"""
    <div class="inv-kpi">
      <div class="card kpi-tile kpi-top-border" style="border-color:#4f46e5">
        <div class="kpi-label">Total VMs</div>
        <div class="kpi-value" style="color:#0f172a">{len(VMS)}</div>
        <div class="kpi-subtitle">{envs} environments</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#0891b2">
        <div class="kpi-label">OS Types</div>
        <div class="kpi-value" style="color:#0891b2">{os_ct}</div>
        <div class="kpi-subtitle">Ubuntu · RHEL · Debian</div>
      </div>
      <div class="card kpi-tile kpi-top-border" style="border-color:#16a34a">
        <div class="kpi-label">Reachable</div>
        <div class="kpi-value" style="color:#16a34a">{reachable}</div>
        <div class="kpi-subtitle">last verified 02:14 UTC</div>
      </div>
    </div>"""

    filters = """
    <div class="card" style="padding:14px 16px;margin-bottom:16px">
      <div class="filter-bar">
        <input class="search-input" type="text" placeholder="Search hostname, IP, OS..."/>
        <select class="select-input">
          <option>All Environments</option>
          <option>PROD</option><option>STAGING</option><option>DEV</option>
        </select>
        <select class="select-input">
          <option>All OS</option>
          <option>Ubuntu 22.04</option><option>RHEL 8.7</option><option>Debian 11</option>
        </select>
        <select class="select-input">
          <option>All Status</option>
          <option>OK</option><option>Warning</option><option>Failed</option><option>Pending</option>
        </select>
        <a href="#" class="btn-primary">FILTER</a>
      </div>
    </div>"""

    rows = ""
    for i, vm in enumerate(VMS):
        alt  = " row-alt" if i % 2 == 1 else ""
        fcls = " row-failed" if vm["status"] == "failed" else (" row-pending" if vm["status"] == "pending" else "")
        rows += f"""<tr class="{alt}{fcls}">
          <td><a href="/vm/{vm['hostname']}" class="td-host">{vm['hostname']}</a></td>
          <td class="td-mono">{vm['ip']}</td>
          <td>{os_tag(vm['os'])}</td>
          <td>{env_tag(vm['env'])}</td>
          <td class="td-mono" style="color:#94a3b8;font-size:0.75rem">/keys/{vm['hostname']}.pem</td>
          <td class="td-mono" style="font-size:0.75rem">Tue/Thu 02:00–04:00</td>
          <td class="td-mono" style="font-size:0.75rem">{vm['uptime']}</td>
          <td>{badge(vm['status'])}</td>
          <td><a href="/vm/{vm['hostname']}" class="td-link">Details →</a></td>
        </tr>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">VM Inventory</div>
        <div class="section-sub">{len(VMS)} hosts registered across {envs} environments</div>
      </div>
      <div style="display:flex;gap:8px">
        <a href="#" class="btn-outline btn-outline-indigo">EXPORT</a>
        <a href="#" class="btn-primary">+ ADD VM</a>
      </div>
    </div>
    {kpis}
    {filters}
    <div class="card table-card">
      <table class="data-table">
        <thead><tr>
          <th>HOSTNAME</th><th>IP ADDRESS</th><th>OS</th><th>ENV</th>
          <th>SSH KEY</th><th>MAINT. WINDOW</th><th>UPTIME</th><th>STATUS</th><th></th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""


# ── Settings page ─────────────────────────────────────────────────────────────

def page_settings() -> str:
    cards = ""
    for s in _SETTINGS_SECTIONS:
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

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Settings</div>
        <div class="section-sub">Current agent configuration — read-only (set via environment variables)</div>
      </div>
      <a href="#" class="btn-outline btn-outline-indigo">VIEW DOCS</a>
    </div>
    <div class="settings-grid">{cards}</div>
    {note}"""


# ── Admin page ────────────────────────────────────────────────────────────────

def page_admin() -> str:
    # Agent controls card
    agent_card = """
    <div class="card admin-card">
      <div class="admin-section-title">Agent Controls</div>
      <div class="agent-row">
        <span class="agent-row-label">Scheduler</span>
        <span class="agent-row-val">
          <span class="sys-dot dot-green" style="display:inline-block;margin-right:6px"></span>RUNNING
        </span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Last batch</span>
        <span class="agent-row-val">prod-0423-0200 &nbsp;·&nbsp; 14m 32s</span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Next scheduled</span>
        <span class="agent-row-val">2026-05-14 02:00 UTC</span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Current mode</span>
        <span class="badge badge-indigo">DRY RUN</span>
      </div>
      <div class="agent-row">
        <span class="agent-row-label">Active batch</span>
        <span class="agent-row-val" style="color:#94a3b8">None</span>
      </div>
      <div class="admin-btns">
        <a href="#" class="btn-run">▶ RUN BATCH NOW</a>
        <a href="#" class="btn-warn-ol">⏸ PAUSE SCHEDULER</a>
      </div>
    </div>"""

    # System health card
    health_rows = ""
    for h in _HEALTH_CHECKS:
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
        <a href="#" class="btn-outline btn-outline-indigo" style="font-size:0.75rem;padding:5px 12px">RUN CHECK</a>
      </div>
      <div class="health-rows">{health_rows}</div>
      <div style="margin-top:14px;font-family:'JetBrains Mono',monospace;font-size:0.6875rem;color:#94a3b8">
        Last checked: 2026-05-13 03:00:12 UTC
      </div>
    </div>"""

    # Lock manager
    if _ACTIVE_LOCKS:
        lock_rows = "".join(
            f"""<tr>
              <td class="td-mono">{lk['vm']}</td>
              <td class="td-ts">{lk['since']}</td>
              <td class="td-mono" style="color:#94a3b8;font-size:0.75rem">{lk['path']}</td>
              <td><a href="#" class="btn-danger-ol" style="font-size:0.75rem;padding:4px 10px">FORCE CLEAR</a></td>
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
        <a href="#" class="btn-danger-ol" style="font-size:0.75rem;padding:5px 12px">CLEAR ALL LOCKS</a>
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

    # Danger zone
    danger_card = """
    <div class="card danger-zone-card">
      <div class="danger-zone-hdr">
        <span style="font-size:1.1rem">⚠</span>
        <span class="danger-zone-title">Danger Zone</span>
      </div>
      <div class="danger-zone-sub">
        These actions are destructive and may be irreversible.
        Confirm before executing in a production environment.
      </div>
      <div class="danger-actions">
        <a href="#" class="btn-danger" style="background:#d97706">FLUSH DEFERRED QUEUE</a>
        <a href="#" class="btn-danger" style="background:#dc2626">CLEAR ALL LOCKS</a>
        <a href="#" class="btn-danger" style="background:#7c3aed">FORCE ROLLBACK ALL VMs</a>
        <a href="#" class="btn-danger" style="background:#0f172a">TRUNCATE AUDIT LOG</a>
      </div>
    </div>"""

    return f"""
    <div class="section-hdr">
      <div>
        <div class="section-title">Admin</div>
        <div class="section-sub">Agent controls, system health, lock management, and operational overrides</div>
      </div>
      <span class="badge badge-amber" style="font-size:0.75rem;padding:5px 12px">⚠ DRY RUN MODE ACTIVE</span>
    </div>
    <div class="admin-top">
      {agent_card}
      {health_card}
    </div>
    {lock_card}
    {override_card}
    {danger_card}"""


# ── Glossary data ────────────────────────────────────────────────────────────

_GLOSS: list[tuple[str, str, str, str, str]] = [
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
    ("Approval Gate",      "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "High-risk actions pause here. The agent posts to Slack and polls for a ✅ or ❌ reaction before proceeding."),
    ("Rollback",           "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Automatic revert to pre-action state on failure. Strategy differs per action: full, re-pull, or no-op."),
    ("Risk Tier",          "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Action classification by impact: Low (auto), Medium (log+notify), High (approval), Critical (blocked)."),
    ("Maintenance Window", "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Configured time slots when the agent is permitted to run. The agent refuses to act outside these windows."),
    ("Audit Log",          "SAFETY",  "#7c3aed", "gloss-chip-safety",
     "Immutable before-and-after record of every agent action. Written to SQLite and never deleted."),
    ("OS Patching",        "ACTIONS", "#0891b2", "gloss-chip-action",
     "Non-kernel security and package updates via apt (Ubuntu/Debian) or dnf (RHEL). Kernel updates are blocked."),
    ("Docker Prune",       "ACTIONS", "#0891b2", "gloss-chip-action",
     "Removal of stopped containers, dangling images, unused networks, and volumes to reclaim disk space."),
    ("Log Rotation",       "ACTIONS", "#0891b2", "gloss-chip-action",
     "Compression and archival of old log files in /var/log via logrotate or journalctl vacuum."),
    ("Disk Cleanup",       "ACTIONS", "#0891b2", "gloss-chip-action",
     "Frees temp files from a strict whitelist: /tmp, apt/yum cache, old journals, orphaned deps only."),
    ("vLLM",               "INFRA",   "#d97706", "gloss-chip-infra",
     "Self-hosted LLM (Qwen3-8B-AWQ) on a private GPU VM. Used for planning with a hardcoded fallback if unreachable."),
    ("SSH",                "INFRA",   "#d97706", "gloss-chip-infra",
     "Key-based Secure Shell protocol used exclusively to connect to and execute commands on target VMs."),
    ("APScheduler",        "INFRA",   "#d97706", "gloss-chip-infra",
     "Python scheduling library that fires maintenance batches on a configured cron schedule inside the agent process."),
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
    checks: 'Cron expression evaluated · Maintenance window verified · No active batch running',
    onfail: 'Batch skipped silently — next scheduled run continues normally',
    code: 'errander/scheduling/scheduler.py · errander/scheduling/windows.py',
    note: 'The scheduler is the only automated entry point. Use --run-now to trigger a batch manually.'
  },
  'parent-graph': {
    title: 'Parent Graph', badge: 'ORCHESTRATOR', badgeColor: '#4f46e5',
    checks: 'Loads VM inventory · Acquires per-VM file locks · Fans out to parallel sub-graphs',
    onfail: 'Individual VM failures do not abort the batch — each VM runs independently',
    code: 'errander/agent/graph.py · errander/config/inventory.py · errander/safety/locking.py',
    note: 'LangGraph parent graph fans out to one per-VM sub-graph execution in parallel across the fleet.'
  },
  'pre-validation': {
    title: 'Pre-Validation', badge: 'RUNS ON EVERY VM', badgeColor: '#16a34a',
    checks: 'SSH reachable · OS detected · Maintenance window active · VM not locked',
    onfail: 'VM skipped for this batch — audit event written with reason',
    code: 'errander/safety/validators.py · errander/execution/os_detection.py',
    note: 'This is the first node run for every VM. No action is taken until all checks pass.'
  },
  'llm-planning': {
    title: 'LLM Planning', badge: 'AI DECISION', badgeColor: '#7c3aed',
    checks: 'Queries vLLM endpoint · Outputs ordered action plan as JSON · Classifies risk tier per action',
    onfail: 'Falls back to hardcoded default action priority — agent never blocks on LLM unavailability',
    code: 'errander/agent/decisions.py · errander/integrations/llm.py · errander/models/plans.py',
    note: 'All LLM responses are validated via Pydantic models. Invalid responses trigger the hardcoded fallback.'
  },
  'approval-gate': {
    title: 'Approval Gate', badge: 'HIGH RISK ONLY', badgeColor: '#d97706',
    checks: 'Posts plan to Slack · Polls for reaction every 30s · Timeout 30 min (auto-REJECT)',
    onfail: 'Action skipped on REJECTED or timeout — audit event written, VM continues to next action',
    code: 'errander/safety/approval.py · errander/integrations/slack.py',
    note: 'Only High-tier actions enter this node. Low and Medium actions bypass it entirely.'
  },
  'action-execution': {
    title: 'Action Execution', badge: 'RUNS MAINTENANCE', badgeColor: '#0891b2',
    checks: 'Dispatches to action sub-graph · dry_run flag respected · Idempotency enforced',
    onfail: 'Exception caught → Rollback node entered → Audit event written with error detail',
    code: 'errander/agent/vm_graph.py · errander/agent/subgraphs/ · errander/execution/commands.py',
    note: 'Sub-graphs: patching.py, log_rotation.py, docker_prune.py, disk_cleanup.py, backup_verify.py'
  },
  'rollback': {
    title: 'Rollback', badge: 'FAILURE PATH ONLY', badgeColor: '#ef4444',
    checks: 'Restores package snapshot (patching) · Re-pull images (Docker) · No-op for log/disk',
    onfail: 'Critical alert fired if rollback itself fails — requires manual intervention',
    code: 'errander/safety/rollback.py',
    note: 'Not all actions support full rollback. See Rollback Tiers in the spec for strategy per action type.'
  },
  'audit-logging': {
    title: 'Audit Logging', badge: 'ALWAYS RUNS', badgeColor: '#16a34a',
    checks: 'Writes before-event · Writes after-event · Records duration, operator, status, detail',
    onfail: 'If audit write fails — agent halts. Audit integrity takes priority over execution.',
    code: 'errander/safety/audit.py · errander/models/events.py',
    note: 'Every action produces two audit events: one before execution and one after. Never deleted.'
  },
  'report': {
    title: 'Report', badge: 'BATCH SUMMARY', badgeColor: '#4f46e5',
    checks: 'LLM generates human-readable summary · Falls back to template if LLM unavailable · Posted to Slack',
    onfail: 'Template fallback always succeeds — batch report is never skipped',
    code: 'errander/observability/reporting.py · errander/integrations/slack.py',
    note: 'Report includes: VMs processed, actions taken, errors, rollbacks triggered, and total time elapsed.'
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
        ("apscheduler",      400, 20,  "",                        "wf-dot-amber",  "APScheduler",   "cron trigger"),
        ("parent-graph",     400, 110, "",                        "wf-dot-indigo", "Parent Graph",  "fan-out · VMs"),
        ("pre-validation",   400, 200, "active",                  "wf-dot-white",  "Pre-Validation","SSH · OS · window"),
        ("llm-planning",     400, 290, "",                        "wf-dot-violet", "LLM Planning",  "decide action plan"),
        ("approval-gate",    120, 385, "wf-node-conditional",     "wf-dot-amber",  "Approval Gate", "high-risk · Slack"),
        ("action-execution", 650, 385, "",                        "wf-dot-teal",   "Action Exec.",  "patch·rotate·prune"),
        ("rollback",         755, 480, "wf-node-failure-node",    "wf-dot-red",    "Rollback",      "revert snapshot"),
        ("audit-logging",    400, 575, "",                        "wf-dot-green",  "Audit Logging", "before + after"),
        ("report",           400, 665, "",                        "wf-dot-indigo", "Report",        "LLM or template"),
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
    nodes_html += '<div class="wf-node-terminal" style="left:50px;top:480px">✕ SKIPPED</div>'

    # SVG arrow overlay — all coordinates are pixel-exact for 960×760 container
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

  <!-- Happy-path: animated flowing dashes, indigo glow -->
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

  <!-- LLM Planning → Action Execution (low/med, happy) -->
  <path d="M 515,340 C 585,365 670,378 730,385"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>

  <!-- LLM Planning → Approval Gate (high risk, amber dashed) -->
  <path d="M 445,340 C 375,362 265,375 200,385"
        stroke="#d97706" stroke-width="1.5" fill="none" stroke-dasharray="5 5"
        marker-end="url(#ma)"/>

  <!-- Approval Gate → Action Execution (APPROVED, green animated) -->
  <path d="M 280,410 C 430,410 500,410 650,410"
        stroke="#16a34a" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mg)"
        style="animation:dash-flow 0.9s linear infinite"/>

  <!-- Approval Gate → SKIPPED (REJECTED, red dashed) -->
  <path d="M 185,435 C 165,455 130,468 105,480"
        stroke="#ef4444" stroke-width="1.5" fill="none" stroke-dasharray="4 5"
        marker-end="url(#mr)"/>

  <!-- Action Execution → Rollback (FAILURE, red dashed) -->
  <path d="M 810,410 C 848,432 850,458 835,480"
        stroke="#ef4444" stroke-width="1.5" fill="none" stroke-dasharray="4 5"
        marker-end="url(#mr)"/>

  <!-- Action Execution → Audit Logging (SUCCESS, green animated) -->
  <path d="M 730,435 C 700,490 635,558 560,600"
        stroke="#16a34a" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mg)"
        style="animation:dash-flow 0.9s linear infinite"/>

  <!-- Rollback → Audit Logging (rejoins, amber dashed) -->
  <path d="M 800,530 C 762,558 660,583 560,600"
        stroke="#d97706" stroke-width="1.5" fill="none" stroke-dasharray="5 5"
        marker-end="url(#ma)"/>

  <!-- Audit Logging → Report (happy) -->
  <path d="M 480,625 L 480,665"
        stroke="#4f46e5" stroke-width="2" fill="none" stroke-dasharray="8 5"
        marker-end="url(#mh)"
        style="animation:dash-flow 0.8s linear infinite;filter:drop-shadow(0 0 3px #4f46e5)"/>

  <!-- Edge labels -->
  <text x="308" y="354" fill="#d97706" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">HIGH RISK</text>
  <text x="576" y="356" fill="#818cf8" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">LOW / MED</text>
  <text x="428" y="402" fill="#4ade80" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">APPROVED</text>
  <text x="116" y="455" fill="#f87171" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">REJECTED</text>
  <text x="820" y="448" fill="#f87171" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">FAILURE</text>
  <text x="650" y="508" fill="#4ade80" font-family="JetBrains Mono,monospace" font-size="9" font-weight="700">SUCCESS</text>
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

    return grid_section + workflow_section


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
                     f'<span class="last-batch">Last batch: 2026-04-23 02:00 UTC</span>'
                     f'<a href="#" class="btn-primary">RUN BATCH NOW</a>',
        content=page_fleet(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_approvals(request: web.Request) -> web.Response:
    html = layout(
        title="Approval Queue",
        active_url="/approvals",
        breadcrumb="Approval Queue",
        topnav_extra=f'{env_badge_top("PROD")}<span class="pending-chip">{APPROVAL_COUNT} PENDING</span>',
        content=page_approvals(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_vm(request: web.Request) -> web.Response:
    hostname = request.match_info["hostname"]
    vm = next((v for v in VMS if v["hostname"] == hostname), None)
    env = vm["env"] if vm else "PROD"
    html = layout(
        title=f"VM: {hostname}",
        active_url="/",
        breadcrumb=f'<a href="/" style="color:#475569;text-decoration:none">Fleet Dashboard</a>'
                   f'<span class="sep">/</span><span class="sub">{hostname}</span>',
        topnav_extra=f'{env_badge_top(env)}'
                     f'<a href="#" class="btn-outline btn-outline-amber">FORCE MAINTENANCE</a>'
                     f'<a href="#" class="btn-outline btn-outline-indigo">SSH TERMINAL</a>',
        content=page_vm(hostname),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_audit(request: web.Request) -> web.Response:
    html = layout(
        title="Audit Log",
        active_url="/audit",
        breadcrumb="Audit Log",
        topnav_extra=f'{env_badge_top("PROD")}<a href="#" class="btn-outline btn-outline-indigo">EXPORT CSV</a>',
        content=page_audit(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_batches(request: web.Request) -> web.Response:
    html = layout(
        title="Batch History",
        active_url="/batches",
        breadcrumb="Batch History",
        topnav_extra=f'{env_badge_top("PROD")}<a href="#" class="btn-primary">+ SCHEDULE BATCH</a>',
        content=page_batches(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_glossary(request: web.Request) -> web.Response:
    html = layout(
        title="Glossary & Workflow",
        active_url="/glossary",
        breadcrumb="Glossary &amp; Workflow",
        topnav_extra='<a href="#" class="btn-outline btn-outline-indigo">DOCS ↗</a>',
        content=page_glossary(),
    )
    return web.Response(text=html, content_type="text/html")


async def handle_inventory(request: web.Request) -> web.Response:
    html = layout(
        title="Inventory",
        active_url="/inventory",
        breadcrumb="Inventory",
        topnav_extra='<a href="#" class="btn-outline btn-outline-indigo">EXPORT CSV</a>',
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


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/",                handle_fleet)
    app.router.add_get("/approvals",       handle_approvals)
    app.router.add_get("/vm/{hostname}",   handle_vm)
    app.router.add_get("/audit",           handle_audit)
    app.router.add_get("/batches",         handle_batches)
    app.router.add_get("/inventory",       handle_inventory)
    app.router.add_get("/settings",        handle_settings)
    app.router.add_get("/admin",           handle_admin)
    app.router.add_get("/glossary",        handle_glossary)
    return app


def run() -> None:
    app = create_app()
    print("\n  errander-ai Operations Hub")
    print("  ---------------------------------")
    print(f"  http://localhost:{PORT}\n")
    web.run_app(app, host="127.0.0.1", port=PORT, print=lambda _: None)


if __name__ == "__main__":
    run()
