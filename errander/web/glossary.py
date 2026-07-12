"""Glossary & Agent Workflow page (production UI).

Extracted verbatim from the deleted legacy demo server (errander/web/server.py,
2026-07-10) — this page was the demo server's one production dependency, served
at /ui/glossary by errander/web/ui.py. Content: the glossary term cards
(owner-curated, 2026-06-22/23 sessions) and the interactive click-to-expand
Agent Workflow diagram (nodes + SVG arrows + detail modal JS).

Self-contained: no imports, no store access — pure HTML/CSS/JS string builders.
`GLOSS_CSS` is injected as an inline <style> by the ui.py handler.
"""

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
    ("Planning Note",      "CORE",    "#4f46e5", "gloss-chip-core",
     "Short LLM-generated note attached to an already-finalized deterministic plan. Informational only — never changes which actions run or their order. Shown in the Slack approval message and the web approval card."),
    ("Agent Proposal",     "CORE",    "#4f46e5", "gloss-chip-core",
     "A suggestion record, not an authorization. The daily probe's deterministic detector — and optionally a bounded read-only investigation loop — files proposals from flagged signals into the /ui/proposals queue, badged AGENT-ORIGINATED. A named operator approves, rejects, or snoozes; an approved actionable proposal (disk_cleanup / log_rotation only) is executed through the same deterministic sub-graph path as a normal batch — approval originates work, it never bypasses the safety gates. Reject the same VM+action twice and further proposals for that pair pause for 14 days."),
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

