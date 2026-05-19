"""Dummy data for the development UI."""
from __future__ import annotations

from typing import Any

VMS: list[dict[str, Any]] = [
    {
        "hostname": "prod-web-01",    "os": "Ubuntu 22.04", "env": "PROD",
        "status": "ok",      "disk": 34, "cpu": 24, "mem": 62,
        "pending_patches": 3, "last_action_type": "Log Rotation",
        "last_action": "2026-04-23 02:14", "ip": "10.0.1.10",
        "uptime": "62d 4h 11m", "note": "",
    },
    {
        "hostname": "prod-web-02",    "os": "Ubuntu 22.04", "env": "PROD",
        "status": "ok",      "disk": 41, "cpu": 18, "mem": 47,
        "pending_patches": 5, "last_action_type": "OS Patching",
        "last_action": "2026-04-23 02:12", "ip": "10.0.1.11",
        "uptime": "62d 4h 11m", "note": "",
    },
    {
        "hostname": "prod-api-01",    "os": "RHEL 8.7",     "env": "PROD",
        "status": "warning", "disk": 78, "cpu": 45, "mem": 78,
        "pending_patches": 8, "last_action_type": "OS Patching",
        "last_action": "2026-04-23 02:08", "ip": "10.0.1.12",
        "uptime": "47d 14h 22m", "note": "Disk usage high — cleanup recommended",
    },
    {
        "hostname": "prod-api-02",    "os": "RHEL 8.7",     "env": "PROD",
        "status": "ok",      "disk": 52, "cpu": 32, "mem": 59,
        "pending_patches": 6, "last_action_type": "Docker Prune",
        "last_action": "2026-04-23 02:10", "ip": "10.0.1.13",
        "uptime": "47d 14h 22m", "note": "",
    },
    {
        "hostname": "prod-db-01",     "os": "Debian 11",    "env": "PROD",
        "status": "pending", "disk": 44, "cpu": 94, "mem": 91,
        "pending_patches": 2, "last_action_type": "Pre-Validation",
        "last_action": "2026-04-23 02:09", "ip": "10.0.1.45",
        "uptime": "91d 6h 03m", "note": "Service restart queued — awaiting approval",
    },
    {
        "hostname": "prod-db-02",     "os": "Debian 11",    "env": "PROD",
        "status": "ok",      "disk": 29, "cpu": 21, "mem": 44,
        "pending_patches": 2, "last_action_type": "Log Rotation",
        "last_action": "2026-04-23 02:11", "ip": "10.0.1.46",
        "uptime": "91d 6h 03m", "note": "",
    },
    {
        "hostname": "staging-web-01", "os": "Ubuntu 22.04", "env": "STAGING",
        "status": "ok",      "disk": 19, "cpu": 11, "mem": 38,
        "pending_patches": 4, "last_action_type": "Disk Cleanup",
        "last_action": "2026-04-23 02:16", "ip": "10.0.2.10",
        "uptime": "14d 2h 05m", "note": "",
    },
    {
        "hostname": "staging-api-01", "os": "RHEL 8.7",     "env": "STAGING",
        "status": "failed",  "disk": 55, "cpu": 38, "mem": 67,
        "pending_patches": 14, "last_action_type": "OS Patching",
        "last_action": "2026-04-23 02:05", "ip": "10.0.2.12",
        "uptime": "14d 2h 05m", "note": "Patch rollback triggered — glibc conflict",
    },
    {
        "hostname": "staging-db-01",  "os": "Debian 11",    "env": "STAGING",
        "status": "ok",      "disk": 31, "cpu": 16, "mem": 52,
        "pending_patches": 3, "last_action_type": "Log Rotation",
        "last_action": "2026-04-23 02:17", "ip": "10.0.2.45",
        "uptime": "14d 2h 05m", "note": "",
    },
    {
        "hostname": "dev-web-01",     "os": "Ubuntu 22.04", "env": "DEV",
        "status": "ok",      "disk": 22, "cpu": 8,  "mem": 31,
        "pending_patches": 7, "last_action_type": "OS Patching",
        "last_action": "2026-04-22 14:00", "ip": "10.0.3.10",
        "uptime": "3d 8h 47m", "note": "",
    },
    {
        "hostname": "dev-api-01",     "os": "Ubuntu 22.04", "env": "DEV",
        "status": "ok",      "disk": 55, "cpu": 19, "mem": 43,
        "pending_patches": 7, "last_action_type": "Pre-Validation",
        "last_action": "2026-04-22 14:00", "ip": "10.0.3.12",
        "uptime": "3d 8h 47m", "note": "",
    },
]

