"""Prometheus metrics and /metrics + /health HTTP endpoints.

All metrics are module-level singletons registered in a shared CollectorRegistry.
The HTTP server is a lightweight aiohttp app — no framework overhead.

Metrics exposed:
- errander_actions_total (counter): Actions executed, labeled by type/status/vm
- errander_action_duration_seconds (histogram): Action execution wall time
- errander_batch_duration_seconds (histogram): Full batch run time
- errander_ssh_errors_total (counter): SSH connection failures by vm/reason
- errander_llm_requests_total (counter): LLM calls labeled by outcome
- errander_approval_wait_seconds (histogram): Time waiting for human approval
- errander_vm_lock_held_seconds (histogram): How long VM locks are held
"""

from __future__ import annotations

import html as _html_mod
import logging
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote as _url_quote

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from errander.models.events import EventType

if TYPE_CHECKING:
    from errander.models.vm import VMTarget
    from errander.safety.approval import ApprovalManager
    from errander.safety.audit import AuditStore
    from errander.safety.overrides import OverridesStore

_esc = _html_mod.escape  # escape untrusted data before HTML interpolation


def _uq(s: str) -> str:
    return _url_quote(str(s), safe="")  # URL-encode path segments (no safe chars)

#: Typed app keys for storing shared objects on the aiohttp Application.
_AUDIT_STORE_KEY: web.AppKey[AuditStore | None] = web.AppKey("audit_store")
_APPROVAL_MANAGER_KEY: web.AppKey[ApprovalManager | None] = web.AppKey("approval_manager")
_OVERRIDES_STORE_KEY: web.AppKey[OverridesStore | None] = web.AppKey("overrides_store")
_BASE_INVENTORY_KEY: web.AppKey[list[VMTarget]] = web.AppKey("base_inventory")
_UI_USER_KEY: web.AppKey[str] = web.AppKey("ui_user")
_UI_PASSWORD_KEY: web.AppKey[str] = web.AppKey("ui_password")

#: CSRF secret key (generated fresh per server start — stateless double-submit pattern)
_CSRF_SECRET_KEY: web.AppKey[str] = web.AppKey("csrf_secret")

_CSRF_COOKIE = "errander_csrf"
_CSRF_HEADER = "X-CSRF-Token"
_CSRF_FIELD = "_csrf_token"

logger = logging.getLogger(__name__)

#: Shared registry — all metrics in one place, easy to pass to tests.
REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

ACTIONS_TOTAL = Counter(
    "errander_actions_total",
    "Total maintenance actions executed",
    ["action_type", "status", "vm_id"],
    registry=REGISTRY,
)

ACTION_DURATION = Histogram(
    "errander_action_duration_seconds",
    "Time spent executing a single maintenance action",
    ["action_type"],
    buckets=(5, 15, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

BATCH_DURATION = Histogram(
    "errander_batch_duration_seconds",
    "Time for a full batch maintenance run",
    buckets=(30, 60, 120, 300, 600, 1200, 1800),
    registry=REGISTRY,
)

SSH_ERRORS_TOTAL = Counter(
    "errander_ssh_errors_total",
    "SSH connection or command failures",
    ["vm_id", "reason"],
    registry=REGISTRY,
)

LLM_REQUESTS_TOTAL = Counter(
    "errander_llm_requests_total",
    "LLM completion calls",
    ["outcome"],  # "success" | "fallback" | "timeout" | "error"
    registry=REGISTRY,
)

APPROVAL_WAIT = Histogram(
    "errander_approval_wait_seconds",
    "Seconds waiting for human approval reaction",
    buckets=(30, 60, 120, 300, 600, 900, 1800),
    registry=REGISTRY,
)

VM_LOCK_HELD = Histogram(
    "errander_vm_lock_held_seconds",
    "Duration VM lock was held",
    ["vm_id"],
    buckets=(10, 30, 60, 120, 300, 600, 1200),
    registry=REGISTRY,
)

WAVE_HEALTH_CHECKS = Counter(
    "errander_wave_health_checks_total",
    "Wave health check outcomes",
    ["wave", "outcome"],
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Design system
# ---------------------------------------------------------------------------

_FONTS = (
    "https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:wght@300;400;500"
    "&display=swap"
)

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#08090d;--surface:#0d1017;--raised:#131825;--hover:#181f2e;
  --border:rgba(255,255,255,0.055);--border-hi:rgba(255,255,255,0.11);
  --amber:#e8970a;--amber-dim:rgba(232,151,10,0.13);--amber-glow:rgba(232,151,10,0.38);
  --green:#1fcc6e;--green-dim:rgba(31,204,110,0.12);
  --red:#f04060;--red-dim:rgba(240,64,96,0.12);
  --blue:#3ba8f5;--blue-dim:rgba(59,168,245,0.12);
  --t1:#e6ecf8;--t2:#6d7d96;--t3:#35414f;
  --mono:'IBM Plex Mono',monospace;
  --sans:'IBM Plex Sans',sans-serif;
  --head:'Syne',sans-serif;
}
html{font-size:14px}
body{
  font-family:var(--sans);background:var(--bg);color:var(--t1);
  min-height:100vh;display:flex;-webkit-font-smoothing:antialiased;
  line-height:1.55;
}
/* top accent line */
body::before{
  content:'';position:fixed;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--amber),transparent);
  z-index:999;
}

/* ── SIDEBAR ─────────────────────────────────────────────── */
.sb{
  width:210px;min-height:100vh;background:var(--surface);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;
  position:fixed;left:0;top:0;bottom:0;z-index:100;
}
.sb-head{
  padding:1.35rem 1.2rem 1.05rem;
  border-bottom:1px solid var(--border);
}
.sb-brand{
  font-family:var(--head);font-weight:800;font-size:1rem;
  letter-spacing:.13em;color:var(--t1);
  display:flex;align-items:center;gap:.55rem;
}
.sb-led{
  width:7px;height:7px;border-radius:50%;background:var(--amber);flex-shrink:0;
  animation:led 2.8s ease-in-out infinite;
}
@keyframes led{
  0%,100%{box-shadow:0 0 0 0 var(--amber-glow);opacity:1}
  50%{box-shadow:0 0 0 5px transparent;opacity:.65}
}
.sb-tag{
  font-family:var(--mono);font-size:.6rem;color:var(--t3);
  letter-spacing:.07em;margin-top:.28rem;
}
.sb-nav{padding:.55rem 0;flex:1}
.sb-a{
  display:flex;align-items:center;gap:.5rem;
  padding:.5rem 1.2rem;color:var(--t2);
  text-decoration:none;font-size:.82rem;
  transition:all .12s;position:relative;
}
.sb-a:hover{color:var(--t1);background:var(--raised)}
.sb-a.on{color:var(--amber);background:var(--amber-dim)}
.sb-a.on::before{
  content:'';position:absolute;left:0;top:0;bottom:0;
  width:2px;background:var(--amber);
}
.sb-ico{font-size:.72rem;opacity:.6;width:.9rem;text-align:center;flex-shrink:0}
.sb-ext{color:var(--t3)!important;font-size:.78rem!important}
.sb-ext:hover{color:var(--t2)!important}
.sb-divider{height:1px;background:var(--border);margin:.45rem 1.2rem}
.sb-badge{
  margin-left:auto;background:var(--red);color:#fff;
  font-family:var(--mono);font-size:.57rem;font-weight:500;
  padding:1px 5px;border-radius:3px;
}
.sb-foot{
  padding:.65rem 1.2rem;border-top:1px solid var(--border);
  font-family:var(--mono);font-size:.58rem;color:var(--t3);
}

