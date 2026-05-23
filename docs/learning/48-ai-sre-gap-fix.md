# 48 — AI SRE Gap Fix: 7 Safety/Quality Fixes

## What Was Built and Why

Opus 4.7 reviewed the Errander-AI codebase and identified 8 findings across three priority tiers. P1-1 (docker_prune removal) was already resolved in v1.1. This session implemented the remaining 7 fixes in a single pass: P0-1, P1-2, P1-3, P1-4, P2-1, P2-2, P2-3.

The goal was closing specific safety and quality gaps that a senior reviewer flagged — not a rewrite, just targeted hardening in 7 independent areas.

---

## The 7 Fixes

### P0-1 — service_restart live mode has no approval gate

**Problem:** `run_restart_service()` in `main.py` entered the subgraph directly without checking for Slack config or requesting approval when `dry_run=False`.

**Fix:** Added a live-mode gate: load settings, assert Slack is configured, call `request_approval()`, poll with `poll_approval()`, then invoke the subgraph only on approval. Missing Slack config returns exit code 1 with a clear error.

**Test:** `tests/agent/test_service_restart_cli.py` — 6 tests covering no-Slack config, approval request creation, rejection, subgraph invocation, subgraph failure, and dry-run bypass.

---

### P1-2 — log_rotation calls `logrotate --force` on remote VM

**Problem:** `execute_node` in `log_rotation.py` called `logrotate --force /etc/logrotate.conf` on the remote VM — a single broad command that modifies system config, is not idempotent, and does not honor the per-file assessment already completed.

**Fix:** Replaced the broad logrotate call with per-file `cp+gzip+truncate` — matching the assessment that already identified oversized files. Each file is compressed individually; the loop is inherently idempotent.

---

### P1-3 — orphaned-deps is in DEFAULT_CLEANUP_PATHS (should require opt-in)

**Problem:** `DEFAULT_CLEANUP_PATHS` included `"orphaned_deps"`. Removing orphaned packages is potentially disruptive (autoremove can pull out packages an operator added intentionally) — it should only run when the operator explicitly enables it.

**Fix:** Moved `"orphaned_deps"` to `EXPLICIT_OPT_IN_PATHS`. It is still a valid cleanup action but only activates when listed in `whitelist_paths` in inventory config.

**Test:** `TestOrphanedDepsOptIn` class in `test_disk_cleanup.py` — 6 tests verifying it is absent from defaults and present in opt-in list.

---

### P1-4 — approval message doesn't distinguish [EXACT] vs [CATEGORICAL] coverage

**Problem:** `_format_plan_for_approval()` in `graph.py` posted a single Slack message for all actions without telling the operator which actions had exact-object approval (docker_hygiene) vs. categorical approval (patching) vs. advisory (disk_cleanup).

**Fix:** Added a per-action coverage table in the Slack message with three labels:
- `[EXACT]` — approval artifact references exact object IDs; execution re-validates each object
- `[CATEGORICAL]` — action category approved; scope bounded by assessment
- `[ADVISORY]` — read-only or whitelist-only; no destructive approval needed

The labels are derived from `action_manifest.approval_coverage` on each registered action.

---

### P2-1 — full plan is not inspectable post-approval

**Problem:** Once an operator approved a Slack message, the full list of packages/objects was not persisted anywhere. For batches with >10 packages, the Slack message was truncated and the full plan was lost after approval.

**Fix:** Three-part implementation:
1. **Migration 8** — added `plan_snapshots` table to audit DB (persists full plan JSON, keyed by plan_id)
2. **`AuditStore.save_plan_snapshot()`** — idempotent INSERT OR IGNORE, called before posting to Slack
3. **Inspection paths** — when web_base_url is set, a signed URL (`HMAC-SHA256`, 1-hour TTL) is appended to the Slack message; when not, a CLI hint (`errander --plan-show <plan_id>`) is included instead

**Test:** `tests/agent/test_plan_inspection_p21.py` — 9 tests covering the no-link case (≤10 packages), CLI hint when no web_base_url, signed URL when configured, signed URL tamper detection, expired token rejection, and `--plan-show` CLI output.

---

### P2-2 — backup_verify MANIFEST has no docstring

**Problem:** `MANIFEST` in `backup_verify.py` had no docstring explaining what backup verification does, its risk tier, or that it is read-only.

**Fix:** Added a concise module-level docstring on `MANIFEST` (the `ActionManifest` instance) explaining that backup_verify is read-only (LOW risk), checks recency and non-zero size, and never modifies files.

