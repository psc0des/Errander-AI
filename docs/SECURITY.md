# Errander-AI Security Architecture

Overview of the security model, threat mitigations, and operational hardening for Errander-AI.

## Design Principle: MCP in Brain, Not Hands

> **Layer A (LLM, investigative)** may use MCP, CLI, skills, APIs, and recommend. **Layer B (execution, deterministic)** is human-approved, code-enforced, audited, and **never LLM-driven.**

See `docs/AI-ARCHITECTURE.md` for the full dual-layer model.

---

## Process Separation (R3, 2026-06-13)

Two OS processes with distinct privilege levels and secret access:

| Property | Agent Process | Web UI Process |
|---|---|---|
| **Command** | `python -m errander` | `python -m errander.web` |
| **OS User** | `errander-agent` | `errander-web` (nologin) |
| **Port** | 9090 (metrics only) | 9091 (approval UI) |
| **SSH Keys** | ✅ YES — executes on targets | ❌ NO — read-only, EACCES enforced |
| **Executor Modules** | ✅ YES — `execution/`, `agent/` | ❌ NO — import isolation tested |
| **LLM Secrets** | ✅ YES — ERRANDER_LLM_* | ❌ NO |
| **Slack Secrets** | ✅ YES — ERRANDER_SLACK_* | ❌ NO |
| **DB Secrets** | ✅ YES — ERRANDER_AUDIT_DB_URL | ✅ YES — shared audit DB |
| **Signing Secret** | ✅ YES — for docker_hygiene URLs | ✅ YES — verification |

**Boundary enforcement:**
- `tests/web/test_import_isolation.py` — walks `errander.web.*` imports, asserts no executor/agent/vm_graph modules in `sys.modules` after web startup
- File permissions: SSH keys mode 0600, owned by `errander-agent:errander-agent`; `errander-web` user denied read (EACCES)
- systemd: separate `EnvironmentFile` per process, no shared `.env`

---

## Authentication & Authorization (R2, 2026-06-12)

### User Model
- Scrypt password hashing (stdlib, salt + params stored per hash)
- DB-backed sessions (cookie token hashed at rest; survives restarts, cross-process compatible)
- Groups carry permissions; users belong to one+ groups
- Group membership resolved per-request (no restart needed for changes)

### Built-in Groups (seeded by migration #14)
- **admin** — `decide_approvals`, `manage_users`, `manage_settings`
- **reader** — no permissions (view-only on authenticated pages)
- Additional groups can be added via INSERTs (no schema migration needed)

### RBAC Gates (Server-Side)
- `_require_permission(request, permission)` raises 403 if session user lacks permission
- All decision handlers (`/ui/approvals/{batch_id}/{action}`) gate on `decide_approvals`
- Settings/inventory POST gates on `manage_settings` or `manage_users`
- Slack links no longer carry decision authority (notify-and-link only)

### Bootstrap Mode
- Zero users = read-only UI on loopback, mutations 403
- Non-loopback bind with zero users → refuses to start (fail closed)
- One-time seed via `ERRANDER_UI_USER`/`ERRANDER_UI_PASSWORD` env vars or `--user-add` CLI

### Public Mode (nginx Mode 2)
- Mandatory TOTP (RFC 6238) for admin group
- Optional IP allowlist (e.g., VPN, office range)
- Outbound Slack only (no inbound webhooks)
- TLS + HSTS (see nginx reference config)

---

## Exact-Object Approval (Mandatory for Destructive Actions)

All actions that remove/delete/destroy state must reference **exact object IDs** in approval artifacts, not action categories.

Example (docker_hygiene):
```
❌ BAD:  "Approve docker cleanup on web-01"
✅ GOOD: "Approve removal of image sha256:abc123... (dangling, 2.5GB)"
         "                  container worker-3 (exited 0 at 13:45)"
```

**Two drift gates per action:**
1. **Snapshot-level** — hash of assessment; mismatch between approval and execution → abort
2. **Per-object** — re-verify each object on target before removal; skip drifted objects