/* ── MAIN AREA ───────────────────────────────────────────── */
.wrap{margin-left:210px;flex:1;display:flex;flex-direction:column;min-height:100vh}
.topbar{
  height:48px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;padding:0 1.75rem;gap:.65rem;
  background:var(--surface);position:sticky;top:0;z-index:50;
}
.tb-title{
  font-family:var(--head);font-size:.86rem;font-weight:700;
  letter-spacing:.04em;color:var(--t1);
}
.tb-sub{font-family:var(--mono);font-size:.7rem;color:var(--t2)}
.tb-sep{color:var(--t3)}
.tb-time{margin-left:auto;font-family:var(--mono);font-size:.64rem;color:var(--t3)}
.page{padding:1.65rem 1.75rem;flex:1}

/* ── STAT CARDS ──────────────────────────────────────────── */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:.8rem;margin-bottom:1.8rem}
.card{
  background:var(--surface);border:1px solid var(--border);
  border-radius:5px;padding:1rem 1.25rem;
  position:relative;overflow:hidden;
}
.card::after{
  content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:var(--border-hi);
}
.card.ca::after{background:var(--amber)}
.card.cg::after{background:var(--green)}
.card.cr::after{background:var(--red)}
.card.cb::after{background:var(--blue)}
.card-lbl{
  font-family:var(--mono);font-size:.58rem;color:var(--t3);
  letter-spacing:.12em;text-transform:uppercase;margin-bottom:.5rem;
}
.card-num{
  font-family:var(--head);font-size:1.8rem;font-weight:700;
  line-height:1;color:var(--t1);
}
.card-num.ca{color:var(--amber)}
.card-num.cg{color:var(--green)}
.card-num.cr{color:var(--red)}
.card-sub{font-size:.72rem;color:var(--t2);margin-top:.3rem}
.card-sub a{color:var(--t2);text-decoration:none}
.card-sub a:hover{color:var(--t1)}

/* ── LED STATUS ──────────────────────────────────────────── */
.led-row{display:inline-flex;align-items:center;gap:.38rem;font-size:.8rem}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dot-g{background:var(--green);box-shadow:0 0 7px rgba(31,204,110,.55);animation:dg 3s infinite}
.dot-a{background:var(--amber);box-shadow:0 0 7px var(--amber-glow);animation:led 2s infinite}
@keyframes dg{0%,100%{opacity:1}50%{opacity:.55}}

/* ── SECTION HEADERS ──────────────────────────────────────── */
.sec{display:flex;align-items:center;gap:.65rem;margin-bottom:.8rem;margin-top:1.6rem}
.sec:first-child{margin-top:0}
.sec-lbl{
  font-family:var(--mono);font-size:.6rem;color:var(--t3);
  letter-spacing:.13em;text-transform:uppercase;white-space:nowrap;
}
.sec-line{flex:1;height:1px;background:var(--border)}
.sec-ct{
  font-family:var(--mono);font-size:.6rem;color:var(--t3);
  background:var(--raised);padding:1px 7px;border-radius:3px;
  border:1px solid var(--border-hi);
}

/* ── TABLES ──────────────────────────────────────────────── */
.tbl{background:var(--surface);border:1px solid var(--border);border-radius:5px;overflow:hidden;margin-bottom:1.4rem}
table{width:100%;border-collapse:collapse;font-size:.79rem}
thead th{
  padding:.52rem 1.15rem;text-align:left;
  font-family:var(--mono);font-size:.6rem;font-weight:500;
  color:var(--t3);letter-spacing:.09em;text-transform:uppercase;
  background:var(--raised);border-bottom:1px solid var(--border);
  white-space:nowrap;
}
tbody tr{border-bottom:1px solid var(--border);transition:background .1s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--hover)}
tbody td{padding:.58rem 1.15rem;color:var(--t1);vertical-align:middle}
td.mono,th.mono{font-family:var(--mono);font-size:.74rem;color:var(--t2)}

/* ── BADGES ──────────────────────────────────────────────── */
.badge{
  display:inline-flex;align-items:center;
  font-family:var(--mono);font-size:.63rem;letter-spacing:.03em;
  padding:2px 7px;border-radius:3px;font-weight:500;white-space:nowrap;
}
.bk-ok {background:var(--green-dim);color:var(--green);border:1px solid rgba(31,204,110,.2)}
.bk-err{background:var(--red-dim);  color:var(--red);  border:1px solid rgba(240,64,96,.2)}
.bk-inf{background:var(--blue-dim); color:var(--blue); border:1px solid rgba(59,168,245,.2)}
.bk-neu{background:var(--raised);   color:var(--t2);   border:1px solid var(--border-hi)}

/* ── LINKS ───────────────────────────────────────────────── */
a{color:var(--blue);text-decoration:none;transition:color .12s}
a:hover{color:var(--t1)}
.id-a{font-family:var(--mono);font-size:.76rem;color:var(--amber)}
.id-a:hover{color:var(--t1)}
.back-a{
  display:inline-flex;align-items:center;gap:.3rem;
  font-family:var(--mono);font-size:.66rem;color:var(--t3);
  text-decoration:none;margin-bottom:1.3rem;transition:color .12s;
}
.back-a:hover{color:var(--t1)}

/* ── DETAIL PAGE ─────────────────────────────────────────── */
.det-hdr{margin-bottom:1.45rem}
.det-id{
  font-family:var(--mono);font-size:1.05rem;color:var(--amber);
  font-weight:500;letter-spacing:.04em;margin-bottom:.25rem;
}
.det-sub{font-size:.76rem;color:var(--t2)}

