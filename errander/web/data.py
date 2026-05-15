"""Dummy data for the development UI."""
from __future__ import annotations

VMS: list[dict[str, object]] = [
    {"hostname": "prod-web-01",    "os": "Ubuntu 22.04", "env": "PROD",    "status": "ok",      "disk": 34, "last_action": "2026-04-23 02:14", "ip": "10.0.1.10", "uptime": "62d 4h 11m",  "note": ""},
    {"hostname": "prod-web-02",    "os": "Ubuntu 22.04", "env": "PROD",    "status": "ok",      "disk": 41, "last_action": "2026-04-23 02:12", "ip": "10.0.1.11", "uptime": "62d 4h 11m",  "note": ""},
    {"hostname": "prod-api-01",    "os": "RHEL 8.7",     "env": "PROD",    "status": "warning", "disk": 78, "last_action": "2026-04-23 02:08", "ip": "10.0.1.12", "uptime": "47d 14h 22m", "note": "Disk usage high"},
    {"hostname": "prod-api-02",    "os": "RHEL 8.7",     "env": "PROD",    "status": "ok",      "disk": 52, "last_action": "2026-04-23 02:10", "ip": "10.0.1.13", "uptime": "47d 14h 22m", "note": ""},
    {"hostname": "prod-db-01",     "os": "Debian 11",    "env": "PROD",    "status": "pending", "disk": 44, "last_action": "2026-04-23 02:09", "ip": "10.0.1.45", "uptime": "91d 6h 03m",  "note": "Service restart queued"},
    {"hostname": "prod-db-02",     "os": "Debian 11",    "env": "PROD",    "status": "ok",      "disk": 29, "last_action": "2026-04-23 02:11", "ip": "10.0.1.46", "uptime": "91d 6h 03m",  "note": ""},
    {"hostname": "staging-web-01", "os": "Ubuntu 22.04", "env": "STAGING", "status": "ok",      "disk": 19, "last_action": "2026-04-23 02:16", "ip": "10.0.2.10", "uptime": "14d 2h 05m",  "note": ""},
    {"hostname": "staging-api-01", "os": "RHEL 8.7",     "env": "STAGING", "status": "failed",  "disk": 55, "last_action": "2026-04-23 02:05", "ip": "10.0.2.12", "uptime": "14d 2h 05m",  "note": "Patch rollback triggered"},
    {"hostname": "staging-db-01",  "os": "Debian 11",    "env": "STAGING", "status": "ok",      "disk": 31, "last_action": "2026-04-23 02:17", "ip": "10.0.2.45", "uptime": "14d 2h 05m",  "note": ""},
    {"hostname": "dev-web-01",     "os": "Ubuntu 22.04", "env": "DEV",     "status": "ok",      "disk": 22, "last_action": "2026-04-22 14:00", "ip": "10.0.3.10", "uptime": "3d 8h 47m",   "note": ""},
    {"hostname": "dev-api-01",     "os": "Ubuntu 22.04", "env": "DEV",     "status": "ok",      "disk": 55, "last_action": "2026-04-22 14:00", "ip": "10.0.3.12", "uptime": "3d 8h 47m",   "note": ""},
]

APPROVALS: list[dict[str, object]] = [
    {
        "id": "appr-001",
        "action": "SERVICE RESTART",
        "tier": "HIGH RISK",
        "hostname": "prod-db-01",
        "os": "Debian 11",
        "ip": "10.0.1.45",
        "env": "PROD",
        "countdown": "23:47",
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
        "reasoning": (
            "14 security packages pending (2 critical CVEs: CVE-2024-1234, CVE-2024-5678). "
            "Non-kernel updates only. Pre-patch snapshot of installed packages saved. "
            "Rollback available via saved package manifest."
        ),
        "packages": ["openssl-3.0.7", "glibc-2.34", "curl-7.76", "systemd-249",
                     "python3-3.9", "libssl3", "vim-9.0", "bind-utils", "krb5-libs",
                     "openssh-server", "nss", "dbus", "tzdata", "expat"],
        "header_color": "#d97706",
        "tier_color": "#d97706",
    },
]

