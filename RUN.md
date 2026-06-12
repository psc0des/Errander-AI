# Errander-AI — Operations Guide

How to run, monitor, approve, and manage Errander-AI in day-to-day operations.

> **Prerequisites:** You have completed SETUP.md and have a working inventory + `.env` file.

---

## Loading environment variables

Every command that touches the agent needs the env vars from `.env` loaded first.

**Windows PowerShell:**
```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
```

**Linux / Git Bash:**
```bash
export $(grep -v '^#' .env | xargs)
```

All examples below assume env vars are loaded.

---

## Running batches

### Dry-run (safe — nothing changes on VMs)

```bash
uv run python -m errander --run-now --env dev --inventory inventory.yaml --dry-run
```

Dry-run is the default mode. It:
- SSH-connects to each VM and reads state (disk, packages, etc.)
- Plans what it would do
- Prints `[DRY-RUN] Would execute: <command>` for every action
- Writes the plan to the audit DB and web UI
- Sends a Slack approval request if approval is required

Use dry-run to preview before committing to a live run.

### Live run (real commands on VMs)

```bash
uv run python -m errander --run-now --env dev --inventory inventory.yaml --live
```

### Run outside maintenance window

The agent enforces maintenance windows. To override:

```bash
uv run python -m errander --run-now --env dev --inventory inventory.yaml --dry-run \
  --force --force-reason "emergency disk cleanup before Monday deploy"
```

### Run specific environment

```bash
# Run against production (uses production window + approval policy)
uv run python -m errander --run-now --env production --inventory inventory.yaml --dry-run

# Run against staging
uv run python -m errander --run-now --env staging --inventory inventory.yaml --dry-run
```

### Scheduled runs (continuous mode)

Start the agent as a long-lived process — it schedules its own runs based on `settings.yaml`:

```bash
uv run python -m errander --inventory inventory.yaml --config settings.yaml
```

The agent runs until killed (Ctrl+C or service stop). Cron triggers fire automatically per the schedule.

---

## Web UI

The agent serves a web UI on port 9090 while running.

| URL | What it shows |
|---|---|
| `http://localhost:9090/ui` | Dashboard — live stats, recent batches |
| `http://localhost:9090/ui/batches` | Full batch history |
| `http://localhost:9090/ui/batches/<batch-id>` | All events for a specific batch |
| `http://localhost:9090/ui/approvals` | Pending approvals + decision history |
| `http://localhost:9090/ui/ai-decisions` | AI decision audit log — every LLM call that influenced a plan |
| `http://localhost:9090/ui/ai-decisions/<id>` | Full detail for one LLM decision: prompt, response, context snapshot |
| `http://localhost:9090/metrics` | Raw Prometheus metrics |
| `http://localhost:9090/health` | Liveness check — `{"status":"ok"}` |

### Data mode: fixture vs live

The Operations Hub can run in two modes, controlled by `ERRANDER_UI_DATA_MODE`:

| Value | Behaviour |
|---|---|
| `fixture` (default) | Static demo data from `errander/web/data.py`. Safe for demos and CI. |
| `live` | Reads from real backend stores (AuditStore, ApprovalRequestStore, inventory). Missing stores show "unavailable" — never silently fall back to fake data. |

```bash
# Demo / CI (default — no env var needed)
uv run python -m errander

# Live data mode
ERRANDER_UI_DATA_MODE=live uv run python -m errander
```

The mode banner at the top of every page shows the current mode and data freshness.

### Local dev / exploration (no real VMs needed)

To browse the UI with realistic seeded data and a headed browser:

```bash
uv run python scripts/browse_ui.py
```

To start the server only (open browser yourself):

```bash
uv run python scripts/dev_ui.py
# Then open http://localhost:9092/ui
```

---

## Approvals

When a batch requires human approval (high-risk actions, or `strict` policy), the agent:
1. Persists the pending approval to the database (durable — survives restarts)
2. Posts the plan summary + a web approval link to the Slack `#errander-approvals` channel (notification only)
3. Waits for a decision recorded in the web UI at `/ui/approvals`

### Approve or reject (Web UI — the only decision surface)

Go to `http://localhost:9090/ui/approvals`, sign in with an `admin`-group
account, and click **Approve Selected** or **Reject All**. The decision is
recorded with your username and group (`decided_by` / `decided_by_group`).
Slack cannot approve or reject — the message only notifies and links.

### Web UI users (RBAC)

