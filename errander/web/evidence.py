"""Enterprise evidence enrichment for the development UI.

Additive overlay — does not modify the demo dicts in ``data.py``. Page
renderers merge an evidence row into the base record by id/hostname. Lets
us surface plan_hash, batch_id, approver, approval_source, before/after
state, lock holders, idempotency state, etc. without rewriting data.py.

The shapes here mirror what the v1 audit DB / immutable execution artifact
actually stores (see ``errander/safety/audit.py``, ``models/plans.py``,
``models/events.py``). When the UI is wired to real data, this module is
the integration seam — replace the dicts with DB queries and the page
renderers don't change.
"""
from __future__ import annotations

from typing import Any

# ── UI mode (every page banner reads this) ──────────────────────────────────

# These are demo defaults. In the real wiring this comes from settings.py /
# the running agent's CLI flags. The mode banner is intentionally loud so
# nobody mistakes the demo for live, or dry-run for live execution.
UI_MODE: dict[str, str] = {
    "data_source":  "DEMO",         # DEMO | LIVE (DB)
    "env":          "PROD",         # PROD | STAGING | DEV
    "execution":    "DRY RUN",      # DRY RUN | LIVE EXECUTION
    "freshness":    "static fixture · not auto-refreshing",
    "backend":      "errander.web.data (dummy)",
    "build":        "v1.0.0",
}

# ── Approval evidence (keyed by approval id) ────────────────────────────────