Reference: `errander/agent/subgraphs/docker_hygiene.py` (exemplar for object-level actions)

---

## Durable Approval Stores

Both regular and hygiene approvals stored in PostgreSQL (not in-memory, survives restarts):

| Table | Used By | Decide Strategy | Timeout Handling |
|---|---|---|---|
| `approval_requests` | patching, docker_hygiene, disk_cleanup, service_restart | atomic UPDATE ... WHERE status='pending' | agent-side reconciler, 30min default |
| `hygiene_approval_requests` | docker_hygiene | atomic UPDATE ... WHERE status='pending' | agent-side reconciler + web page |

**Cross-process safety:**
- Decision committed to DB before response sent to operator
- Agent restarts don't lose in-flight approvals
- Web and agent coordinate via DB rows (no shared memory)

---

## SSH Key Isolation

**In production (both processes on same VM or separate VMs):**

1. SSH keys live at `/home/errander-agent/.ssh/id_rsa` (mode 0600)
2. Only `errander-agent` can read them
3. `errander-web` user (nologin) cannot escalate or read them (EACCES)
4. Web process never imports `execution/` or `agent/` modules (static assertion via test)

**Verification script (bootstrap.sh):**
```bash
sudo -u errander-web cat /home/errander-agent/.ssh/id_rsa 2>&1 | grep -q "Permission denied" \
  || { echo "FAIL: errander-web can read SSH key"; exit 1; }
```

---

## Database Role Grants (PostgreSQL)

Single audit DB with two role-based connections:

```sql
-- Agent process: full read/write on all audit tables
GRANT SELECT, INSERT, UPDATE, DELETE ON approval_requests TO errander_agent;
GRANT SELECT, INSERT, UPDATE, DELETE ON hygiene_approval_requests TO errander_agent;
-- ... (all audit tables)

-- Web process: read approval state, write decisions + RBAC tables
GRANT SELECT, INSERT, UPDATE ON approval_requests TO errander_web;
GRANT SELECT, INSERT, UPDATE ON hygiene_approval_requests TO errander_web;
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO errander_web;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_groups TO errander_web;
GRANT SELECT ON group_permissions TO errander_web;
GRANT SELECT, INSERT ON sessions TO errander_web;
```

---

## nginx Mode 2 Hardening (Public Mode)

When deploying behind a reverse proxy (not private VPN):

### TLS & Certificates
- Use Let's Encrypt or corporate CA
- Minimum TLS 1.2 (prefer 1.3)
- Strong cipher suite (no RC4, MD5)

### HTTP Security Headers
- `Strict-Transport-Security: max-age=15552000; includeSubDomains; preload` (6 months + preload list)
- `X-Frame-Options: DENY` (prevent clickjacking)
- `X-Content-Type-Options: nosniff` (prevent MIME sniffing)
- `X-XSS-Protection: 1; mode=block` (legacy XSS filter)

### Rate Limiting (per reference config)
- `/ui/login` — 5 req/min per IP (aggressive, defeats brute-force)
- `/ui/*` — 100 req/min per IP (API threshold)
- Burst buffer: small (5–20) to prevent replay

### IP Allowlist (Optional)
```nginx
geo $ip_whitelist {
    default 0;
    10.0.0.0/8 1;       # VPN range
    203.0.113.0/24 1;   # Office IPs
}
if ($ip_whitelist = 0) { return 403; }
```

### Logging
- Log all requests to `access_log` (useful for forensics)
- Log errors to `error_log` (warn level or higher)
- Rotate logs to avoid disk fill (use `logrotate`)

---

## Audit Trail & Compliance

All operations logged to PostgreSQL before execution:

| Table | What | Who | When | Why |
|---|---|---|---|---|
| `audit_events` | Every action (patch, cleanup, etc.) | `batch_id:action_type` | before + after | full trail |
| `audit_events` | User management | `--user-add` caller (OS user) | login/modify | RBAC changes |
| `approval_requests` | Decision made | username + group | before execution | approval gate |
| `sessions` | Login | username | hashed cookie @ rest | auth trail |