```bash
uv run python -m errander --user-add <name> --user-groups admin   # can decide
uv run python -m errander --user-add <name> --user-groups reader  # view-only
uv run python -m errander --user-list
uv run python -m errander --user-set-groups <name> --user-groups reader
uv run python -m errander --user-set-password <name>
uv run python -m errander --user-remove <name>
```

Password comes from `ERRANDER_USER_PASSWORD` or an interactive prompt. Group
changes apply on the user's next request; all changes are audit-logged.

### Approval timeout

If no decision is made within the timeout (default 30 minutes), the batch is **auto-rejected**. Change the timeout in `settings.yaml`:

```yaml
agent:
  approval_timeout_seconds: 3600  # 1 hour
```

---

## Audit trail

### View recent batches

```bash
uv run python -m errander --audit --batches
```

Example output:
```
batch-id              env         started              status
--------------------  ----------  -------------------  ----------
batch-2026-04-18-001  production  2026-04-18 02:00:00  completed
batch-2026-04-17-001  staging     2026-04-17 22:00:00  completed
batch-2026-04-15-001  production  2026-04-15 02:01:00  failed
```

### View all events for a batch

```bash
uv run python -m errander --audit --batch-id batch-2026-04-18-001
```

Example output:
```
timestamp            event             vm_id         action       detail
-------------------  ----------------  ------------  -----------  -------------------
2026-04-18 02:00:00  BATCH_STARTED     —             —            Batch started
2026-04-18 02:00:05  ACTION_STARTED    prod/web-01   disk_cleanup Starting disk cleanup
2026-04-18 02:00:47  ACTION_COMPLETED  prod/web-01   disk_cleanup Freed 1.2 GB
2026-04-18 02:01:03  ACTION_STARTED    prod/db-01    disk_cleanup Starting disk cleanup
2026-04-18 02:03:21  ACTION_COMPLETED  prod/db-01    disk_cleanup Freed 3.4 GB
2026-04-18 02:03:22  BATCH_COMPLETED   —             —            2 success
```

---

## Durability measurement

View completion rates, duration percentiles, and interrupted-batch count for the last N days:

```bash
uv run python -m errander --measure-durability
# (default window: 14 days)

uv run python -m errander --measure-durability --window-days 30
```

Example output:
```
Errander durability snapshot  window: last 14 days
  Batches:        total=47   completed=45   interrupted=2   completion_rate=95.7%
  Batch duration (BATCH_STARTED -> BATCH_COMPLETED):
    p50=184.3s   p95=421.0s   max=603.2s   sample=45
  Approval wait (APPROVAL_REQUESTED -> first of GRANTED/REJECTED/TIMEOUT):
    p50=42.1s   p95=310.0s   max=1800.0s   sample=47
    auto-rejected=2   granted=43   rejected=2
  Longest actions (ACTION_STARTED -> ACTION_COMPLETED/FAILED):
    patching             p95=340.2s   max=601.4s   sample=38
    disk_cleanup         p95=62.4s    max=120.1s   sample=47
  Agent restarts during a live batch: 2
```

The "Agent restarts during a live batch" count equals interrupted batches — a non-zero value means the agent process died mid-batch. This is also reported via `BATCHES_INTERRUPTED_TOTAL` in Prometheus at startup.

---

## VM operational facts (B3)

Spot-check the historical outcome facts that OperatorAssistant surfaces to the LLM.

```bash
# All action outcomes for one VM
uv run python -m errander --vm-facts prod/web-01

# Filter to one action type
uv run python -m errander --vm-facts prod/web-01 --vm-facts-action patching

# Cross-fleet: all VMs for one action type (omit vm_id)
uv run python -m errander --vm-facts-action patching
```

Example output:
```
Action outcomes — prod/web-01
------------------------------------------------------------------------
  VM            ACTION        RATE  SAMPLE  LAST SUCCESS       LAST FAILURE
  --------------------------------------------------------------------------
  prod/web-01   disk_cleanup   ✓ 100%      12  2026-05-18 02:14   —
  prod/web-01   patching       ~ 75%        8  2026-05-16 02:11   dpkg lock

Reboot pattern — prod/web-01
------------------------------------------------------------------------
  prod/web-01: 3 / 8 patching runs required a reboot

Approval rejections — last 90 days (fleet-wide)
------------------------------------------------------------------------
  ACTION    REJECTIONS (90d)  REASONS
  ------------------------------------
  patching                 2  risk too high; maintenance window missed
```

---

## Health checks

### Check the agent is running

```bash
curl http://localhost:9090/health
# {"status":"ok"}
```

### Check vLLM connectivity

```bash
uv run python -m errander --check-llm
# Status  : OK
# Models  : qwen3-8b
# Latency : ~1100 ms
```