APPROVAL_EVIDENCE: dict[str, dict[str, Any]] = {
    "appr-001": {
        "plan_id":          "plan-7f3b9a2c",
        "plan_hash":        "sha256:9c4f2a18b6e7d3c5a90b14fe72a8d6c4e1f3b29a7c0d5e8f1b6a4c2d9e7f0a13",
        "batch_id":         "prod-emerg-0423-0207",
        "action_id":        "act-9e1f4b7d",
        "requester":        "errander-agent (Layer B / scheduler escalation)",
        "approver_role":    "sre-oncall",
        "deadline_iso":     "2026-04-23T02:36:47Z",
        "artifact_age_h":   "0h 12m",
        "artifact_expiry_h":"23h 48m (window: 24h)",
        "drift_check":      "no drift since plan time (sha matches probe at 02:08 UTC)",
        "rollback_ready":   "yes — pre-restart pg_dump snapshot at 2026-04-23 01:58 UTC + WAL archive intact",
        "idempotency":      "service restart is not idempotent — guarded by 60s post-check + auto-rollback on pg_isready fail",
        "slack_thread_url": "https://errander.slack.com/archives/C0ERR/p1714016707000123",
        "audit_url":        "/audit?batch=prod-emerg-0423-0207&action=act-9e1f4b7d",
        "vm_lock":          "held by errander-agent since 02:09:01 UTC, expires 02:39:01",
        "window_state":     "EMERGENCY override (--force, reason: 'pg OOM imminent')",
        # Probe facts (Layer B deterministic)
        "probe_facts": [
            "postgresql.service mem%=94 for 47 min (probe at 02:08 UTC)",
            "journal: 47 errors in 24h (top: 'invalid checkpoint record')",
            "no failed services besides postgresql.service",
            "uptime 91d 6h — no scheduled restart in 30 days",
        ],
        # Policy decision (Layer B deterministic — rule-based)
        "policy_decision": [
            "Action class: service_restart → Risk Tier HIGH",
            "Allowlist: postgresql.service ∈ /etc/errander/restart-allowlist on prod-db-01 ✓",
            "Maintenance window: outside window — requires --force (operator-triggered) ✓",
            "Approval required: HIGH tier → Slack approval mandatory (CLAUDE.md domain rule)",
        ],
        # AI explanation (Layer A advisory only — clearly labeled)
        "ai_explanation": (
            "Operator-assistant summary (LLM, advisory): probe signals match the kernel-OOM "
            "pattern observed in incident INC-2024-0411 — pre-emptive controlled restart "
            "limits blast radius vs. waiting for the OOM killer. The AI is NOT the approval "
            "authority; only the operator's ✅ in Slack moves this forward."
        ),
        # Action evidence (per-action; service restart has no package list, but has a unit list)
        "units": [{"unit": "postgresql.service", "current": "active (running)", "target": "active (running, restarted)"}],
    },
    "appr-002": {
        "plan_id":          "plan-2d8c5e1a",
        "plan_hash":        "sha256:4a8e1f9d7b3c2e6a05f8b1c4d2e9a7b6c3f0d8e1a7b4c2d9e6f3a8b1c5d0e72",
        "batch_id":         "staging-0423-0200",
        "action_id":        "act-1c7f2a4e",
        "requester":        "errander-agent (Layer B / scheduled batch)",
        "approver_role":    "sre-oncall",
        "deadline_iso":     "2026-04-23T02:18:12Z",
        "artifact_age_h":   "0h 02m",
        "artifact_expiry_h":"23h 58m (window: 24h)",
        "drift_check":      "matches enriched plan — no repo metadata change since assess",
        "rollback_ready":   "yes — full pinned package manifest snapshot saved (RHEL 8.7) + per-pkg revert list",
        "idempotency":      "patching is idempotent at the approved version — replay safe",
        "slack_thread_url": "https://errander.slack.com/archives/C0ERR/p1714016287000456",
        "audit_url":        "/audit?batch=staging-0423-0200&action=act-1c7f2a4e",
        "vm_lock":          "held by errander-agent since 02:00:08 UTC, expires 02:30:08",
        "window_state":     "inside maintenance window (Tue/Thu 02:00 UTC)",
        "probe_facts": [
            "14 security packages pending (RHEL 8.7 dnf check-update)",
            "2 CVE matches: CVE-2024-1234 (openssl-3.0.7), CVE-2024-5678 (glibc-2.34)",
            "no held packages, no kernel pkgs in pending set",
            "yum/dnf locks clear, /var/cache/dnf=412 MB",
        ],
        "policy_decision": [
            "Action class: patching (non-kernel) → Risk Tier MEDIUM",
            "Approval required: STAGING + MEDIUM tier → policy 'moderate' requires Slack ✓",
            "Kernel filter: 0 kernel packages in pending — none would be applied ✓",
            "Approved-version pinning: install_pinned() will be used in live mode",
        ],
        "ai_explanation": (
            "Operator-assistant summary (LLM, advisory): 2 critical CVEs, both with public PoCs. "
            "Patch ordering follows the dependency graph emitted by dnf — glibc lands before "
            "anything that links libc.so.6. Layer A advice only; approval gate is the operator."
        ),
        "packages": [
            {"name": "openssl",         "current": "3.0.5-r2.el8",  "target": "3.0.7-r1.el8",   "cve": "CVE-2024-1234"},
            {"name": "glibc",           "current": "2.31-r9.el8",   "target": "2.34-r3.el8",    "cve": "CVE-2024-5678"},
            {"name": "curl",            "current": "7.61.1-r33.el8","target": "7.76.1-r28.el8", "cve": ""},
            {"name": "systemd",         "current": "239-r74.el8",   "target": "249-r2.el8",     "cve": ""},
            {"name": "python3",         "current": "3.6.8-r51.el8", "target": "3.9.16-r1.el8",  "cve": ""},
            {"name": "libssl3",         "current": "3.0.5-r2.el8",  "target": "3.0.7-r1.el8",   "cve": ""},
            {"name": "vim-enhanced",    "current": "8.0-r3.el8",    "target": "9.0-r1.el8",     "cve": ""},
            {"name": "bind-utils",      "current": "9.11.36-r3",    "target": "9.16.23-r5",     "cve": ""},
            {"name": "krb5-libs",       "current": "1.18.2-r22",    "target": "1.19.1-r10",     "cve": ""},
            {"name": "openssh-server",  "current": "8.0p1-r17",     "target": "8.7p1-r34",      "cve": ""},
            {"name": "nss",             "current": "3.79.0-r10",    "target": "3.90.0-r3",      "cve": ""},
            {"name": "dbus",            "current": "1.12.20-r1",    "target": "1.12.24-r4",     "cve": ""},
            {"name": "tzdata",          "current": "2023c-r1",      "target": "2024a-r1",       "cve": ""},
            {"name": "expat",           "current": "2.2.5-r11",     "target": "2.5.0-r3",       "cve": ""},
        ],
    },
}