**Queries example:**
```sql
-- All approvals made by user "alice" in last 7 days
SELECT batch_id, decided_at, status FROM approval_requests
  WHERE decided_by = 'alice' AND decided_at > NOW() - '7 days'::INTERVAL;

-- All users in admin group (current)
SELECT username FROM user_groups WHERE group_name = 'admin';

-- Full action history on a VM
SELECT action_type, status, completed_at FROM audit_events
  WHERE vm_id = 'prod-web-01' ORDER BY completed_at DESC;
```

---

## Incident Response

### Breached SSH Key
1. **Immediate** — disable targets (VPC security group, if applicable)
2. **Short-term** — rotate key: `ssh-keygen` new key, update inventory, redeploy agent
3. **Post-mortem** — grep audit logs for unauthorized actions; restore any changed configs

### Breached DB Credentials
1. **Immediate** — revoke old connection role, create new role + credentials
2. **Audit** — check `audit_events` for unauthorized decisions or user modifications
3. **Prevent** — rotate secrets, enforce strong password policy

### Compromised Web Session
1. **Immediate** — DELETE row from `sessions` table (invalidates cookie)
2. **Audit** — check `approval_requests` for decisions under that session
3. **Review** — operator checks recent approvals, rolls back any unauthorized changes

---

## Secrets Management

### v1 (Current — Environment Variables)
- `.env` files per process (`/home/errander-agent/.env.agent`, `/home/errander-web/.env.web`)
- Never committed to git (add to `.gitignore`)
- File permissions: mode 0600, read by systemd `EnvironmentFile=`
- Rotation: update file, `systemctl restart errander-agent errander-web`

### v2 Plan (HashiCorp Vault)
- Central secret store (not yet implemented)
- Per-process auth tokens
- Audit trail of secret access
- Automatic rotation support

---

## Testing & Verification

**Security tests in CI:**
```bash
# Import isolation: web process never imports executor/agent modules
pytest tests/web/test_import_isolation.py -v

# RBAC gates: readers cannot decide, admins can
pytest tests/observability/test_rbac.py::TestDecisionRBAC -v

# Exact-object approval: patch/hygiene handler correctly validates/filters
pytest tests/agent/test_docker_hygiene.py::TestDriftGates -v

# Session/auth: sessions expire, tokens hash, passwords scrypt
pytest tests/safety/test_user_store.py -v
```

---

## Threat Model

| Threat | Surface | Mitigation | Residual Risk |
|---|---|---|---|
| **LLM injection** (malicious target output) | Plan generator | Output is template-based fallback unless LLM available; executed plans reviewed by human | Human error |
| **Compromised SSH key** | Agent process | Key isolation via OS user; web process has no access | Insider with agent shell access |
| **Breached DB** | PostgreSQL (shared) | Both processes use separate roles (RBAC); hashed session tokens; scrypt password hashes | Insider with DB admin access |
| **Unauthorized approval** | Web UI | Server-side RBAC gate; session timeout (8h); TOTP in public mode | Session theft (mitigated by HTTPS + HttpOnly cookies) |
| **Replay attack** (approval) | Signed URLs (docker_hygiene) | HMAC-SHA256 signature; 10min expiry | Attacker with signing secret + time window |
| **Escalation** (reader → admin) | Web UI | Group membership in DB, server-side gate, no client-side permission assumption | Code/middleware bug in RBAC check |
| **DoS** (/ui/login) | Web process | Rate limiting (5 req/min per IP) in nginx | Distributed botnet can still disrupt |
| **Eavesdropping** (operator → web UI) | Network | TLS (nginx Mode 2); plaintext in private VPN (v1) | MITM in private network (operational responsibility) |

---

## See Also

- `docs/AI-ARCHITECTURE.md` — Layer A/B split, decision audit log
- `docs/OBSERVABILITY.md` — Prometheus + Grafana, LangSmith, ELK
- `deploy/nginx-mode2.conf.example` — Reference hardening config
- `CLAUDE.md` — Core architecture, code style