APPROVALS: list[dict[str, Any]] = [
    {
        "id": "appr-001",
        "action": "SERVICE RESTART",
        "tier": "HIGH RISK",
        "hostname": "prod-db-01",
        "os": "Debian 11",
        "ip": "10.0.1.45",
        "env": "PROD",
        "countdown": "23:47",
        "vm_cpu": 94,
        "vm_mem": 91,
        "vm_disk": 44,
        "vm_load": "4.2 / 4.4 / 3.9",
        "vm_uptime": "91d 6h 03m",
        "trigger": "PostgreSQL consuming 94% memory for 47 min — kernel OOM kill imminent",
        "reject_consequence": (
            "PostgreSQL will likely be kernel-OOM-killed within minutes. "
            "Risk of data corruption on active write transactions. "
            "No automatic mitigation available — manual intervention required."
        ),
        "rollback_strategy": "Service auto-restarts on OOM; pre-restart data snapshot taken at 2026-04-23 01:58 UTC",
        "reasoning": (
            "LLM analysis: PostgreSQL process consuming 94% memory for 47 minutes. "
            "OOM kill imminent. Controlled restart recommended to prevent data corruption. "
            "Rollback: service will auto-restart; snapshot taken at 2026-04-23 01:58 UTC."
        ),
        "commands": [
            "systemctl restart postgresql",
            "# Pre-check:  pg_isready -h localhost",
            "# Post-check: systemctl is-active postgresql",
        ],
        "header_color": "#7c3aed",
        "tier_color": "#dc2626",
    },
    {
        "id": "appr-002",
        "action": "OS PATCHING",
        "tier": "MEDIUM",
        "hostname": "staging-api-01",
        "os": "RHEL 8.7",
        "ip": "10.0.2.12",
        "env": "STAGING",
        "countdown": "18:12",
        "vm_cpu": 38,
        "vm_mem": 67,
        "vm_disk": 55,
        "vm_load": "1.2 / 0.9 / 0.8",
        "vm_uptime": "14d 2h 05m",
        "trigger": "14 security packages pending including 2 critical CVEs: CVE-2024-1234 (openssl), CVE-2024-5678 (glibc)",
        "reject_consequence": (
            "2 critical CVEs remain unpatched on RHEL 8.7. "
            "System stays vulnerable to remote code execution via openssl and privilege escalation via glibc. "
            "Agent will retry at next maintenance window (Tue 02:00 UTC)."
        ),
        "rollback_strategy": "Full package manifest snapshot saved before execution. Per-package rollback via pinned version list.",
        "reasoning": (
            "14 security packages pending (2 critical CVEs: CVE-2024-1234, CVE-2024-5678). "
            "Non-kernel updates only. Pre-patch snapshot of installed packages saved. "
            "Rollback available via saved package manifest."
        ),
        "packages": [
            "openssl-3.0.7",  "glibc-2.34",     "curl-7.76",      "systemd-249",
            "python3-3.9",    "libssl3",         "vim-9.0",        "bind-utils",
            "krb5-libs",      "openssh-server",  "nss",            "dbus",
            "tzdata",         "expat",
        ],
        "header_color": "#d97706",
        "tier_color": "#d97706",
    },
]