/* ── APPROVAL CARDS ──────────────────────────────────────── */
.apv{
  background:var(--surface);border:1px solid var(--border);
  border-left:3px solid var(--amber);border-radius:5px;
  padding:1.15rem 1.35rem;margin-bottom:.9rem;
}
.apv-id{font-family:var(--mono);font-size:.88rem;color:var(--amber);font-weight:500;margin-bottom:.28rem}
.apv-meta{font-family:var(--mono);font-size:.66rem;color:var(--t3);margin-bottom:.85rem}
.apv-report{
  background:var(--bg);border:1px solid var(--border);border-radius:4px;overflow:hidden;
  margin-bottom:.85rem;
}
.apv-report summary{
  padding:.42rem .8rem;cursor:pointer;
  font-family:var(--mono);font-size:.64rem;color:var(--t3);
  letter-spacing:.05em;list-style:none;
  display:flex;align-items:center;gap:.35rem;
  user-select:none;transition:color .12s;
}
.apv-report summary::marker,.apv-report summary::-webkit-details-marker{display:none}
.apv-report summary::before{content:'▶';font-size:.45rem;transition:transform .15s;opacity:.6}
.apv-report[open] summary::before{transform:rotate(90deg)}
.apv-report summary:hover{color:var(--t1)}
.apv-pre{
  padding:.65rem .85rem;
  font-family:var(--mono);font-size:.68rem;color:var(--t2);
  max-height:175px;overflow:auto;white-space:pre-wrap;
  line-height:1.65;border-top:1px solid var(--border);
}
.apv-btns{display:flex;gap:.6rem;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;gap:.32rem;
  padding:.44rem 1rem;border:1px solid transparent;
  border-radius:4px;cursor:pointer;
  font-family:var(--mono);font-size:.7rem;font-weight:500;
  letter-spacing:.07em;text-transform:uppercase;
  transition:all .15s;
}
.btn-ok{background:var(--green-dim);color:var(--green);border-color:rgba(31,204,110,.25)}
.btn-ok:hover{background:var(--green);color:#000;border-color:var(--green)}
.btn-no{background:var(--red-dim);color:var(--red);border-color:rgba(240,64,96,.25)}
.btn-no:hover{background:var(--red);color:#fff;border-color:var(--red)}

/* ── HISTORY DECISION BADGES ─────────────────────────────── */
.dec-ok{color:var(--green);font-family:var(--mono);font-size:.74rem;font-weight:500}
.dec-no{color:var(--red);font-family:var(--mono);font-size:.74rem;font-weight:500}

/* ── EMPTY / NOTE ────────────────────────────────────────── */
.empty{
  padding:2.5rem;text-align:center;
  font-family:var(--mono);font-size:.76rem;color:var(--t3);
}
.note{
  font-family:var(--mono);font-size:.63rem;color:var(--t3);
  margin-top:.65rem;
}
.note a{color:var(--t3)}
.note a:hover{color:var(--t2)}

/* ── NOT CONNECTED ───────────────────────────────────────── */
.nc{
  display:flex;align-items:center;justify-content:center;height:180px;
  font-family:var(--mono);font-size:.76rem;color:var(--t3);
  background:var(--surface);border:1px dashed var(--border-hi);border-radius:5px;
}

/* ── FORMS (settings / inventory) ───────────────────────────── */
.form-card{
  background:var(--surface);border:1px solid var(--border);border-radius:5px;
  padding:1.4rem 1.6rem;margin-bottom:1.2rem;max-width:640px;
}
.form-row{margin-bottom:1.1rem}
.form-lbl{
  display:block;font-family:var(--mono);font-size:.64rem;color:var(--t3);
  letter-spacing:.1em;text-transform:uppercase;margin-bottom:.38rem;
}
.form-src{
  display:inline-block;font-family:var(--mono);font-size:.6rem;
  padding:1px 6px;border-radius:3px;margin-left:.5rem;
  background:var(--raised);color:var(--t3);border:1px solid var(--border-hi);
}
.form-src.env{background:rgba(240,64,96,0.1);color:var(--red);border-color:rgba(240,64,96,.2)}
.form-src.db{background:var(--blue-dim);color:var(--blue);border-color:rgba(59,168,245,.2)}
.form-src.yaml{background:var(--green-dim);color:var(--green);border-color:rgba(31,204,110,.2)}
input[type=text],input[type=password],input[type=number],select{
  width:100%;padding:.46rem .7rem;
  background:var(--bg);border:1px solid var(--border-hi);border-radius:4px;
  color:var(--t1);font-family:var(--mono);font-size:.8rem;
  transition:border-color .12s;outline:none;
}
input[type=text]:focus,input[type=password]:focus,
input[type=number]:focus,select:focus{border-color:var(--amber)}
input:disabled,select:disabled{opacity:.45;cursor:not-allowed}
.btn-save{
  background:var(--amber-dim);color:var(--amber);border:1px solid rgba(232,151,10,.3);
  padding:.44rem 1.1rem;border-radius:4px;cursor:pointer;
  font-family:var(--mono);font-size:.72rem;font-weight:500;
  letter-spacing:.07em;text-transform:uppercase;transition:all .15s;
}
.btn-save:hover{background:var(--amber);color:#000}
.btn-del{
  background:var(--red-dim);color:var(--red);border:1px solid rgba(240,64,96,.25);
  padding:.36rem .75rem;border-radius:4px;cursor:pointer;
  font-family:var(--mono);font-size:.67rem;font-weight:500;transition:all .15s;
}
.btn-del:hover{background:var(--red);color:#fff}
.btn-sm{
  background:var(--raised);color:var(--t2);border:1px solid var(--border-hi);
  padding:.3rem .65rem;border-radius:4px;cursor:pointer;
  font-family:var(--mono);font-size:.65rem;transition:all .15s;
}
.btn-sm:hover{color:var(--t1);border-color:var(--t3)}
.flash{
  padding:.55rem 1rem;border-radius:4px;margin-bottom:1rem;
  font-family:var(--mono);font-size:.76rem;
}
.flash-ok{background:var(--green-dim);color:var(--green);border:1px solid rgba(31,204,110,.2)}
.flash-err{background:var(--red-dim);color:var(--red);border:1px solid rgba(240,64,96,.2)}
.inv-row{display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem;flex-wrap:wrap}
.inv-badge{
  font-family:var(--mono);font-size:.6rem;padding:2px 7px;border-radius:3px;
  background:var(--blue-dim);color:var(--blue);border:1px solid rgba(59,168,245,.2);
}
.inv-dis{opacity:.45;text-decoration:line-through}
"""

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_EVENT_BADGE: dict[str, str] = {
    EventType.ACTION_COMPLETED.value: "bk-ok",
    EventType.BATCH_COMPLETED.value:  "bk-ok",
    EventType.ACTION_FAILED.value:    "bk-err",
    EventType.BATCH_STARTED.value:    "bk-inf",
    EventType.ACTION_STARTED.value:   "bk-inf",
}


def _event_cell(event_type: str) -> str:
    cls = _EVENT_BADGE.get(event_type, "bk-neu")
    return f'<span class="badge {cls}">{event_type}</span>'


def _section(label: str, count: int | None = None) -> str:
    ct = f'<span class="sec-ct">{count}</span>' if count is not None else ""
    return (
        f'<div class="sec"><span class="sec-lbl">{label}</span>'
        f'<span class="sec-line"></span>{ct}</div>'
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<div class="tbl"><div class="empty">No records found.</div></div>'
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        f'<div class="tbl"><table>'
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{trs}</tbody>"
        f"</table></div>"
    )


def _page(
    title: str,
    body: str,
    *,
    refresh: int = 0,
    pending_count: int = 0,
    request: web.Request | None = None,
) -> web.Response:
    # Active nav state
    t = title.lower()
    nav: dict[str, str] = {}
    if t == "dashboard":
        nav["dashboard"] = " on"
    elif t.startswith("batch") or t.startswith("vm:"):
        nav["batches"] = " on"
    elif t == "approvals":
        nav["approvals"] = " on"
    elif t == "settings":
        nav["settings"] = " on"
    elif t == "inventory":
        nav["inventory"] = " on"

    refresh_tag = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    badge = (
        f'<span class="sb-badge">{pending_count}</span>' if pending_count > 0 else ""
    )
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  {refresh_tag}
  <title>Errander-AI \u2014 {_esc(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="{_FONTS}" rel="stylesheet">
  <style>{_CSS}</style>
</head>
<body>
<aside class="sb">
  <div class="sb-head">
    <div class="sb-brand"><span class="sb-led"></span>Errander-AI</div>
    <div class="sb-tag">supervised agentic AI SRE</div>
  </div>
  <nav class="sb-nav">
    <a href="/ui" class="sb-a{nav.get('dashboard','')}">
      <span class="sb-ico" aria-hidden="true">&#9632;</span>Dashboard
    </a>
    <a href="/ui/batches" class="sb-a{nav.get('batches','')}">
      <span class="sb-ico" aria-hidden="true">&#9776;</span>Batches
    </a>
    <a href="/ui/approvals" class="sb-a{nav.get('approvals','')}">
      <span class="sb-ico" aria-hidden="true">&#9670;</span>Approvals{badge}
    </a>
    <a href="/ui/settings" class="sb-a{nav.get('settings','')}">
      <span class="sb-ico" aria-hidden="true">&#9881;</span>Settings
    </a>
    <a href="/ui/inventory" class="sb-a{nav.get('inventory','')}">
      <span class="sb-ico" aria-hidden="true">&#9782;</span>Inventory
    </a>
    <div class="sb-divider"></div>
    <a href="/metrics" target="_blank" class="sb-a sb-ext">
      <span class="sb-ico" aria-hidden="true">&#8599;</span>Metrics
    </a>
    <a href="/health" target="_blank" class="sb-a sb-ext">
      <span class="sb-ico" aria-hidden="true">&#8599;</span>Health
    </a>
  </nav>
  <div class="sb-foot">v1.0.0 &nbsp;&middot;&nbsp; sqlite</div>
</aside>
<div class="wrap">
  <header class="topbar">
    <span class="tb-title">{_esc(title)}</span>
    <span class="tb-time">{ts}</span>
  </header>
  <main class="page">
    {body}
  </main>
</div>
</body>
</html>"""
    if request is not None:
        html, nonce = _inject_csrf(request, html)
        response = web.Response(text=html, content_type="text/html")
        _set_csrf_cookie(response, nonce)
        return response
    return web.Response(text=html, content_type="text/html")


# ---------------------------------------------------------------------------
# Basic Auth middleware
# ---------------------------------------------------------------------------

@web.middleware
async def _basic_auth_middleware(
    request: web.Request,
    handler: web.RequestHandler,
) -> web.StreamResponse:
    """Require HTTP Basic Auth on /ui/* when ERRANDER_UI_USER/PASSWORD are set."""
    ui_user: str = request.app.get(_UI_USER_KEY, "")
    ui_password: str = request.app.get(_UI_PASSWORD_KEY, "")

    if not ui_user or not ui_password:
        return await handler(request)

    if not request.path.startswith("/ui"):
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            provided_user, _, provided_pass = decoded.partition(":")
            user_ok = secrets.compare_digest(provided_user, ui_user)
            pass_ok = secrets.compare_digest(provided_pass, ui_password)
            if user_ok and pass_ok:
                return await handler(request)
        except Exception:
            pass

    return web.Response(
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="Errander-AI"'},
        text="Unauthorized",
    )


@web.middleware
async def _csrf_middleware(
    request: web.Request,
    handler: web.RequestHandler,
) -> web.StreamResponse:
    """Enforce CSRF double-submit cookie on all /ui/* POST requests (finding #14)."""
    if request.method == "POST" and request.path.startswith("/ui") and not await _csrf_verify(request):
        logger.warning("CSRF check failed for %s %s", request.method, request.path)
        raise web.HTTPForbidden(reason="CSRF token missing or invalid")
    return await handler(request)


# ---------------------------------------------------------------------------
# CSRF — double-submit cookie pattern (finding #14)
# ---------------------------------------------------------------------------

def _csrf_token(request: web.Request) -> str:
    """Return the per-session CSRF token, creating it if absent.

    Uses the double-submit cookie pattern: a signed HMAC token is set in a
    cookie (HttpOnly=False so JS can read it if needed, SameSite=Strict) and
    must also appear in the POST body or X-CSRF-Token header.
    """
    import hashlib
    import hmac
    import os

    secret = request.app.get(_CSRF_SECRET_KEY, "")
    # Per-session nonce stored in the cookie
    cookie_val = request.cookies.get(_CSRF_COOKIE, "")
    if not cookie_val:
        cookie_val = os.urandom(16).hex()
    # HMAC of nonce with server secret — the value the client submits
    token = hmac.new(
        secret.encode(),
        cookie_val.encode(),
        hashlib.sha256,
    ).hexdigest()
    return token


def _csrf_cookie_value(request: web.Request) -> str:
    """Return the raw nonce stored in the CSRF cookie."""
    return request.cookies.get(_CSRF_COOKIE, "")


async def _csrf_verify(request: web.Request) -> bool:
    """Verify the CSRF token on a POST request.

    Checks both the X-CSRF-Token header and the _csrf_token form field.
    Returns True if valid, False if missing or wrong.
    """
    import hashlib
    import hmac

    secret = request.app.get(_CSRF_SECRET_KEY, "")
    cookie_val = request.cookies.get(_CSRF_COOKIE, "")
    if not cookie_val or not secret:
        return False

    expected = hmac.new(
        secret.encode(),
        cookie_val.encode(),
        hashlib.sha256,
    ).hexdigest()

    # Check header first (for AJAX), then form field
    submitted = request.headers.get(_CSRF_HEADER, "")
    if not submitted:
        try:
            data = await request.post()
            submitted = str(data.get(_CSRF_FIELD, ""))
        except Exception:
            return False

    return secrets.compare_digest(submitted, expected)


def _set_csrf_cookie(response: web.Response, nonce: str) -> None:
    """Attach the CSRF nonce cookie to a response (SameSite=Strict)."""
    response.set_cookie(
        _CSRF_COOKIE,
        nonce,
        httponly=False,   # client JS may need to read it
        samesite="Strict",
        secure=False,     # TLS handled by reverse proxy
    )


def _inject_csrf(request: web.Request, html: str) -> tuple[str, str]:
    """Return (modified_html, nonce) with hidden CSRF fields injected into all <form> tags.

    The nonce is returned so it can be set as a cookie on the response.
    """
    import hashlib
    import hmac
    import os

    secret = request.app.get(_CSRF_SECRET_KEY, "")
    nonce = request.cookies.get(_CSRF_COOKIE, "") or os.urandom(16).hex()
    token = hmac.new(
        secret.encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    hidden = f'<input type="hidden" name="{_CSRF_FIELD}" value="{token}">'
    html = _re_inject_csrf(html, hidden)
    return html, nonce


def _re_inject_csrf(html: str, hidden_field: str) -> str:
    """Insert a hidden CSRF field after the opening tag of every HTML form."""
    import re as _re
    return _re.sub(r"(<form\b[^>]*>)", r"\1" + hidden_field, html)


# ---------------------------------------------------------------------------
# UI — Settings page handlers
# ---------------------------------------------------------------------------

_LLM_SETTINGS_FIELDS = [
    ("llm.base_url", "ERRANDER_LLM_BASE_URL", "LLM Base URL", "text", False),
    ("llm.model", "ERRANDER_LLM_MODEL", "Model ID", "text", False),
    ("llm.api_key", "ERRANDER_LLM_API_KEY", "API Key", "password", True),
    ("llm.temperature", "ERRANDER_LLM_TEMPERATURE", "Temperature (0.0–2.0)", "number", False),
    ("llm.timeout_seconds", "ERRANDER_LLM_TIMEOUT", "Timeout (seconds)", "number", False),
]

_LLM_MODEL_DATALIST = [
    "Qwen/Qwen3-8B-AWQ", "gpt-4o-mini", "gpt-4o",
    "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    "llama-3.3-70b-versatile", "llama3.2",
]


async def _ui_settings_get(request: web.Request) -> web.Response:
    """GET /ui/settings — render LLM settings form."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    pending_count = len(manager.get_pending()) if manager is not None else 0

    db_rows: dict[str, dict[str, object]] = {}
    if store is not None:
        for row in await store.get_settings_overrides_raw():
            db_rows[str(row["key"])] = row

    flash = request.rel_url.query.get("flash", "")
    flash_err = request.rel_url.query.get("err", "")
    flash_html = ""
    if flash:
        flash_html = f'<div class="flash flash-ok">{_esc(flash)}</div>'
    elif flash_err:
        flash_html = f'<div class="flash flash-err">{_esc(flash_err)}</div>'

    import os as _os
    datalist = "".join(f'<option value="{m}">' for m in _LLM_MODEL_DATALIST)
    datalist_tag = f'<datalist id="model-presets">{datalist}</datalist>'

    rows_html = ""
    reset_forms_html = ""  # collected outside the main form to avoid nested-form invalid HTML
    for field_key, env_key, label, input_type, is_secret in _LLM_SETTINGS_FIELDS:
        env_set = _os.environ.get(env_key) is not None
        db_row = db_rows.get(env_key)
        has_db = db_row is not None

        if env_set:
            source_label = '<span class="form-src env">from env (locked)</span>'
            disabled = "disabled"
            display_val = "••••••••" if is_secret else (_os.environ.get(env_key, ""))
        elif has_db:
            source_label = '<span class="form-src db">overridden in UI</span>'
            disabled = ""
            display_val = "••••••••" if (is_secret and db_row.get("is_secret")) else str(db_row.get("value", ""))
        else:
            source_label = '<span class="form-src yaml">from YAML/default</span>'
            disabled = ""
            display_val = ""

        extra_attrs = 'list="model-presets"' if field_key == "llm.model" else ""
        if input_type == "number":
            extra_attrs += ' step="0.1" min="0"' if "temperature" in field_key else ' step="1" min="1"'

        # Use HTML5 form="..." attribute to associate the reset button with an out-of-band
        # <form> element. Nesting a <form> inside the main settings <form> is invalid HTML5
        # and causes browsers to implicitly close the outer form, breaking Save.
        reset_btn = ""
        if has_db and not env_set:
            form_id = f"reset-{env_key}"
            reset_btn = (
                f'<button type="submit" form="{form_id}"'
                f' class="btn-del" style="margin-left:.5rem">Reset</button>'
            )
            reset_forms_html += (
                f'<form id="{form_id}" method="POST" action="/ui/settings/reset">'
                f'<input type="hidden" name="key" value="{env_key}">'
                f'</form>'
            )

        rows_html += (
            f'<div class="form-row">'
            f'<label class="form-lbl">{label}{source_label}</label>'
            f'<input type="{input_type}" name="{env_key}" value="{_esc(display_val)}" {disabled} {extra_attrs}>'
            f'{reset_btn}'
            f'</div>'
        )

    body = (
        flash_html
        + datalist_tag
        + _section("LLM Provider")
        + '<div class="form-card">'
        + '<form method="POST" action="/ui/settings">'
        + rows_html
        + '<div style="margin-top:1.2rem">'
        + '<button type="submit" class="btn-save">Save Changes</button>'
        + '</div>'
        + '</form>'
        + '</div>'
        + reset_forms_html  # reset forms rendered outside main form
        + '<p class="note">Env-var-locked fields cannot be overridden via UI. '
        + 'Reset removes the DB override and reverts to YAML/default.</p>'
        + '<p class="note" style="color:#b45309">&#9888; LLM settings changes take effect after agent restart. '
        + 'The running agent was built from the settings active at startup.</p>'
    )
    return _page("Settings", body, pending_count=pending_count, request=request)


async def _ui_settings_post(request: web.Request) -> web.Response:
    """POST /ui/settings — save LLM settings overrides."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    audit: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)

    if store is None:
        raise web.HTTPFound("/ui/settings?err=Overrides+store+not+connected")

    data = await request.post()
    import os as _os

    errors: list[str] = []
    for field_key, env_key, label, _, is_secret in _LLM_SETTINGS_FIELDS:
        if _os.environ.get(env_key) is not None:
            continue  # env-locked, skip
        value = str(data.get(env_key, "")).strip()
        if not value:
            continue

        if field_key == "llm.temperature":
            try:
                t = float(value)
                if not 0.0 <= t <= 2.0:
                    errors.append(f"{label} must be between 0.0 and 2.0")
                    continue
            except ValueError:
                errors.append(f"{label} must be a number")
                continue

        await store.set_setting_override(
            key=env_key,
            value=value,
            is_secret=is_secret,
            updated_by="ui",
        )

        if audit is not None:
            from errander.models.events import AuditEvent, EventType
            await audit.log_event(AuditEvent(
                event_type=EventType.SETTINGS_CHANGED,
                batch_id="ui",
                detail=f"{env_key}: {'<secret>' if is_secret else value}",
            ))

    if errors:
        raise web.HTTPFound(f"/ui/settings?err={'%20'.join(errors)}")
    raise web.HTTPFound("/ui/settings?flash=Settings+saved")


async def _ui_settings_reset(request: web.Request) -> web.Response:
    """POST /ui/settings/reset — delete a single DB override."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    if store is None:
        raise web.HTTPFound("/ui/settings?err=Overrides+store+not+connected")

    data = await request.post()
    key = str(data.get("key", "")).strip()
    if key:
        await store.delete_setting_override(key)
    raise web.HTTPFound("/ui/settings?flash=Override+cleared")


async def _ui_settings_test_llm(request: web.Request) -> web.Response:
    """POST /ui/settings/test-llm — verify LLM connectivity with provided params.

    Accepts POST so secrets never appear in URLs, browser history, or access logs.
    """
    import os as _os

    from errander.integrations.llm import LLMClient

    data = await request.post()
    base_url = str(data.get("base_url", "") or _os.environ.get("ERRANDER_LLM_BASE_URL", ""))
    model = str(data.get("model", "") or _os.environ.get("ERRANDER_LLM_MODEL", ""))
    api_key = str(data.get("api_key", "") or _os.environ.get("ERRANDER_LLM_API_KEY", "not-needed"))

    try:
        temperature = float(str(data.get("temperature", "0.1")) or "0.1")
    except ValueError:
        temperature = 0.1

    if not base_url or not model:
        return web.json_response({"ok": False, "error": "base_url and model are required"})

    client = LLMClient(base_url=base_url, model=model, api_key=api_key, temperature=temperature)
    result = await client.check_endpoint()
    return web.json_response({"ok": result["reachable"], **result})


# ---------------------------------------------------------------------------
# UI — Inventory page handlers
# ---------------------------------------------------------------------------

_VALID_OS_FAMILIES = {"ubuntu", "debian", "rhel"}  # must match OSFamily enum in models/vm.py


async def _ui_inventory_get(request: web.Request) -> web.Response:
    """GET /ui/inventory — show full YAML fleet merged with DB overrides."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    base_inventory: list[VMTarget] = request.app.get(_BASE_INVENTORY_KEY) or []
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    pending_count = len(manager.get_pending()) if manager is not None else 0

    flash = request.rel_url.query.get("flash", "")
    flash_err = request.rel_url.query.get("err", "")
    flash_html = ""
    if flash:
        flash_html = f'<div class="flash flash-ok">{_esc(flash)}</div>'
    elif flash_err:
        flash_html = f'<div class="flash flash-err">{_esc(flash_err)}</div>'

    if store is None:
        return _page(
            "Inventory",
            flash_html + '<div class="nc">Overrides store not connected.</div>',
            pending_count=pending_count,
        )

    # Build a lookup: (env_name, vm_name) → override row
    all_overrides = await store.get_all_inventory_overrides()
    override_map: dict[tuple[str, str], dict[str, object]] = {}
    adhoc_rows: list[dict[str, object]] = []
    for row in all_overrides:
        key = (str(row["env_name"]), str(row["vm_name"]))
        if str(row["source"]) == "db_addition":
            adhoc_rows.append(row)
        else:
            override_map[key] = row

    # Group YAML VMs by environment (vm_id = "env/name")
    by_env: dict[str, list[dict[str, object]]] = {}
    for vm in base_inventory:
        parts = vm.vm_id.split("/", 1)
        env_name, vm_name = (parts[0], parts[1]) if len(parts) == 2 else ("default", vm.vm_id)
        override = override_map.get((env_name, vm_name))
        disabled = bool(override["disabled"]) if override else False
        by_env.setdefault(env_name, []).append({
            "vm_name": vm_name,
            "host": vm.hostname,
            "os_family": vm.os_family.value,
            "disabled": disabled,
            "source": "yaml",
            "is_adhoc": False,
        })

    # Append ad-hoc VMs per environment
    for row in adhoc_rows:
        env_name = str(row["env_name"])
        by_env.setdefault(env_name, []).append({
            "vm_name": str(row["vm_name"]),
            "host": str(row["host"] or ""),
            "os_family": str(row["os_family"] or ""),
            "disabled": bool(row["disabled"]),
            "source": "db_addition",
            "is_adhoc": True,
        })

    env_sections = ""
    os_options = "".join(f'<option value="{o}">{o}</option>' for o in sorted(_VALID_OS_FAMILIES))

    for env_name, vms in sorted(by_env.items()):
        items_html = ""
        for vm in vms:
            vm_name = _esc(str(vm["vm_name"]))
            host = _esc(str(vm["host"]))
            os_fam = _esc(str(vm["os_family"]))
            disabled = bool(vm["disabled"])
            is_adhoc = bool(vm["is_adhoc"])

            name_cls = "inv-dis" if disabled else ""
            source_badge = (
                '<span class="inv-badge">+ ad-hoc</span>'
                if is_adhoc else
                '<span class="inv-badge inv-badge-yaml">YAML</span>'
            )
            disable_label = "Enable" if disabled else "Disable"
            env_name_esc = _esc(env_name)

            toggle_form = (
                f'<form method="POST" action="/ui/inventory/toggle" style="display:inline">'
                f'<input type="hidden" name="env_name" value="{env_name_esc}">'
                f'<input type="hidden" name="vm_name" value="{vm_name}">'
                f'<input type="hidden" name="disabled" value="{"0" if disabled else "1"}">'
                f'<button type="submit" class="btn-sm">{disable_label}</button>'
                f'</form>'
            )
            delete_form = ""
            if is_adhoc:
                delete_form = (
                    f'<form method="POST" '
                    f'action="/ui/inventory/delete/{_uq(env_name)}/{_uq(str(vm["vm_name"]))}"'
                    f' style="display:inline">'
                    f'<button type="submit" class="btn-del">Delete</button>'
                    f'</form>'
                )

            items_html += (
                f'<div class="inv-row">'
                f'<span class="mono {name_cls}">{vm_name}</span>'
                f'{source_badge}'
                f'<span style="color:var(--t3);font-size:.74rem">{host}</span>'
                f'<span class="badge bk-neu">{os_fam}</span>'
                f'{toggle_form}{delete_form}'
                f'</div>'
            )

        add_form = (
            f'<details style="margin-top:1rem">'
            f'<summary style="cursor:pointer;font-family:var(--mono);'
            f'font-size:.7rem;color:var(--t3)">+ Add VM</summary>'
            f'<div class="form-card" style="margin-top:.6rem">'
            f'<form method="POST" action="/ui/inventory/add">'
            f'<input type="hidden" name="env_name" value="{_esc(env_name)}">'
            f'<div class="form-row"><label class="form-lbl">Name</label>'
            f'<input type="text" name="vm_name" required placeholder="my-vm-01"></div>'
            f'<div class="form-row"><label class="form-lbl">Host (IP or DNS)</label>'
            f'<input type="text" name="host" required placeholder="10.0.1.20"></div>'
            f'<div class="form-row"><label class="form-lbl">SSH User</label>'
            f'<input type="text" name="ssh_user" placeholder="errander"></div>'
            f'<div class="form-row"><label class="form-lbl">SSH Key Path</label>'
            f'<input type="text" name="ssh_key_path" placeholder="~/.ssh/errander"></div>'
            f'<div class="form-row"><label class="form-lbl">OS Family</label>'
            f'<select name="os_family"><option value="">-- select --</option>{os_options}</select></div>'
            f'<div class="form-row"><label class="form-lbl">Note</label>'
            f'<input type="text" name="note" placeholder="Optional note"></div>'
            f'<button type="submit" class="btn-save">Add VM</button>'
            f'</form></div></details>'
        )

        env_sections += (
            _section(f"Environment: {env_name}", len(vms))
            + '<div class="form-card">'
            + items_html
            + add_form
            + '</div>'
        )

    if not env_sections:
        env_sections = (
            '<div class="nc">No VMs in inventory. '
            'Add an ad-hoc entry below or configure inventory.yaml.</div>'
        )

    body = flash_html + env_sections
    return _page("Inventory", body, pending_count=pending_count, request=request)


async def _ui_inventory_toggle(request: web.Request) -> web.Response:
    """POST /ui/inventory/toggle — enable/disable a VM."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    audit: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    if store is None:
        raise web.HTTPFound("/ui/inventory?err=Overrides+store+not+connected")

    data = await request.post()
    env_name = str(data.get("env_name", "")).strip()
    vm_name = str(data.get("vm_name", "")).strip()
    disabled = str(data.get("disabled", "0")) == "1"

    if not env_name or not vm_name:
        raise web.HTTPFound("/ui/inventory?err=Missing+env_name+or+vm_name")

    await store.upsert_inventory_override(
        env_name=env_name,
        vm_name=vm_name,
        source="yaml_override",
        disabled=disabled,
        updated_by="ui",
    )

    if audit is not None:
        from errander.models.events import AuditEvent, EventType
        await audit.log_event(AuditEvent(
            event_type=EventType.INVENTORY_CHANGED,
            batch_id="ui",
            vm_id=f"{env_name}/{vm_name}",
            detail=f"{'disabled' if disabled else 'enabled'} via UI",
        ))

    action = "disabled" if disabled else "enabled"
    raise web.HTTPFound(f"/ui/inventory?flash={vm_name}+{action}")


async def _ui_inventory_add(request: web.Request) -> web.Response:
    """POST /ui/inventory/add — add an ad-hoc VM."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    audit: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    if store is None:
        raise web.HTTPFound("/ui/inventory?err=Overrides+store+not+connected")

    data = await request.post()
    env_name = str(data.get("env_name", "")).strip()
    vm_name = str(data.get("vm_name", "")).strip()
    host = str(data.get("host", "")).strip()
    ssh_user = str(data.get("ssh_user", "")).strip() or None
    ssh_key_path = str(data.get("ssh_key_path", "")).strip() or None
    os_family = str(data.get("os_family", "")).strip()
    note = str(data.get("note", "")).strip()

    errors: list[str] = []
    if not vm_name or " " in vm_name:
        errors.append("VM name is required and must not contain spaces")
    if not host:
        errors.append("Host is required")
    if os_family and os_family not in _VALID_OS_FAMILIES:
        errors.append(f"OS family must be one of {sorted(_VALID_OS_FAMILIES)}")

    if errors:
        raise web.HTTPFound(f"/ui/inventory?err={'+'.join(errors)[:200]}")

    await store.upsert_inventory_override(
        env_name=env_name,
        vm_name=vm_name,
        source="db_addition",
        disabled=False,
        host=host,
        ssh_user=ssh_user,
        ssh_key_path=ssh_key_path,
        os_family=os_family or None,
        updated_by="ui",
        note=note,
    )

    if audit is not None:
        from errander.models.events import AuditEvent, EventType
        await audit.log_event(AuditEvent(
            event_type=EventType.INVENTORY_CHANGED,
            batch_id="ui",
            vm_id=f"{env_name}/{vm_name}",
            detail=f"added ad-hoc VM {host}",
        ))

    raise web.HTTPFound(f"/ui/inventory?flash={vm_name}+added")


async def _ui_inventory_delete(request: web.Request) -> web.Response:
    """POST /ui/inventory/delete/{env_name}/{vm_name} — delete ad-hoc VM."""
    store: OverridesStore | None = request.app.get(_OVERRIDES_STORE_KEY)
    audit: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    if store is None:
        raise web.HTTPFound("/ui/inventory?err=Overrides+store+not+connected")

    env_name = request.match_info["env_name"]
    vm_name = request.match_info["vm_name"]

    await store.delete_inventory_override(env_name, vm_name)

    if audit is not None:
        from errander.models.events import AuditEvent, EventType
        await audit.log_event(AuditEvent(
            event_type=EventType.INVENTORY_CHANGED,
            batch_id="ui",
            vm_id=f"{env_name}/{vm_name}",
            detail="deleted ad-hoc VM via UI",
        ))

    raise web.HTTPFound(f"/ui/inventory?flash={vm_name}+deleted")


# ---------------------------------------------------------------------------
# UI — route handlers
# ---------------------------------------------------------------------------

async def _ui_dashboard(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)

    if store is None:
        return _page("Dashboard", '<div class="nc">Audit store not connected.</div>', refresh=30)

    batches = await store.get_recent_batches(limit=10)
    total = await store.count_events()
    pending_count = len(manager.get_pending()) if manager is not None else 0
    batch_count = len(batches)

    # ── Stat cards ──────────────────────────────────────────────────────────
    apv_class = "cr" if pending_count > 0 else "cb"
    apv_num_class = "cr" if pending_count > 0 else ""
    cards = (
        f'<div class="cards">'
        f'<div class="card ca"><div class="card-lbl">Total Events</div>'
        f'<h3 class="card-num ca">{total}</h3></div>'
        f'<div class="card cb"><div class="card-lbl">Recent Batches</div>'
        f'<div class="card-num">{batch_count}</div></div>'
        f'<div class="card cg"><div class="card-lbl">Agent Status</div>'
        f'<div class="card-num cg">'
        f'<span class="led-row"><span class="dot dot-g"></span>Running</span>'
        f'</div></div>'
        f'<div class="card {apv_class}"><div class="card-lbl">Pending approvals</div>'
        f'<div class="card-num {apv_num_class}">{pending_count}</div>'
        f'<div class="card-sub"><a href="/ui/approvals">Review &rarr;</a></div></div>'
        f'</div>'
    )

    # ── Recent batches table ─────────────────────────────────────────────────
    rows = [
        [
            f'<a class="id-a" href="/ui/batches/{_uq(str(b["batch_id"]))}">{_esc(str(b["batch_id"]))}</a>',
            f'<span class="mono">{str(b["started_at"])[:19]}</span>',
            str(b["event_count"]),
            ", ".join(
                f'<a href="/ui/vms/{_uq(v)}">{_esc(v)}</a>'
                for v in (b["vm_ids"] or [])  # type: ignore[attr-defined]
            ) or '<span style="color:var(--t3)">—</span>',
        ]
        for b in batches
    ]
    table = _table(["Batch ID", "Started (UTC)", "Events", "VMs"], rows)

    body = (
        cards
        + _section("Recent Batches", batch_count)
        + table
        + '<p class="note">Auto-refreshes every 30s &nbsp;&middot;&nbsp;'
        '<a href="/ui/batches">See all Batches &rarr;</a></p>'
    )
    return _page("Dashboard", body, refresh=30, pending_count=pending_count)


async def _ui_batches(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    pending_count = len(manager.get_pending()) if manager is not None else 0

    if store is None:
        return _page("Batches", '<div class="nc">Audit store not connected.</div>')

    batches = await store.get_recent_batches(limit=100)
    rows = [
        [
            f'<a class="id-a" href="/ui/batches/{_uq(str(b["batch_id"]))}">{_esc(str(b["batch_id"]))}</a>',
            f'<span class="mono">{str(b["started_at"])[:19]}</span>',
            str(b["event_count"]),
            ", ".join(
                f'<a href="/ui/vms/{_uq(v)}">{_esc(v)}</a>'
                for v in (b["vm_ids"] or [])  # type: ignore[attr-defined]
            ) or '<span style="color:var(--t3)">—</span>',
        ]
        for b in batches
    ]
    body = _section("All Batches", len(batches)) + _table(["Batch ID", "Started (UTC)", "Events", "VMs"], rows)
    return _page("Batches", body, pending_count=pending_count)


async def _ui_batch_detail(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    batch_id = request.match_info["batch_id"]
    pending_count = len(manager.get_pending()) if manager is not None else 0

    if store is None:
        return _page(f"Batch: {batch_id}", '<div class="nc">Audit store not connected.</div>')

    events = await store.get_events(batch_id=batch_id, limit=500)
    back = '<a class="back-a" href="/ui/batches">\u2190 All batches</a>'

    if not events:
        return _page(
            f"Batch: {batch_id}",
            back + '<div class="tbl"><div class="empty">No events found.</div></div>',
            pending_count=pending_count,
        )

    rows = [
        [
            f'<span class="mono">{e.timestamp.strftime("%Y-%m-%d %H:%M:%S")}</span>',
            _event_cell(e.event_type.value),
            f'<a class="id-a" href="/ui/vms/{_uq(e.vm_id)}">{_esc(e.vm_id)}</a>' if e.vm_id else "",
            f'<span class="mono">{_esc(e.action_type)}</span>' if e.action_type else "",
            _esc(e.detail or ""),
        ]
        for e in events
    ]
    body = (
        back
        + f'<div class="det-hdr">'
        f'<div class="det-id">{_esc(batch_id)}</div>'
        f'<div class="det-sub">{len(events)} event(s)</div>'
        f'</div>'
        + _section("Event Log", len(events))
        + _table(["Timestamp (UTC)", "Event", "VM", "Action", "Detail"], rows)
    )
    return _page(f"Batch: {batch_id}", body, pending_count=pending_count)


async def _ui_vm(request: web.Request) -> web.Response:
    store: AuditStore | None = request.app.get(_AUDIT_STORE_KEY)
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    vm_id = request.match_info["vm_id"]
    pending_count = len(manager.get_pending()) if manager is not None else 0

    if store is None:
        return _page(f"VM: {vm_id}", '<div class="nc">Audit store not connected.</div>')

    events = await store.get_events(vm_id=vm_id, limit=500)
    back = '<a class="back-a" href="/ui">\u2190 Dashboard</a>'

    if not events:
        return _page(
            f"VM: {vm_id}",
            back + '<div class="tbl"><div class="empty">No events found.</div></div>',
            pending_count=pending_count,
        )

    rows = [
        [
            f'<span class="mono">{e.timestamp.strftime("%Y-%m-%d %H:%M:%S")}</span>',
            _event_cell(e.event_type.value),
            f'<a class="id-a" href="/ui/batches/{_uq(e.batch_id)}">{_esc(e.batch_id)}</a>',
            f'<span class="mono">{_esc(e.action_type)}</span>' if e.action_type else "",
            _esc(e.detail or ""),
        ]
        for e in events
    ]
    body = (
        back
        + f'<div class="det-hdr">'
        f'<div class="det-id">{_esc(vm_id)}</div>'
        f'<div class="det-sub">{len(events)} event(s)</div>'
        f'</div>'
        + _section("Action History", len(events))
        + _table(["Timestamp (UTC)", "Event", "Batch", "Action", "Detail"], rows)
    )
    return _page(f"VM: {vm_id}", body, pending_count=pending_count)


# ---------------------------------------------------------------------------
# Approval UI — list pending + decide
# ---------------------------------------------------------------------------

async def _ui_approvals(request: web.Request) -> web.Response:
    """GET /ui/approvals — list pending approvals and recent decisions."""
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    if manager is None:
        return _page("Approvals", '<div class="nc">Approval manager not connected.</div>')

    pending = manager.get_pending()
    history = manager.get_history()
    pending_count = len(pending)

    # ── Pending ──────────────────────────────────────────────────────────────
    if pending:
        cards: list[str] = []
        for p in pending:
            elapsed_min = int(
                (datetime.now(tz=UTC) - p.posted_at).total_seconds()
            ) // 60
            channel = "also posted to Slack" if p.slack_message_ts else "UI only"
            rpt = (
                p.report[:700]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            cards.append(
                f'<div class="apv">'
                f'<div class="apv-id">{_esc(p.batch_id)}</div>'
                f'<div class="apv-meta">{elapsed_min}m ago &nbsp;&bull;&nbsp; {channel}</div>'
                f'<details class="apv-report">'
                f'<summary>View dry-run report</summary>'
                f'<pre class="apv-pre">{rpt}</pre>'
                f'</details>'
                f'<div class="apv-btns">'
                f'<form method="POST" action="/ui/approvals/{_uq(p.batch_id)}/approve">'
                f'<button type="submit" class="btn btn-ok">&#10003; Approve</button>'
                f'</form>'
                f'<form method="POST" action="/ui/approvals/{_uq(p.batch_id)}/reject">'
                f'<button type="submit" class="btn btn-no">&#10007; Reject</button>'
                f'</form>'
                f'</div>'
                f'</div>'
            )
        pending_section = (
            _section("Pending", pending_count) + "".join(cards)
        )
    else:
        pending_section = (
            _section("Pending")
            + '<div class="tbl"><div class="empty">'
            "No pending approvals. They appear here when a dry-run batch completes."
            "</div></div>"
        )

    # ── History ───────────────────────────────────────────────────────────────
    if history:
        rows = [
            [
                f'<a class="id-a" href="/ui/batches/{_uq(h.batch_id)}">{_esc(h.batch_id)}</a>',
                f'<span class="mono">{h.posted_at.strftime("%Y-%m-%d %H:%M:%S")}</span>',
                (
                    '<span class="dec-ok">&#10003; Approved</span>'
                    if h.approved
                    else '<span class="dec-no">&#10007; Rejected</span>'
                ),
                f'<span class="mono">{h.decided_by or "timeout"}</span>',
            ]
            for h in history
        ]
        history_section = (
            _section("Recent Decisions", len(history))
            + _table(["Batch ID", "Requested (UTC)", "Decision", "Decided by"], rows)
        )
    else:
        history_section = (
            _section("Recent Decisions")
            + '<div class="tbl"><div class="empty">No decisions recorded yet.</div></div>'
        )

    return _page(
        "Approvals",
        pending_section + history_section,
        refresh=15,
        pending_count=pending_count,
        request=request,
    )


async def _ui_approval_decide(request: web.Request) -> web.Response:
    """POST /ui/approvals/{batch_id}/{action} — approve or reject via web form."""
    manager: ApprovalManager | None = request.app.get(_APPROVAL_MANAGER_KEY)
    if manager is None:
        return web.Response(status=503, text="Approval manager not connected")

    batch_id = request.match_info["batch_id"]
    action = request.match_info["action"]  # "approve" | "reject"
    approved = action == "approve"

    manager.decide(batch_id, approved=approved, user_id="ui")
    logger.info(
        "UI %s for batch %s", "approved" if approved else "rejected", batch_id,
    )
    raise web.HTTPFound("/ui/approvals")


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def _metrics_handler(request: web.Request) -> web.Response:
    """Serve Prometheus /metrics in text exposition format."""
    output = generate_latest(REGISTRY)
    return web.Response(
        body=output,
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


async def _health_handler(request: web.Request) -> web.Response:
    """Serve /health liveness check.

    Returns 200 OK with a JSON body. No dependency checks — if the
    process is alive enough to serve HTTP, it's alive.
    """
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def start_metrics_server(
    port: int = 9090,
    audit_store: AuditStore | None = None,
    approval_manager: ApprovalManager | None = None,
    overrides_store: OverridesStore | None = None,
    base_inventory: list[VMTarget] | None = None,
    ui_user: str = "",
    ui_password: str = "",
    bind_address: str = "127.0.0.1",
) -> web.AppRunner:
    """Start the Prometheus metrics, health, and web UI HTTP server.

    Serves:
    - GET  /metrics                              — Prometheus text format
    - GET  /health                               — {"status": "ok"}
    - GET  /ui                                   — Dashboard
    - GET  /ui/batches                           — All recent batches
    - GET  /ui/batches/{id}                      — Events for one batch
    - GET  /ui/vms/{vm_id}                       — History for one VM (vm_id may contain /)
    - GET  /ui/approvals                         — Pending + decided approvals
    - POST /ui/approvals/{batch_id}/approve      — Approve via UI button
    - POST /ui/approvals/{batch_id}/reject       — Reject via UI button
    - GET  /ui/settings                          — LLM settings form
    - POST /ui/settings                          — Save settings overrides
    - POST /ui/settings/reset                    — Clear a single override
    - POST /ui/settings/test-llm                 — Test LLM connectivity (POST keeps secrets out of URLs)
    - GET  /ui/inventory                         — Inventory management
    - POST /ui/inventory/toggle                  — Enable/disable a VM
    - POST /ui/inventory/add                     — Add an ad-hoc VM
    - POST /ui/inventory/delete/{env}/{vm}       — Delete an ad-hoc VM

    Args:
        port: Port to listen on (default 9090).
        audit_store: Connected AuditStore for UI queries.
        approval_manager: ApprovalManager for dual-channel approval UI.
        overrides_store: OverridesStore for settings/inventory overrides.
        base_inventory: Flat list of VMTarget from inventory.yaml — shown as the base fleet on /ui/inventory.
        ui_user: HTTP Basic Auth username for /ui/* (empty = auth disabled).
        ui_password: HTTP Basic Auth password for /ui/*.

    Returns:
        Running AppRunner — call runner.cleanup() on shutdown.
    """
    # Finding #14: auth is mandatory when binding to a non-loopback address.
    _is_loopback = bind_address in ("127.0.0.1", "::1", "localhost")
    if not _is_loopback and (not ui_user or not ui_password):
        msg = (
            f"UI is configured to bind on {bind_address} (non-loopback) "
            f"but ERRANDER_UI_USER / ERRANDER_UI_PASSWORD are not set. "
            f"Set credentials or restrict bind to 127.0.0.1."
        )
        raise RuntimeError(msg)

    if ui_user and ui_password:
        logger.info("UI auth enabled for /ui/* routes (bind=%s)", bind_address)
    else:
        logger.warning(
            "UI auth disabled — set ERRANDER_UI_USER and ERRANDER_UI_PASSWORD to enable"
        )

    import os as _os
    csrf_secret = _os.urandom(32).hex()  # fresh per server start

    app = web.Application(middlewares=[_basic_auth_middleware, _csrf_middleware])
    app[_AUDIT_STORE_KEY] = audit_store
    app[_APPROVAL_MANAGER_KEY] = approval_manager
    app[_OVERRIDES_STORE_KEY] = overrides_store
    app[_BASE_INVENTORY_KEY] = base_inventory or []
    app[_UI_USER_KEY] = ui_user
    app[_UI_PASSWORD_KEY] = ui_password
    app[_CSRF_SECRET_KEY] = csrf_secret

    app.router.add_get("/metrics", _metrics_handler)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/ui", _ui_dashboard)
    app.router.add_get("/ui/batches", _ui_batches)
    app.router.add_get("/ui/batches/{batch_id}", _ui_batch_detail)
    app.router.add_get(r"/ui/vms/{vm_id:.+}", _ui_vm)
    app.router.add_get("/ui/approvals", _ui_approvals)
    app.router.add_post(
        r"/ui/approvals/{batch_id:[^/]+}/{action:(approve|reject)}",
        _ui_approval_decide,
    )
    app.router.add_get("/ui/settings", _ui_settings_get)
    app.router.add_post("/ui/settings", _ui_settings_post)
    app.router.add_post("/ui/settings/reset", _ui_settings_reset)
    app.router.add_post("/ui/settings/test-llm", _ui_settings_test_llm)
    app.router.add_get("/ui/inventory", _ui_inventory_get)
    app.router.add_post("/ui/inventory/toggle", _ui_inventory_toggle)
    app.router.add_post("/ui/inventory/add", _ui_inventory_add)
    app.router.add_post(r"/ui/inventory/delete/{env_name:[^/]+}/{vm_name:[^/]+}", _ui_inventory_delete)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=bind_address, port=port)
    await site.start()
    logger.info(
        "Server listening on %s:%d (/metrics, /health, /ui)",
        bind_address, port,
    )
    return runner
