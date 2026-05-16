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
| `http://localhost:9090/metrics` | Raw Prometheus metrics |
| `http://localhost:9090/health` | Liveness check — `{"status":"ok"}` |

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
1. Posts a dry-run report to the Slack `#errander-approvals` channel
2. Waits for a ✅ (approve) or ❌ (reject) reaction on that message
3. Also shows the pending approval in the web UI at `/ui/approvals`

### Approve or reject via Slack

React with ✅ to approve, ❌ to reject. The agent polls every 30 seconds.

### Approve or reject via Web UI

Go to `http://localhost:9090/ui/approvals` and click **Approve** or **Reject**.

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

---

## Monitoring with Prometheus + Grafana

The agent exposes metrics at `http://localhost:9090/metrics` in Prometheus format.

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
| `--check-llm` | Test vLLM connectivity and print latency |
| `--check-targets <env>` | Pre-flight SSH + sudo + OS readiness check for all VMs in an environment |
| `--probe-now <env>` | Run daily probe (disk, drift, failed logins, journal errors, ELK) — read-only |
| `--ask "<question>"` | LLM fleet analysis (Layer A — read-only, no changes) |
| `--audit --batches` | Print recent batch history |
| `--audit --batch-id <id>` | Print all events for a specific batch |

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