AUDIT_EVENTS: list[dict[str, Any]] = [
    {
        "ts": "2026-04-23 02:14:33", "batch": "prod-0423-0200",
        "vm": "prod-api-01",    "action": "Log Rotation",
        "status": "ok",     "duration": "12s",    "op": "agent",
        "detail": "Rotated 1.2 GB across /var/log/nginx, /var/log/syslog (12 files compressed)",
    },
    {
        "ts": "2026-04-23 02:12:01", "batch": "prod-0423-0200",
        "vm": "prod-web-02",    "action": "OS Patching",
        "status": "ok",     "duration": "3m 47s", "op": "agent",
        "detail": "Updated 11 packages: openssl 3.0.5→3.0.7, curl 7.81→7.88, libssl3, systemd, python3, dbus (6 more)",
    },
    {
        "ts": "2026-04-23 02:09:44", "batch": "prod-0423-0200",
        "vm": "prod-db-01",     "action": "Pre-Validation",
        "status": "pending", "duration": "—",      "op": "agent",
        "detail": "Awaiting Slack approval for SERVICE RESTART — PostgreSQL 94% memory, OOM imminent",
    },
    {
        "ts": "2026-04-23 02:08:22", "batch": "prod-0423-0200",
        "vm": "prod-api-01",    "action": "OS Patching",
        "status": "ok",     "duration": "4m 18s", "op": "agent",
        "detail": "Updated 8 packages: glibc 2.31→2.34, bind-utils, krb5-libs, nss, openssh-server, tzdata (3 more)",
    },
    {
        "ts": "2026-04-23 02:05:17", "batch": "prod-0423-0200",
        "vm": "staging-api-01", "action": "OS Patching",
        "status": "failed",  "duration": "2m 04s", "op": "agent",
        "detail": "Rollback triggered: glibc-2.34 conflicts with libssl3-1.1 on RHEL 8.7 (repo version mismatch) — reverted 14 packages",
    },
    {
        "ts": "2026-04-23 02:03:55", "batch": "prod-0423-0200",
        "vm": "prod-api-02",    "action": "Docker Prune",
        "status": "ok",     "duration": "34s",    "op": "agent",
        "detail": "Freed 8.3 GB — removed 12 dangling images, 3 unused volumes, 5 stopped containers",
    },
    {
        "ts": "2026-04-23 02:01:12", "batch": "prod-0423-0200",
        "vm": "prod-web-01",    "action": "Disk Cleanup",
        "status": "ok",     "duration": "8s",     "op": "agent",
        "detail": "Freed 2.1 GB from /tmp (files >7d), 0.4 GB apt cache — root partition: 36% → 34%",
    },
    {
        "ts": "2026-04-23 02:00:44", "batch": "prod-0423-0200",
        "vm": "prod-web-01",    "action": "Pre-Validation",
        "status": "ok",     "duration": "3s",     "op": "agent",
        "detail": "SSH OK (42ms), RHEL 8 verified, maintenance window active, no active locks",
    },
    {
        "ts": "2026-04-22 02:11:02", "batch": "prod-0422-0200",
        "vm": "prod-db-02",     "action": "Log Rotation",
        "status": "ok",     "duration": "9s",     "op": "agent",
        "detail": "Rotated 0.8 GB across /var/log/postgresql, /var/log/syslog (8 files compressed)",
    },
    {
        "ts": "2026-04-22 02:09:14", "batch": "prod-0422-0200",
        "vm": "prod-api-02",    "action": "OS Patching",
        "status": "ok",     "duration": "5m 12s", "op": "agent",
        "detail": "Updated 14 packages: curl, openssl, expat, vim, bind-utils, python3-3.9 (8 more security updates)",
    },
    {
        "ts": "2026-04-22 02:07:33", "batch": "prod-0422-0200",
        "vm": "prod-web-01",    "action": "Docker Prune",
        "status": "ok",     "duration": "28s",    "op": "agent",
        "detail": "Freed 4.2 GB — removed 7 dangling images, 2 unused networks",
    },
    {
        "ts": "2026-04-22 02:05:08", "batch": "prod-0422-0200",
        "vm": "staging-web-01", "action": "Disk Cleanup",
        "status": "ok",     "duration": "6s",     "op": "agent",
        "detail": "Freed 1.1 GB from /tmp, 0.3 GB yum cache — root partition: 23% → 19%",
    },
]

BATCHES: list[dict[str, Any]] = [
    {
        "id": "prod-0423-0200",    "started": "2026-04-23 02:00", "env": "PROD",
        "vms": 11, "actions": 87, "status": "completed", "duration": "14m 32s", "errors": 2,
        "failed_vms": ["staging-api-01"],
        "error_summary": "glibc conflict on staging-api-01 (patching rolled back)",
    },
    {
        "id": "prod-0422-0200",    "started": "2026-04-22 02:00", "env": "PROD",
        "vms": 11, "actions": 91, "status": "completed", "duration": "12m 08s", "errors": 0,
        "failed_vms": [],
        "error_summary": "",
    },
    {
        "id": "staging-0422-1400", "started": "2026-04-22 14:00", "env": "STAGING",
        "vms":  3, "actions": 24, "status": "completed", "duration": "4m 22s",  "errors": 0,
        "failed_vms": [],
        "error_summary": "",
    },
    {
        "id": "prod-0421-0200",    "started": "2026-04-21 02:00", "env": "PROD",
        "vms": 11, "actions": 89, "status": "completed", "duration": "13m 44s", "errors": 1,
        "failed_vms": ["prod-api-01"],
        "error_summary": "2 kernel packages held back on prod-api-01 (expected)",
    },
    {
        "id": "prod-0418-0200",    "started": "2026-04-18 02:00", "env": "PROD",
        "vms": 11, "actions": 76, "status": "partial",   "duration": "19m 05s", "errors": 4,
        "failed_vms": ["prod-api-01", "prod-db-01", "staging-api-01", "staging-db-01"],
        "error_summary": "SSH timeout on 2 VMs, disk gate blocked 2 patching jobs",
    },
    {
        "id": "staging-0418-0200", "started": "2026-04-18 02:00", "env": "STAGING",
        "vms":  3, "actions": 18, "status": "failed",    "duration": "6m 31s",  "errors": 3,
        "failed_vms": ["staging-api-01", "staging-db-01", "staging-web-01"],
        "error_summary": "All 3 VMs unreachable — SSH key mismatch after host rotation",
    },
    {
        "id": "prod-0417-0200",    "started": "2026-04-17 02:00", "env": "PROD",
        "vms": 11, "actions": 92, "status": "completed", "duration": "11m 52s", "errors": 0,
        "failed_vms": [],
        "error_summary": "",
    },
    {
        "id": "prod-0416-0200",    "started": "2026-04-16 02:00", "env": "PROD",
        "vms": 11, "actions": 88, "status": "completed", "duration": "10m 59s", "errors": 0,
        "failed_vms": [],
        "error_summary": "",
    },
]