**Test:** `TestBackupVerifyManifest` in `test_backup_verify.py` — verifies `MANIFEST.risk_tier == RiskTier.LOW` and `MANIFEST.name == ActionType.BACKUP_VERIFY.value`.

---

### P2-3 — shell injection not defended at unit-name validation

**Problem:** systemd unit names in service_restart were validated at config load (schema.py) but not at execution time. An attacker who bypassed config loading (e.g., by directly invoking internal functions) could pass a unit name like `nginx.service; rm -rf /` and it would reach the SSH command.

**Fix:** Added `safe_systemd_unit_name(name: str) -> str` in `execution/commands.py`:
- Validates suffix is in `{.service, .socket, .target, .timer, .mount, .device, .scope, .slice}`
- Rejects names containing shell metacharacters, path separators, null bytes
- Raises `CommandBuildError` on any violation
- Called at three independent layers: config load (schema.py), snapshot_node, execute_node

**Test:** `TestSafeSystemdUnitName` in `test_command_builder.py` — valid names, missing suffix, unknown suffix, empty name, 7 shell injection payloads, path traversal, null byte. `TestAdversarialUnitNames` in `test_service_restart.py` — verifies snapshot_node makes no SSH call for bad unit names, and execute_node returns FAILED.

---

## Key Concepts

### Defense-in-depth for unit names

The validator runs at three independent layers: config load (catches bad names at deploy time), snapshot_node (before any SSH), execute_node (before the destructive SSH). Any single layer is sufficient to block an injection — having all three means a bug in one layer doesn't create a vulnerability.

```python
# Layer 1: schema.py config load
def _validate_unit_name(name: str) -> str:
    return safe_systemd_unit_name(name)  # raises ConfigError on failure

# Layer 2: snapshot_node
try:
    safe_systemd_unit_name(unit_name)
except CommandBuildError:
    return {}  # no SSH made — state machine backtracks

# Layer 3: execute_node
try:
    safe_systemd_unit_name(unit_name)
except CommandBuildError:
    return {"status": ActionStatus.FAILED.value, ...}
```

### Plan snapshot signed URLs

The signing infrastructure already existed in `integrations/signed_url.py` for docker_hygiene web approval URLs. P2-1 reuses the same `make_signed_token()` / `verify_signed_token()` pattern:

```python
token = make_signed_token({"plan_id": plan_id, "batch_id": batch_id}, ttl_seconds=3600)
url = f"{web_base_url}/plans/{plan_id}?token={token}"
```

The web handler verifies the token, checks plan_id matches payload, then returns the persisted JSON from `plan_snapshots`. This means the Slack message is immutable (no secrets inline) and the token is time-limited (1 hour).

### Per-action coverage labels

The `[EXACT]/[CATEGORICAL]/[ADVISORY]` table is generated from `action_manifest.approval_coverage` — a field on each `ActionManifest` that declares what kind of approval artifact the action produces. This is a declarative system: adding a new action requires setting `approval_coverage` to the right value, and the Slack message formatter picks it up automatically.

---

## Gotchas

**snapshot_node does not return `status=FAILED`.** It returns an empty dict (or partial state) on failure. Only `execute_node` sets `status`. Tests for bad unit names in snapshot_node should assert `executor.execute.assert_not_awaited()`, not `status == FAILED`.

**Base64 tamper testing is tricky.** Flipping the last character of a base64url string is unreliable because the last character may only encode padding bits — the decoded value doesn't change. To test tamper detection reliably, replace the entire signature component with known-wrong bytes:
```python
fake_sig = base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode()
tampered = f"{body_b64}.{fake_sig}"
```

**Migration version tracking.** The test `test_records_applied_versions` asserts the exact version list. After adding migration 8, the assertion must be `[0, 1, 2, 3, 4, 5, 6, 7, 8]` and `test_idempotent_on_second_run` must assert `count == 9`. Easy to forget when adding migrations.

**Orphaned deps opt-in is a behavioral change.** Existing inventories that relied on orphaned_deps running by default will stop seeing it. The right fix is for operators to add `orphaned_deps` to `whitelist_paths` in inventory config — but this is a breaking change, not a bug.

---

## Quiz

1. Why does snapshot_node return `{}` instead of `{"status": "failed"}` for a bad unit name?
2. What is the race window that Contract A's two-layer drift gate closes? Why isn't one check enough?
3. Why is `[EXACT]` approval superior to `[CATEGORICAL]` for safety?
4. What happens if `save_plan_snapshot()` fails? Why is logging a warning (not raising) the right choice there?
5. Why is `orphaned_deps` considered higher risk than `/tmp` cleanup, even though both remove data from the local VM?