# ── Audit evidence (positional overlay matching data.AUDIT_EVENTS order) ────

AUDIT_EVIDENCE: list[dict[str, Any]] = [
    {  # idx 0 — log rotation prod-api-01
        "event_id":        "evt-7a92f1c4",
        "action_id":       "act-5e1b8d3f",
        "plan_hash":       "sha256:abc1…ef72",
        "approver":        "(none — Low risk, auto)",
        "approval_source": "policy auto-approve (Low tier)",
        "before":          "/var/log: 4.8 GB",
        "after":           "/var/log: 3.6 GB (rotated 1.2 GB, 12 files)",
        "command":         "logrotate -s /var/lib/logrotate/status /etc/logrotate.conf",
        "stdout_summary":  "rotated nginx access/error, syslog, auth.log; postrotate kill -USR1 OK",
        "stderr_summary":  "",
        "rollback_status": "not applicable (log rotation, no rollback needed)",
    },
    {  # idx 1 — OS patching prod-web-02
        "event_id":        "evt-3c1e9d8a",
        "action_id":       "act-7b2f1c5e",
        "plan_hash":       "sha256:7d4a…b91c",
        "approver":        "ui:sre-oncall (admin · web UI ✓ at 02:08:14 UTC)",
        "approval_source": "slack:#errander-approvals · msg p1714016287000231",
        "before":          "11 pkgs pending, kernel-pinned",
        "after":           "11 pkgs at approved versions (openssl 3.0.5→3.0.7, …)",
        "command":         "apt-get install -y --no-install-recommends openssl=3.0.7-r1 curl=7.88-r1 libssl3=3.0.7-r1 systemd=249-r2 python3=3.9.16-r1 dbus=1.12.24-r4 + 5 more",
        "stdout_summary":  "11 upgraded, 0 newly installed, 0 to remove, 0 not upgraded",
        "stderr_summary":  "",
        "rollback_status": "not needed (verify_node: installed == approved_target for all 11)",
    },
    {  # idx 2 — pre-validation prod-db-01 (pending)
        "event_id":        "evt-9f2d4b8e",
        "action_id":       "act-3a8c1e7d",
        "plan_hash":       "sha256:9c4f…0a13",
        "approver":        "(pending — Slack approval not yet returned)",
        "approval_source": "web UI /ui/approvals · awaiting decision (Slack notified) · deadline 02:36 UTC",
        "before":          "n/a",
        "after":           "n/a (no execution yet)",
        "command":         "(blocked at approval gate)",
        "stdout_summary":  "",
        "stderr_summary":  "",
        "rollback_status": "n/a",
    },
    {  # idx 3 — patching prod-api-01
        "event_id":        "evt-1d7e4c9b",
        "action_id":       "act-2f8b3d1a",
        "plan_hash":       "sha256:5b3c…d91f",
        "approver":        "ui:sre-oncall (admin · web UI ✓ at 02:04:32 UTC)",
        "approval_source": "slack:#errander-approvals · msg p1714016272000189",
        "before":          "8 pkgs pending (glibc 2.31, bind-utils 9.11, nss 3.79, openssh-server 8.0, tzdata 2023c +3)",
        "after":           "8 pkgs at approved versions",
        "command":         "dnf install -y glibc-2.34-r3.el8 bind-utils-9.16.23-r5 nss-3.90.0-r3 openssh-server-8.7p1-r34 tzdata-2024a-r1 + 3 more",
        "stdout_summary":  "8 upgraded, complete!",
        "stderr_summary":  "",
        "rollback_status": "not needed",
    },
    {  # idx 4 — FAILED patching staging-api-01
        "event_id":        "evt-6a4f1c3d",
        "action_id":       "act-8e2d9b7f",
        "plan_hash":       "sha256:4a8e…0e72",
        "approver":        "ui:sre-oncall (admin · web UI ✓ at 02:03:11 UTC)",
        "approval_source": "slack:#errander-approvals · msg p1714016191000412",
        "before":          "14 pkgs at pre-patch versions (snapshot saved)",
        "after":           "14 pkgs reverted to pre-patch versions (rollback complete)",
        "command":         "dnf install -y glibc-2.34-r3.el8 libssl3-3.0.7-r1.el8 + 12 more  ⤵  rollback: dnf downgrade --allowerasing <pinned manifest>",
        "stdout_summary":  "Error: Transaction test error: package glibc-2.34 requires libssl3 = 3.0.7 but installed libssl3-1.1 conflicts.",
        "stderr_summary":  "RHEL 8.7 repo metadata mismatch — libssl3 1.1 cannot be removed (kept by openssh-server-8.0)",
        "rollback_status": "completed — full pinned-version revert via stored package manifest (14/14 OK)",
    },
    {  # idx 5 — docker hygiene prod-api-02
        "event_id":        "evt-2b9c5e3f",
        "action_id":       "act-4d1a8c6e",
        "plan_hash":       "sha256:0e8d…3a47",
        "approver":        "(none — Medium tier, policy 'relaxed' auto-approve)",
        "approval_source": "policy auto-approve (Medium · relaxed policy, log+notify)",
        "before":          "docker: 12 dangling images, 3 unused volumes, 5 stopped containers (8.3 GB)",
        "after":           "docker: 0 dangling, 0 unused volumes, 0 stopped (8.3 GB freed)",
        "command":         "docker image prune -f --filter 'until=168h' && docker volume prune -f && docker container prune -f --filter 'until=168h'",
        "stdout_summary":  "Total reclaimed space: 8.3GB",
        "stderr_summary":  "",
        "rollback_status": "not possible (re-pull strategy — destructive but low risk per CLAUDE.md Rollback Tiers)",
    },
    {  # idx 6 — disk cleanup prod-web-01
        "event_id":        "evt-8c3a1d5e",
        "action_id":       "act-6b9e2f4d",
        "plan_hash":       "sha256:c4a1…7e9b",
        "approver":        "(none — Low risk, auto)",
        "approval_source": "policy auto-approve (Low tier)",
        "before":          "/tmp 2.5 GB · apt cache 0.4 GB · root partition 36%",
        "after":           "/tmp 0.4 GB · apt cache 0 · root partition 34%",
        "command":         "find /tmp -type f -mtime +7 -delete && apt-get clean",
        "stdout_summary":  "freed 2.1 GB from /tmp, 0.4 GB from /var/cache/apt",
        "stderr_summary":  "",
        "rollback_status": "not applicable",
    },
    {  # idx 7 — pre-validation prod-web-01
        "event_id":        "evt-5e1f8b3c",
        "action_id":       "act-3a7d2e9f",
        "plan_hash":       "(pre-plan)",
        "approver":        "(n/a — pre-validation)",
        "approval_source": "n/a",
        "before":          "n/a",
        "after":           "validated (SSH 42ms, RHEL 8 OK, window active, no locks)",
        "command":         "ssh prod-web-01 'uname -a; cat /etc/os-release; test -f /var/lib/errander.lock'",
        "stdout_summary":  "Ubuntu 22.04.3 LTS · no lock file present",
        "stderr_summary":  "",
        "rollback_status": "n/a",
    },
]