### Pre-flight VM readiness check

Verify SSH access, sudo privilege, and OS detection for every VM in an environment before running a batch:

```bash
uv run python -m errander --check-targets dev
uv run python -m errander --check-targets production
```

Prints a readiness table (hostname, SSH status, sudo status, OS detected). Fix any failures before running live batches.

Also checks allowlist drift for any environment with `service_restart.enabled: true` — prints `ALLOWLIST DRIFT` lines if inventory `restartable_units` and `/etc/errander/restart-allowlist` disagree.

---

## Inventory migration

If you have an existing inventory using the old flat `docker_command_mode: wrapper` format, migrate it to the new nested `actions:` block:

```bash
uv run python -m errander --migrate-inventory inventory.yaml
```

This writes `inventory.yaml.migrated` alongside the original. Review it, then apply:

```bash
diff inventory.yaml inventory.yaml.migrated
mv inventory.yaml inventory.yaml.bak && mv inventory.yaml.migrated inventory.yaml
```

---

## Service restart (operator-triggered)

Service restart is operator-initiated only — the agent never decides to restart a service autonomously.
Prerequisites: wrapper installed on each target VM, `restartable_units` configured in inventory. See `SETUP.md#optional-service-restart`.

```bash
# Dry-run (default) — prints plan, no SSH
uv run python -m errander --restart-service production --unit nginx.service --vm prod-web-01

# Target multiple VMs
uv run python -m errander --restart-service production --unit nginx.service \
  --vms prod-web-01,prod-web-02

# Live execution — routes to Slack for approval before SSH
uv run python -m errander --restart-service production --unit nginx.service \
  --vm prod-web-01 --live

# Verify allowlist sync before triggering
uv run python -m errander --check-targets production
```

---

## Monitoring with Prometheus + Grafana *(optional — dedicated external VM only)*

The agent exposes metrics at `http://localhost:9090/metrics` in Prometheus format. The built-in `/ui/monitoring` page is the primary view — no external stack needed on the agent VM.

> **External VM only:** run `bash scripts/install-prometheus.sh` on a separate monitoring VM and point it at `<agent-vm-ip>:9090`. Running Prometheus on the same server as the agent adds resource pressure with no observability gain over the built-in page.

### Scrape config (`prometheus.yml`)

```yaml
scrape_configs:
  - job_name: errander
    static_configs:
      - targets: ['<controller-ip>:9090']
```

### Key metrics

| Metric | Description |
|---|---|
| `errander_batches_total` | Total batches run, labelled by `env` and `status` |
| `errander_actions_total` | Total actions, labelled by `action_type` and `status` |
| `errander_action_duration_seconds` | Action duration histogram |
| `errander_pending_approvals` | Number of approvals currently waiting |
| `errander_llm_calls_total` | LLM calls, labelled by `mode` and `status` |

---

## Logs

The agent writes structured JSON logs to stdout. Capture them with your preferred log shipper or redirect to a file.

**Linux — view live logs (systemd):**
```bash
journalctl -u errander -f
```

**Linux — redirect to file:**
```bash
uv run python -m errander --inventory inventory.yaml 2>&1 | tee errander.log
```

**Windows — redirect to file (PowerShell):**
```powershell
uv run python -m errander --inventory inventory.yaml 2>&1 | Tee-Object -FilePath errander.log
```

---

## Stopping the agent

**Graceful shutdown** (waits up to 120 seconds for in-flight actions to finish):

- Linux: `systemctl stop errander` or `Ctrl+C` in the terminal
- Windows: `Ctrl+C` in the terminal, or stop the Task Scheduler task

The agent will not interrupt a running SSH command mid-flight — it finishes the current VM before stopping.

---

## Common CLI flags

