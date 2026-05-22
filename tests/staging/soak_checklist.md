# Errander-AI — Staging Soak Checklist (Phase 4.1)

Run against three disposable VMs (Ubuntu 22.04, Debian 12, RHEL 9) before any
production deployment. Destroy VMs after each run.

## Infrastructure

| VM | OS | Role |
|----|----|------|
| staging-ubuntu | Ubuntu 22.04 LTS | Primary target |
| staging-debian | Debian 12 | Secondary target |
| staging-rhel   | RHEL 9 / Rocky 9 | Third target |

Provision with:
```bash
# Example — adapt for your provider
az vm create --name staging-ubuntu --image Ubuntu2204 --size Standard_B2s ...
```

Add to `inventory.yaml` under a `staging-soak` environment with `approval_policy: strict`.

---

## Step 1 — OS verification (finding #8)

```bash
uv run python -m errander --run-now --env staging-soak --dry-run --force --force-reason "soak step 1"
```

**Pass criteria:**
- [ ] All 3 VMs appear in `healthy_targets` (not `failed_targets`)
- [ ] Audit events include detected OS family matching inventory declaration
- [ ] No `OS_MISMATCH` events in audit trail

Verify:
```bash
uv run python -m errander --audit --batch-id <batch_id>
```

---

## Step 2 — Dry-run plan generation

```bash
uv run python -m errander --run-now --env staging-soak --dry-run
```

**Pass criteria:**
- [ ] `plan_hash` appears in logs
- [ ] All action types are from the allow-list (disk_cleanup, log_rotation, docker_hygiene, patching, backup_verify)
- [ ] No kernel packages appear in planned patching actions
- [ ] `ai_decisions` table populated (if LLM configured)
- [ ] All actions show `DRY_RUN_OK` status

---

## Step 3 — Approval gate (strict policy)

With `approval_policy: strict`, the plan requires Slack approval for MEDIUM+ risk tiers.

```bash
uv run python -m errander --run-now --env staging-soak --dry-run
```

**Pass criteria:**
- [ ] Slack message posted with plan hash
- [ ] Approving with ✅ grants approval and logs `APPROVAL_GRANTED` event
- [ ] Rejecting with ❌ routes to `generate_report` with no execution
- [ ] Timeout (wait past `approval_timeout_seconds`) auto-rejects

---

## Step 4 — Live run (one action type at a time)

Live mode is now fully unblocked (`--unsafe-legacy-live` guard removed in re-audit Blocker 3):

```bash
# Start with lowest-risk action only
uv run python -m errander --run-now --env staging-soak --live
```

**Pass criteria per action type:**

### disk_cleanup
- [ ] `/tmp` files older than threshold deleted
- [ ] apt/dnf cache cleaned
- [ ] `journal` vacuumed
- [ ] `df -h` shows reduced usage after (verify node passes)
- [ ] Audit trail has `ACTION_COMPLETED` for each VM

### log_rotation
- [ ] Oversized files in `/var/log` rotated/compressed
- [ ] No files outside `/var/log` touched
- [ ] Idempotent: second run shows `nothing_to_do`

### docker_hygiene
- [ ] Assessment posted to Slack with exact image IDs and container names for approval
- [ ] Only operator-approved objects removed (exact-object approval)
- [ ] Running containers unaffected
- [ ] Per-object audit entries recorded (one row per removed object)

### patching
- [ ] No kernel packages in `pending_updates` list
- [ ] `version_snapshot` captured before upgrade
- [ ] Post-upgrade `updated_versions` verified against snapshot
- [ ] Rollback tested: stop dpkg mid-flight → verify rollback runs and restores

### backup_verify
- [ ] All configured backup paths checked for existence, size, recency
- [ ] `NEEDS_MANUAL` status for any stale/missing backups
- [ ] Read-only: no files written or deleted

---

## Step 5 — Fleet threshold abort (finding #7)

Shut down `staging-rhel` mid-test:

```bash
az vm deallocate --name staging-rhel  # or equivalent
uv run python -m errander --run-now --env staging-soak --dry-run --force --force-reason "threshold test"
```

**Pass criteria:**
- [ ] `FLEET_ABORT` event if failure rate exceeds `fleet_failure_threshold`
- [ ] No actions run on remaining healthy VMs after abort
- [ ] Report generated with abort reason

---

## Step 6 — SSH host key verification (finding #9)

Bootstrap known_hosts first:

```bash
ERRANDER_SSH_KNOWN_HOSTS=~/.ssh/errander_staging_known_hosts \
  uv run python -m errander --bootstrap-known-hosts staging-soak
```

Then run with strict host keys:

```bash
ERRANDER_SSH_KNOWN_HOSTS=~/.ssh/errander_staging_known_hosts \
ERRANDER_SSH_STRICT_HOST_KEYS=true \
  uv run python -m errander --run-now --env staging-soak --dry-run --force
```

**Pass criteria:**
- [ ] Connects successfully to all known hosts
- [ ] Replacing a host's key → connection refused with clear error
- [ ] TOFU mode (ERRANDER_SSH_STRICT_HOST_KEYS=false) logs security warning per connection

---

## Step 7 — Chaos: audit DB locked

```bash
# Lock the SQLite file externally, then run
flock errander.sqlite uv run python -m errander --run-now --env staging-soak --live &
sleep 2 && kill %1  # release lock after 2s
```

**Pass criteria (strict mode):**
- [ ] With DB locked for first 2s, live action aborts with `AuditWriteError`
- [ ] No partial state left on target VM

---

## Step 8 — AI decision audit (finding #3.4)

After any run with LLM configured:

```bash
sqlite3 errander.sqlite "SELECT decision_type, model, outcome, latency_ms FROM ai_decisions LIMIT 10;"
```

**Pass criteria:**
- [ ] `prioritize_actions` entries for each VM
- [ ] `outcome` is `success`, `fallback`, or `no_llm`
- [ ] `prompt_hash` is populated (non-empty 16-char hex)
- [ ] `latency_ms` recorded for successful calls

---

## Cleanup

```bash
az vm delete --name staging-ubuntu staging-debian staging-rhel --yes
rm errander.sqlite errander_staging.sqlite ~/.ssh/errander_staging_known_hosts
```

---

## Sign-off

Before marking Phase 4 complete:

- [ ] All steps above pass on fresh VMs
- [ ] Zero uncaught exceptions in agent logs
- [ ] `uv run pytest` clean on the same machine (867+ passing)
- [ ] Git working tree clean