# Pad if data.py grows
def audit_evidence_for(idx: int) -> dict[str, Any]:
    if idx < len(AUDIT_EVIDENCE):
        return AUDIT_EVIDENCE[idx]
    return {
        "event_id": f"evt-{idx:08x}", "action_id": f"act-{idx:08x}", "plan_hash": "sha256:…",
        "approver": "(unknown)", "approval_source": "(unknown)", "before": "", "after": "",
        "command": "", "stdout_summary": "", "stderr_summary": "", "rollback_status": "",
    }


# ── VM evidence (keyed by hostname) ─────────────────────────────────────────
#
# cpu_history / mem_history: 24 hourly float values, oldest → newest (last = now).
# cpu_history_7d / mem_history_7d: 42 values at 4 h intervals (7 days × 6 per day).
# disk_history: dict[partition, 24-point list].  Values are % utilisation.

VM_EVIDENCE: dict[str, dict[str, Any]] = {
    "prod-web-01": {
        "lock": None,
        "window": "inside (Tue/Thu 02:00 UTC)",
        "last_patched": "2026-04-22 02:09 UTC",
        "noop_now": False,
        "ssh_key_fp": "SHA256:abc1…ef72",
        # diurnal web traffic pattern; peaks ~14:00 UTC
        "cpu_history":    [18,16,14,13,12,14,18,24,30,35,38,40,42,44,42,40,38,35,30,28,25,22,20,18],
        "mem_history":    [40,40,39,39,40,40,41,42,43,44,44,45,45,45,44,44,43,43,42,42,41,41,40,40],
        "cpu_history_7d": [
            32,28,22,16,13,20,32,40,38,34,30,26,  # day -6 (Mon)
            28,24,18,14,12,18,28,36,34,30,26,22,  # day -5 (Tue — maint window dip at 02:00)
            30,26,20,15,13,19,30,38,36,32,28,24,  # day -4 (Wed)
            25,22,17,13,11,16,25,33,31,28,24,20,  # day -5 (Thu — quieter)
            22,18,15,12,10,14,22,30,28,25,21,18,  # day -2 (Fri)
            16,14,12,10,9, 12,18,24,22,20,17,15,  # day -1 (Sat — low traffic)
            18,16,14,13,12,14,18,24,30,35,38,18,  # today  (Sun → now)
        ],
        "mem_history_7d": [
            44,43,42,41,40,40,41,43,44,44,44,43,
            43,42,41,40,40,40,41,43,44,44,43,42,
            43,42,41,40,40,40,41,43,44,44,43,42,
            42,41,40,39,39,40,41,43,44,44,43,42,
            42,41,40,39,39,40,41,42,43,43,42,41,
            41,40,39,39,38,39,40,41,42,42,41,40,
            40,40,39,39,40,40,41,42,43,44,44,40,
        ],
        "disk_history": {
            "/":     [34]*24,
            "/var":  [52]*24,
            "/tmp":  [8,8,8,8,7,7,7,7,7,8,8,9,9,9,9,9,8,8,8,8,8,8,8,8],
            "/home": [23]*24,
        },
    },
    "prod-web-02": {
        "lock": None,
        "window": "inside (Tue/Thu 02:00 UTC)",
        "last_patched": "2026-04-23 02:12 UTC",
        "noop_now": True,
        "ssh_key_fp": "SHA256:7d4a…b91c",
        "cpu_history":    [12,11,10,10,9,10,13,18,22,26,28,27,28,28,27,26,24,22,20,18,17,15,14,12],
        "mem_history":    [36,36,35,35,35,36,37,38,39,40,40,40,41,41,40,40,39,39,38,38,37,37,36,36],
        "cpu_history_7d": [
            22,18,14,10, 9,13,22,28,26,23,20,18,
            20,16,12, 9, 8,12,20,26,24,21,18,16,
            21,17,13,10, 9,13,21,27,25,22,19,17,
            18,15,11, 9, 8,11,18,24,22,20,17,15,
            17,14,10, 8, 7,10,17,22,20,18,16,14,
            12,10, 8, 7, 6, 8,12,16,15,13,11,10,
            12,11,10,10, 9,10,13,18,22,26,28,12,
        ],
        "mem_history_7d": [
            40,39,38,37,37,38,39,41,41,40,40,39,
        ] * 7,
        "disk_history": {
            "/":     [28]*24,
            "/var":  [43]*24,
            "/tmp":  [5]*24,
            "/home": [18]*24,
        },
    },
    "prod-api-01": {
        "lock": None,
        "window": "inside",
        "last_patched": "2026-04-23 02:08 UTC (warning: 2 kernel held back)",
        "noop_now": False,
        "ssh_key_fp": "SHA256:9f2d…4b8e",
        # API server: sustained load through the day, recent spike (batch processing surge)
        "cpu_history": [40,43,45,48,52,55,58,60,62,65,64,62,60,58,62,65,68,70,72,74,73,71,70,72],
        "mem_history": [51,52,52,53,53,54,54,55,55,56,56,57,57,58,58,59,59,60,60,61,61,61,61,61],
        "cpu_history_7d": [
            55,50,46,42,40,44,55,65,63,60,57,54,  # Mon
            52,48,44,40,38,42,52,62,60,57,54,51,  # Tue (post-maint)
            54,50,46,42,40,44,54,64,62,59,56,53,  # Wed
            50,46,42,38,36,40,50,60,58,55,52,49,  # Thu
            48,44,40,36,34,38,48,58,56,53,50,47,  # Fri
            38,34,30,28,26,30,38,46,44,42,40,38,  # Sat
            40,43,45,48,52,55,58,60,62,65,70,40,  # Sun → now
        ],
        "mem_history_7d": [
            48,47,46,46,46,47,48,50,51,51,51,50,  # Mon
            49,48,47,47,47,48,49,51,52,52,52,51,  # Tue
            50,49,48,48,48,49,50,52,53,53,53,52,  # Wed
            51,50,49,49,49,50,51,53,54,54,54,53,  # Thu (slowly climbing)
            52,51,50,50,50,51,52,54,55,55,55,54,  # Fri
            53,52,51,51,51,52,53,55,56,56,56,55,  # Sat
            54,54,54,55,55,56,56,57,58,59,61,54,  # Sun → now (reaching 61%)
        ],
        "disk_history": {
            "/":     [35,35,35,35,35,35,35,35,35,36,36,36,36,36,36,36,36,36,37,37,37,37,38,38],
            "/var":  [52]*24,
            "/tmp":  [8,8,8,8,7,7,7,7,7,8,9,10,11,11,10,9,8,8,8,8,8,8,8,8],
            "/home": [23]*24,
        },
    },
    "prod-api-02": {
        "lock": None,
        "window": "inside",
        "last_patched": "2026-04-22 02:09 UTC",
        "noop_now": False,
        "ssh_key_fp": "SHA256:1d7e…4c9b",
        "cpu_history": [32,30,28,26,25,28,35,42,45,48,46,44,44,46,48,50,48,45,43,40,38,36,34,32],
        "mem_history": [48,48,47,47,47,48,49,51,52,53,53,53,54,54,54,54,53,53,52,51,50,50,49,49],
        "cpu_history_7d": [
            44,40,35,30,28,33,44,52,50,48,45,42,
        ] * 7,
        "mem_history_7d": [
            52,51,50,50,50,51,52,54,55,55,54,53,
        ] * 7,
        "disk_history": {
            "/":     [29]*24,
            "/var":  [47]*24,
            "/tmp":  [6]*24,
            "/home": [21]*24,
        },
    },
    "prod-db-01": {
        "lock": "errander-agent · 02:09:01 → 02:39:01 UTC",
        "window": "EMERGENCY override (--force)",
        "last_patched": "2026-04-21 02:15 UTC",
        "noop_now": False,
        "ssh_key_fp": "SHA256:6a4f…1c3d",
        # DB under OOM pressure: MEM climbing relentlessly, CPU spikes with GC
        "cpu_history": [25,22,20,18,20,22,25,28,32,38,42,45,48,52,55,58,62,65,60,58,55,52,55,58],
        "mem_history": [70,71,72,73,74,75,76,78,79,80,82,84,85,86,88,89,90,91,92,93,94,94,93,94],
        "cpu_history_7d": [
            30,26,22,18,18,22,30,38,36,34,30,26,  # Mon (normal)
            28,24,20,16,16,20,28,36,34,32,28,24,  # Tue
            30,26,22,18,18,22,30,38,38,36,32,28,  # Wed (starting to climb)
            32,28,24,20,20,24,32,40,42,40,36,32,  # Thu (noticeable)
            36,32,28,24,24,28,36,44,46,44,40,36,  # Fri
            40,36,32,28,28,32,40,48,50,48,46,42,  # Sat
            25,22,20,18,20,22,25,28,32,38,55,25,  # Sun → now (sharp recent spike)
        ],
        "mem_history_7d": [
            62,61,61,61,62,62,63,64,64,64,63,62,  # Mon
            63,63,63,63,64,64,65,66,66,66,65,64,  # Tue
            65,65,65,65,66,66,67,68,68,68,67,66,  # Wed
            67,67,67,67,68,68,69,70,70,70,69,68,  # Thu
            69,69,69,70,70,71,72,74,75,76,77,78,  # Fri (accelerating)
            79,80,82,83,84,85,86,87,88,89,90,91,  # Sat (critical zone)
            92,92,93,93,93,94,94,94,94,93,94,92,  # Sun → now
        ],
        "disk_history": {
            "/":     [45]*24,
            "/var":  [60,60,61,61,62,62,63,63,64,65,65,66,67,68,69,69,70,71,72,73,74,74,75,76],
            "/tmp":  [12,12,13,14,15,16,17,18,18,17,17,16,15,14,14,15,16,17,18,19,20,20,21,22],
            "/home": [34]*24,
        },
    },
    "prod-db-02": {
        "lock": None,
        "window": "inside",
        "last_patched": "2026-04-22 02:11 UTC",
        "noop_now": True,
        "ssh_key_fp": "SHA256:2b9c…5e3f",
        "cpu_history": [15,14,13,12,12,14,18,22,24,26,25,24,24,25,26,27,26,24,22,20,18,17,16,15],
        "mem_history": [52,52,52,51,51,52,53,55,56,57,57,57,58,58,58,58,57,57,56,55,54,53,52,52],
        "cpu_history_7d": [
            22,18,15,12,12,15,22,28,26,24,22,20,
        ] * 7,
        "mem_history_7d": [
            56,55,54,54,54,55,56,58,59,59,58,57,
        ] * 7,
        "disk_history": {
            "/":     [42]*24,
            "/var":  [58]*24,
            "/tmp":  [9]*24,
            "/home": [28]*24,
        },
    },
    "staging-web-01": {
        "lock": None,
        "window": "inside",
        "last_patched": "2026-04-22 02:05 UTC",
        "noop_now": True,
        "ssh_key_fp": "SHA256:8c3a…1d5e",
        "cpu_history": [8,7,7,6,6,7,10,14,16,18,17,16,16,17,18,19,18,16,14,12,10,9,8,8],
        "mem_history": [32,32,31,31,31,32,33,34,35,36,36,36,37,37,36,36,35,35,34,33,33,32,32,32],
        "cpu_history_7d": [14,12,9,7,7,10,14,18,17,16,14,12] * 7,
        "mem_history_7d": [36,35,34,34,34,35,36,38,38,38,37,36] * 7,
        "disk_history": {
            "/":     [22]*24,
            "/var":  [38]*24,
            "/tmp":  [4]*24,
            "/home": [15]*24,
        },
    },
    "staging-api-01": {
        "lock": None,
        "window": "inside",
        "last_patched": "2026-04-23 02:05 UTC (FAILED — rolled back)",
        "noop_now": False,
        "ssh_key_fp": "SHA256:5e1f…8b3c",
        # CPU spike during failed patching attempt at ~02:05, then rolled back
        "cpu_history": [20,18,16,14,12,  # 00-04
                        65,68,72,75,70,65,60,  # 05-11: patching spike
                        22,20,18,16,18,22,25,28,30,28,26,24],  # 12-23
        "mem_history": [38,38,37,37,37,  # 00-04
                        45,50,55,58,55,50,45,  # 05-11: patching surge
                        40,39,39,38,39,40,41,42,43,42,41,40],  # 12-23
        "cpu_history_7d": [24,20,16,13,12,16,24,32,30,27,24,21] * 7,
        "mem_history_7d": [40,39,38,38,38,39,40,42,43,43,42,41] * 7,
        "disk_history": {
            "/":     [31]*24,
            "/var":  [44]*24,
            "/tmp":  [7]*24,
            "/home": [19]*24,
        },
    },
    "staging-db-01": {
        "lock": None,
        "window": "inside",
        "last_patched": "2026-04-22 02:11 UTC",
        "noop_now": True,
        "ssh_key_fp": "SHA256:abc4…1234",
        "cpu_history": [12,10,9,8,8,10,14,18,20,22,21,20,20,21,22,23,22,20,18,16,14,13,12,12],
        "mem_history": [44,44,43,43,43,44,45,47,48,49,49,49,50,50,49,49,48,47,46,45,44,44,43,44],
        "cpu_history_7d": [18,15,11,9,9,12,18,24,22,20,18,16] * 7,
        "mem_history_7d": [48,47,46,46,46,47,48,50,51,51,50,49] * 7,
        "disk_history": {
            "/":     [26]*24,
            "/var":  [41]*24,
            "/tmp":  [5]*24,
            "/home": [17]*24,
        },
    },
    "dev-web-01": {
        "lock": None,
        "window": "anytime (DEV)",
        "last_patched": "2026-04-22 14:00 UTC",
        "noop_now": True,
        "ssh_key_fp": "SHA256:def8…9abc",
        "cpu_history": [5,5,4,4,4,5,8,12,15,18,16,14,14,15,16,18,15,12,10,8,7,6,5,5],
        "mem_history": [28,28,27,27,27,28,29,30,31,32,32,32,33,33,32,32,31,30,29,29,28,28,27,28],
        "cpu_history_7d": [10,8,5,4,4,6,10,14,13,12,10,9] * 7,
        "mem_history_7d": [30,29,28,28,28,29,30,32,33,33,32,31] * 7,
        "disk_history": {
            "/":     [18]*24,
            "/var":  [32]*24,
            "/tmp":  [3]*24,
            "/home": [12]*24,
        },
    },
    "dev-api-01": {
        "lock": None,
        "window": "anytime (DEV)",
        "last_patched": "(never — fresh provisioning)",
        "noop_now": False,
        "ssh_key_fp": "SHA256:111a…bbcc",
        "cpu_history": [6,5,5,4,4,5,8,12,14,16,15,14,14,15,16,18,15,12,10,8,7,6,5,6],
        "mem_history": [24,24,23,23,23,24,25,26,27,28,28,28,29,29,28,28,27,26,25,25,24,24,23,24],
        "cpu_history_7d": [9,7,5,4,4,6,9,13,12,11,9,8] * 7,
        "mem_history_7d": [27,26,25,25,25,26,27,29,30,30,29,28] * 7,
        "disk_history": {
            "/":     [14]*24,
            "/var":  [28]*24,
            "/tmp":  [2]*24,
            "/home": [9]*24,
        },
    },
}