| Flag | Description |
|---|---|
| `--run-now` | Trigger a batch immediately instead of waiting for the schedule |
| `--env <name>` | Environment to run against (must match a key in inventory.yaml) |
| `--inventory <path>` | Path to inventory YAML (default: `inventory.yaml`) |
| `--config <path>` | Path to settings YAML (default: `settings.yaml`) |
| `--dry-run` | Simulate actions — no changes on VMs (default) |
| `--live` | Execute real commands on VMs |
| `--force` | Bypass maintenance window check |
| `--force-reason <text>` | Required with `--force` — logged to audit trail |
| `--log-level <level>` | Log verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO) |
| `--check-inventory` | Validate inventory.yaml and print a target summary, then exit |
| `--check-llm` | Test LLM endpoint connectivity and print latency |
| `--check-targets <env>` | Pre-flight SSH + sudo + OS readiness check for all VMs in an environment |
| `--bootstrap-known-hosts <env>` | Connect once to every VM in ENV and pin SSH host keys, then exit |
| `--probe-now <env>` | Run daily probe (disk, drift, failed logins, journal errors, ELK) — read-only |
| `--ask "<question>"` | LLM fleet analysis (Layer A — read-only, no changes) |
| `--migrate-inventory <path>` | Convert a legacy flat inventory to the nested `actions:` format; writes `<path>.migrated` |
| `--restart-service <env>` | Trigger an operator-initiated service restart (HIGH risk, Slack approval required) |
| `--unit <name>` | Systemd unit name to restart (required with `--restart-service`) |
| `--vm <vm-id>` | Single VM target by name (use with `--restart-service`) |
| `--vms <id1,id2>` | Comma-separated VM names (use with `--restart-service`) |
| `--generate-secrets-key` | Generate a new `ERRANDER_SECRETS_KEY` and print it, then exit |
| `--encrypt <value>` | Encrypt a value with `ERRANDER_SECRETS_KEY` and print the `enc:v1:` blob, then exit |
| `--audit --batches` | Print recent batch history |
| `--audit --batch-id <id>` | Print all events for a specific batch |
| `--audit --vm-id <id>` | Filter audit events by VM ID |
| `--audit --action-type <type>` | Filter audit events by action type (e.g. `disk_cleanup`) |
| `--audit --event-type <type>` | Filter audit events by event type (e.g. `action_started`) |
| `--audit --last <n>` | Maximum audit events to return (default: 50) |
| `--measure-durability` | Print durability snapshot (completion rate, duration/approval percentiles) |
| `--window-days <n>` | Look-back window for `--measure-durability` in days (default: 14) |
| `--vm-facts <vm_id>` | Print action outcome, reboot pattern, and rejection facts for a VM |
| `--vm-facts-action <type>` | Filter `--vm-facts` to one action type, or use alone for cross-fleet view |
| `--plan-show <plan-id>` | Print the full package/object list for a saved plan snapshot (use when Slack message is truncated) |
| `--ai-decisions` | Query the AI decision audit log and exit |
| `--ai-decision-show <id>` | Show full detail for a single AI decision by numeric ID, then exit |
| `--decision-type <type>` | Filter `--ai-decisions` by decision type (e.g. `prioritize_actions`) |
| `--ai-eval-replay` | Replay stored LLM decisions against a candidate model and print pass/fail/error summary |
| `--eval-model <id>` | Model ID to use as the candidate in `--ai-eval-replay` (default: current `ERRANDER_LLM_MODEL`) |

---

## Runbook — common scenarios

### "Disk is critically full on a VM right now"

```bash
# 1. Dry-run first to see what would be cleaned
uv run python -m errander --run-now --env production --inventory inventory.yaml \
  --dry-run --force --force-reason "emergency: disk critical on prod/web-01"

# 2. Review the dry-run output, then run live
uv run python -m errander --run-now --env production --inventory inventory.yaml \
  --live --force --force-reason "emergency: disk critical on prod/web-01"
```

### "The last batch failed — what happened?"

```bash
# Find the batch ID
uv run python -m errander --audit --batches

# Drill into the failed batch
uv run python -m errander --audit --batch-id <batch-id>

# Or open the web UI
# http://localhost:9090/ui/batches/<batch-id>
```

### "A pending approval has been sitting there too long"

Go to `http://localhost:9090/ui/approvals` and click **Reject** to cancel it. Or react with ❌ on the Slack message.

### "I want to disable scheduled runs temporarily"

Comment out the schedule in `settings.yaml`:
```yaml
schedules:
  production:
    maintenance: null   # disabled
```

Reload the agent (restart the process) for the change to take effect.

### "I deployed a config change and need to restart a service"

```bash
# 1. Dry-run first — confirms the unit is in the allowlist and the VM is reachable
uv run python -m errander --restart-service production --unit nginx.service --vm prod-web-01

# 2. Run live — notifies #errander-approvals and waits for your web UI decision
uv run python -m errander --restart-service production --unit nginx.service \
  --vm prod-web-01 --live
```

### "I want to add a new VM"

Add it to `inventory.yaml` under the appropriate environment:
```yaml
targets:
  - host: 10.0.1.15
    name: prod-web-03
    os_family: ubuntu
```

Then run a dry-run to confirm SSH connectivity works:
```bash
uv run python -m errander --run-now --env production --inventory inventory.yaml --dry-run
```

No restart needed — the inventory is read at batch start time.