VM_ACTIONS: dict[str, list[dict[str, Any]]] = {
    "prod-api-01": [
        {
            "ts": "2026-04-23 02:08", "action": "Log Rotation",
            "status": "ok",      "duration": "12s",    "op": "agent",
            "detail": "/var/log rotated 1.2 GB — nginx, syslog, auth.log (12 files)",
        },
        {
            "ts": "2026-04-23 02:06", "action": "OS Patching",
            "status": "ok",      "duration": "4m 18s", "op": "agent",
            "detail": "Updated 8 packages: glibc 2.31→2.34, bind-utils 9.16→9.18, nss 3.79, openssh-server 8.9p1, tzdata 2023c (3 more)",
        },
        {
            "ts": "2026-04-23 02:01", "action": "Pre-Validation",
            "status": "ok",      "duration": "3s",     "op": "agent",
            "detail": "SSH reachable (38ms), RHEL 8.7 verified, window active, no locks, sudo OK",
        },
        {
            "ts": "2026-04-21 02:14", "action": "OS Patching",
            "status": "warning", "duration": "6m 02s", "op": "agent",
            "detail": "2 packages held back (kernel-5.14, kernel-headers) — kernel patching blocked by policy",
        },
        {
            "ts": "2026-04-21 02:08", "action": "Disk Cleanup",
            "status": "ok",      "duration": "8s",     "op": "agent",
            "detail": "Freed 4.1 GB from /tmp (files >7d), 1.2 GB yum cache — root: 82% → 73%",
        },
    ],
    "prod-db-01": [
        {
            "ts": "2026-04-23 02:09", "action": "Pre-Validation",
            "status": "pending", "duration": "—",      "op": "agent",
            "detail": "Awaiting Slack approval — SERVICE RESTART queued (PostgreSQL OOM risk)",
        },
        {
            "ts": "2026-04-21 02:20", "action": "Log Rotation",
            "status": "ok",      "duration": "14s",    "op": "agent",
            "detail": "Rotated 2.1 GB /var/log/postgresql, /var/log/syslog — 18 files archived",
        },
        {
            "ts": "2026-04-21 02:15", "action": "Pre-Validation",
            "status": "ok",      "duration": "4s",     "op": "agent",
            "detail": "SSH reachable (31ms), Debian 11 verified, window active, no locks",
        },
    ],
    "staging-api-01": [
        {
            "ts": "2026-04-23 02:05", "action": "OS Patching",
            "status": "failed",  "duration": "2m 04s", "op": "agent",
            "detail": "Rollback triggered: glibc-2.34 conflicts with libssl3-1.1 (RHEL 8.7 repo mismatch) — 14 packages reverted",
        },
        {
            "ts": "2026-04-23 02:02", "action": "Pre-Validation",
            "status": "ok",      "duration": "3s",     "op": "agent",
            "detail": "SSH reachable (55ms), RHEL 8.7 verified, window active, no locks",
        },
        {
            "ts": "2026-04-21 02:07", "action": "Disk Cleanup",
            "status": "ok",      "duration": "9s",     "op": "agent",
            "detail": "Freed 3.2 GB from /tmp, 0.8 GB yum cache — root: 61% → 55%",
        },
    ],
}

ACTIVE_BATCH = {
    "id": "prod-2026-04-23-0200",
    "status": "completed",
    "vms_done": 11,
    "vms_total": 11,
    "actions_done": 87,
    "actions_total": 87,
    "duration": "14m 32s",
    "patched": 9,
    "rotations": 11,
    "prunes": 5,
    "errors": 2,
}