# ── Batch evidence (keyed by id) ────────────────────────────────────────────

BATCH_EVIDENCE: dict[str, dict[str, Any]] = {
    "prod-0423-0200":    {"plan_hash": "sha256:abc1…ef72", "approver": "sre-oncall (3 actions)",        "succeeded": 85, "failed": 2,  "partial": 0, "rolled_back": 1, "approval_source": "slack:#errander-approvals"},
    "prod-0422-0200":    {"plan_hash": "sha256:7d4a…b91c", "approver": "(none — all Low/Medium auto)",   "succeeded": 91, "failed": 0,  "partial": 0, "rolled_back": 0, "approval_source": "policy"},
    "staging-0422-1400": {"plan_hash": "sha256:9c4f…0a13", "approver": "sre-oncall (1 action)",          "succeeded": 24, "failed": 0,  "partial": 0, "rolled_back": 0, "approval_source": "slack"},
    "prod-0421-0200":    {"plan_hash": "sha256:5b3c…d91f", "approver": "sre-oncall (1 action)",          "succeeded": 88, "failed": 1,  "partial": 0, "rolled_back": 0, "approval_source": "slack"},
    "prod-0418-0200":    {"plan_hash": "sha256:4a8e…0e72", "approver": "sre-oncall (2 actions)",         "succeeded": 72, "failed": 2,  "partial": 2, "rolled_back": 0, "approval_source": "slack"},
    "staging-0418-0200": {"plan_hash": "sha256:0e8d…3a47", "approver": "(none — all SSH-failed before approval)", "succeeded": 15, "failed": 3, "partial": 0, "rolled_back": 0, "approval_source": "n/a"},
    "prod-0417-0200":    {"plan_hash": "sha256:c4a1…7e9b", "approver": "sre-oncall (2 actions)",         "succeeded": 92, "failed": 0,  "partial": 0, "rolled_back": 0, "approval_source": "slack"},
    "prod-0416-0200":    {"plan_hash": "sha256:f8e2…1a4d", "approver": "(none — Low/Medium auto)",       "succeeded": 88, "failed": 0,  "partial": 0, "rolled_back": 0, "approval_source": "policy"},
}