AUDIT_EVENTS: list[dict[str, object]] = [
    {"ts": "2026-04-23 02:14:33", "batch": "prod-0423-0200", "vm": "prod-api-01",    "action": "Log Rotation",   "status": "ok",      "duration": "12s",    "op": "agent", "detail": "Rotated 1.2 GB /var/log"},
    {"ts": "2026-04-23 02:12:01", "batch": "prod-0423-0200", "vm": "prod-web-02",    "action": "OS Patching",    "status": "ok",      "duration": "3m 47s", "op": "agent", "detail": "11 packages updated"},
    {"ts": "2026-04-23 02:09:44", "batch": "prod-0423-0200", "vm": "prod-db-01",     "action": "Pre-Validation", "status": "pending", "duration": "—",      "op": "agent", "detail": "Awaiting approval: service restart"},
    {"ts": "2026-04-23 02:08:22", "batch": "prod-0423-0200", "vm": "prod-api-01",    "action": "OS Patching",    "status": "ok",      "duration": "4m 18s", "op": "agent", "detail": "8 packages updated"},
    {"ts": "2026-04-23 02:05:17", "batch": "prod-0423-0200", "vm": "staging-api-01", "action": "OS Patching",    "status": "failed",  "duration": "2m 04s", "op": "agent", "detail": "Rollback triggered: glibc conflict"},
    {"ts": "2026-04-23 02:03:55", "batch": "prod-0423-0200", "vm": "prod-api-02",    "action": "Docker Prune",   "status": "ok",      "duration": "34s",    "op": "agent", "detail": "Freed 8.3 GB (12 images, 3 volumes)"},
    {"ts": "2026-04-23 02:01:12", "batch": "prod-0423-0200", "vm": "prod-web-01",    "action": "Disk Cleanup",   "status": "ok",      "duration": "8s",     "op": "agent", "detail": "Freed 2.1 GB from /tmp"},
    {"ts": "2026-04-23 02:00:44", "batch": "prod-0423-0200", "vm": "prod-web-01",    "action": "Pre-Validation", "status": "ok",      "duration": "3s",     "op": "agent", "detail": "SSH OK, RHEL 8 verified"},
    {"ts": "2026-04-22 02:11:02", "batch": "prod-0422-0200", "vm": "prod-db-02",     "action": "Log Rotation",   "status": "ok",      "duration": "9s",     "op": "agent", "detail": "Rotated 0.8 GB /var/log"},
    {"ts": "2026-04-22 02:09:14", "batch": "prod-0422-0200", "vm": "prod-api-02",    "action": "OS Patching",    "status": "ok",      "duration": "5m 12s", "op": "agent", "detail": "14 packages updated"},
    {"ts": "2026-04-22 02:07:33", "batch": "prod-0422-0200", "vm": "prod-web-01",    "action": "Docker Prune",   "status": "ok",      "duration": "28s",    "op": "agent", "detail": "Freed 4.2 GB (7 images)"},
    {"ts": "2026-04-22 02:05:08", "batch": "prod-0422-0200", "vm": "staging-web-01", "action": "Disk Cleanup",   "status": "ok",      "duration": "6s",     "op": "agent", "detail": "Freed 1.1 GB from /tmp"},
]

BATCHES: list[dict[str, object]] = [
    {"id": "prod-0423-0200",     "started": "2026-04-23 02:00", "env": "PROD",    "vms": 11, "actions": 87, "status": "completed", "duration": "14m 32s", "errors": 2},
    {"id": "prod-0422-0200",     "started": "2026-04-22 02:00", "env": "PROD",    "vms": 11, "actions": 91, "status": "completed", "duration": "12m 08s", "errors": 0},
    {"id": "staging-0422-1400",  "started": "2026-04-22 14:00", "env": "STAGING", "vms":  3, "actions": 24, "status": "completed", "duration": "4m 22s",  "errors": 0},
    {"id": "prod-0421-0200",     "started": "2026-04-21 02:00", "env": "PROD",    "vms": 11, "actions": 89, "status": "completed", "duration": "13m 44s", "errors": 1},
    {"id": "prod-0418-0200",     "started": "2026-04-18 02:00", "env": "PROD",    "vms": 11, "actions": 76, "status": "partial",   "duration": "19m 05s", "errors": 4},
    {"id": "staging-0418-0200",  "started": "2026-04-18 02:00", "env": "STAGING", "vms":  3, "actions": 18, "status": "failed",    "duration": "6m 31s",  "errors": 3},
    {"id": "prod-0417-0200",     "started": "2026-04-17 02:00", "env": "PROD",    "vms": 11, "actions": 92, "status": "completed", "duration": "11m 52s", "errors": 0},
    {"id": "prod-0416-0200",     "started": "2026-04-16 02:00", "env": "PROD",    "vms": 11, "actions": 88, "status": "completed", "duration": "10m 59s", "errors": 0},
]

VM_ACTIONS: dict[str, list[dict[str, object]]] = {
    "prod-api-01": [
        {"ts": "2026-04-23 02:08", "action": "Log Rotation",   "status": "ok",      "duration": "12s",    "op": "agent", "detail": "/var/log rotated 1.2 GB"},
        {"ts": "2026-04-23 02:06", "action": "OS Patching",    "status": "ok",      "duration": "4m 18s", "op": "agent", "detail": "8 packages updated"},
        {"ts": "2026-04-23 02:01", "action": "Pre-Validation", "status": "ok",      "duration": "3s",     "op": "agent", "detail": "SSH reachable, OS verified"},
        {"ts": "2026-04-21 02:14", "action": "OS Patching",    "status": "warning", "duration": "6m 02s", "op": "agent", "detail": "2 packages held back (kernel)"},
        {"ts": "2026-04-21 02:08", "action": "Disk Cleanup",   "status": "ok",      "duration": "8s",     "op": "agent", "detail": "4.1 GB freed from /tmp"},
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
