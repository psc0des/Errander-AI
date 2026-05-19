# 37 -- Immutable Signed Plan Artifact (P0-1)

## What was built and why

Before P0-1, the Slack approval message showed:

```
• prod-web-01: patching(exclude_patterns=['kernel*']), disk_cleanup
⚠️ You are approving action categories and parameters, not exact pinned commands or package versions.
```

The operator had no idea which packages would be upgraded. The hash committed to the action categories and params, not to actual package names and versions. This was an honest but weak HITL guarantee.

After P0-1:

```
prod-web-01:
  - patching: 3 package(s)
    nginx  1.18.0-0ubuntu1 -> 1.24.0-1ubuntu1
    openssl  1.1.1f-1ubuntu2.20 -> 1.1.1f-1ubuntu2.21
    curl  7.68.0-1ubuntu2.20 -> 7.81.0-1ubuntu1.10
  - disk_cleanup: 78% disk used, ~450MB apt cache

Hash a1b2c3d4e5f6abcd commits to the exact packages and actions listed above.
```

The operator approves exact packages. The hash proves the message hasn't been altered.

---

## Architecture: where enrichment slots in

The batch graph has a two-phase design:

**Phase 1 (plan):** `plan_vm` fan-out → `collect_plans` → **`enrich_plan` (new)** → `generate_plan_artifact` (hash) → `approval_gate`

**Phase 2 (execute):** `dispatch_wave` → `run_vm` fan-out

P0-1 inserts `enrich_plan_node` between plan collection and hash computation. The hash is computed **after** enrichment, so it now covers exact package data.

```
plan_vm (×N concurrent)
  ↓ [vm_plans reduced into BatchGraphState]
collect_plans_node    (passthrough, logs count)
  ↓
enrich_plan_node      ← NEW: SSH read per VM, adds preview to planned_actions
  ↓
generate_plan_artifact_node  (SHA-256 over full vm_plans including preview)
  ↓
approval_gate_node    (Slack message shows exact packages)
```

The existing `verify_plan_hash_node` required no changes — it already hashes the full `vm_plans` structure, which now includes `preview`.

---

## Key functions

### `enrich_plan_node(state, *, ssh_manager)` in `graph.py`

Entry point. Runs `_enrich_vm_plan` for each VM concurrently via `asyncio.gather`.

### `_enrich_vm_plan(plan, target_by_id, ssh_manager)`

For one VM:
1. Looks up connection params from `target_by_id` (matches vm_id → hostname/ssh_user/ssh_key_path)
2. Iterates planned actions
3. For `patching`: calls `_preview_patching()`
4. For `disk_cleanup`: calls `_preview_disk_cleanup()`
5. For other actions: `preview = {}` (no SSH)
6. Appends `preview` to each action dict

### `_preview_patching(vm_id, hostname, username, key_path, os_family, ssh_manager)`

Runs `apt list --upgradable` (or `dnf check-update` for RHEL) via SSH. Parses with `_parse_upgradable_with_versions` to get `{name, current, target}` per package. Applies `MANDATORY_KERNEL_EXCLUDES` filter — same exclusions as `assess_node` in the execution phase.

### `_parse_upgradable_with_versions(output, os_family)` in `patching.py`

Extends the existing `_parse_upgradable` to also capture current and target versions.

apt output format:
```
nginx/focal-updates 1.24.0-1ubuntu1 amd64 [upgradable from: 1.18.0-0ubuntu1]
```
Regex: `^(\S+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from:\s+(\S+)\]` → (name, target, current)

DNF output: `nginx.x86_64  1:1.24.0-1.el9  @appstream` → (name, target) — no current version in DNF output.

### `_format_plan_for_approval()` in `graph.py`

Updated to render:
- `patching` with packages: exact `name  current -> target` list (capped at 10)
- `patching` with error in preview: `(preview unavailable: ...)` fallback
- `patching` with empty preview: falls back to showing `params` (pre-P0-1 behavior for dry-run mode)
- `disk_cleanup`: disk usage % and apt cache size
- Other actions: same as before (type + params)

---

## Why `verify_plan_hash_node` needed no changes

`generate_plan_artifact_node` hashes:
```python
canonical = json.dumps(
    {"batch_id": batch_id, "env_name": env_name, "vm_plans": vm_plans},
    sort_keys=True,
    default=str,
)
```

`vm_plans` now contains `preview` dicts in every `planned_actions` entry. The JSON serialization covers them automatically. The hash changes if the packages change — exactly what P0-1 requires.

`verify_plan_hash_node` re-computes this same formula at execution time. Since `vm_plans` was approved, and the same `vm_plans` is in `BatchGraphState`, the re-computation matches.

---

## Best-effort design

SSH failure in `enrich_plan_node` → `preview = {"error": "..."}` → batch continues. The hash then commits to the error state. The Slack message shows "(preview unavailable: ...)". The operator can still approve, knowing that exact package data was unavailable at plan time.

This is correct: a failure to get the preview is not a reason to abort the entire batch. But the operator can see it in the message and decide.

---

## Load test regression (lessons learned)

`test_wave_abort_stops_fleet_at_boundary` had a hardcoded SSH call count: "12 validate + 60 plan_vm + 3 wave-0 health = 75 succeed, wave-1 health fails."

`enrich_plan_node` adds 2 SSH calls per VM for `disk_cleanup` preview (12 VMs × 2 = 24 calls). Updated threshold: `call_count <= 99` (12 + 60 + 24 + 3 = 99).

**Rule**: whenever a new phase adds SSH calls to the planning flow, grep for hardcoded call-count thresholds in load tests and update them. Comment the breakdown explicitly: `# 12 validate + 60 plan_vm + 24 enrich_plan + 3 wave-0 health = 99`.

---

## What P0-1 now guarantees (post-fix)

The QA/SRE review identified that the original P0-1 implementation was "pre-approval plan enrichment with hash commitment" — not a true immutable execution artifact. Three fixes were applied:

**Fix 1 — Pinned execution (P0)**: `execute_node` in `patching.py` now uses `install_pinned()` instead of `upgrade_all()` when `approved_packages` is present in state. `commands.py` gained `install_pinned(packages: list[tuple[str, str]])` and `simulate_install_pinned()` for both `AptManager` and `DnfManager`. In live mode, execution generates `apt-get install -y pkg=version ...` — the exact versions the operator approved. Missing or empty versions fail closed. Dry-run simulates the pinned install (`apt-get install --simulate pkg=version ...`).

**Fix 2 — Approved packages wired through dispatch (P0)**: `_run_patching()` in `vm_graph.py` now extracts `approved_packages` from `planned_actions[current_action_index]["preview"]["packages"]` and injects them into `PatchingGraphState`. The enriched plan data was always present in the dispatch state — it was just never forwarded into the sub-graph.

**Fix 3 — Deferred replay age check (P0/P1)**: `load_deferred_artifact_node` in `graph.py` now checks the artifact age using `preloaded_approved_at` (passed from `record.approved_at` in `main.py`). Artifacts exceeding `_DEFERRED_MAX_ARTIFACT_AGE_HOURS` (168h = 7 days) fail closed. Artifacts older than 24h log a warning. Package drift at replay time is handled by pinned execution failing closed if the approved version is unavailable.

## What P0-1 still does NOT include

- Rollback plan in the artifact — the rollback snapshot is taken at execution time (`snapshot_node`). Including it would require running `dpkg -l` at plan time.
- `autonomous_live_apply_enabled = True` — P0-1/P0-2 don't change this flag. Enabling autonomous mode is a separate conscious decision.

---

## Quiz

1. Where in the batch graph does `enrich_plan_node` run, and why does this timing matter?
2. Why does `verify_plan_hash_node` require no changes after P0-1?
3. What happens when `enrich_plan_node` SSH fails for one VM? Does the batch abort?
4. Why does `_preview_patching` apply `MANDATORY_KERNEL_EXCLUDES`? What would happen if it didn't?
5. DNF `check-update` output doesn't include the current version. How does P0-1 handle this?
6. Why does `_format_plan_for_approval` fall back to showing `params` when preview is empty (not when preview has an error)?

## Fix 4 — Second SRE pass: assess bypass, exact verify, approved_at fails-closed (P0/P1)

Three gaps found by the second SRE review pass (all in `patching.py` / `graph.py`):

**assess bypass**: With `approved_packages`, `assess_node` was still calling `list_upgradable()` (which queries the live package repo). Fresh repo state could silently override the approved artifact. Fixed: approved-artifact path now calls `list_installed_versions()` for the approved package names, compares installed vs. approved targets, and sets `nothing_to_do` without touching the repo. The approved artifact is the sole source of truth.

**exact verify**: `verify_node` was checking "did anything change" (snapshot diff), not "did each package land at its approved version". Fixed: with `approved_packages`, verify now asserts `installed == approved_target` for every approved package. Any mismatch or absent package → `FAILED` with the specific discrepancy.

**approved_at fails-closed**: `load_deferred_artifact_node` was silently skipping the age check when `preloaded_approved_at` was empty/None. Fixed: missing, None, or unparseable `preloaded_approved_at` → fail closed immediately. The age check is mandatory and cannot be bypassed by absent data.

## Fix 5 — Third SRE pass: verify_node partial-update query scope (P1)

**The bug**: `verify_node` queried `list_installed_versions(pending_updates)` but compared results against all `approved_packages`. In a partial-update scenario — some approved packages already at their target version (assess finds nothing to do for them), only the rest in `pending_updates` — the already-at-target packages were never in `pending_updates`, so they never appeared in the dpkg query output. `verify_node` saw them as "missing" and failed the verification.

**Example**: approved = `[nginx=1.24, curl=7.88]`. Assess finds nginx already at 1.24 (nothing to do); only curl goes into `pending_updates`. Execute installs curl. Verify queries dpkg for just `["curl"]`. nginx not in output → "expected 1.24 — not found in dpkg output" → **false fail**.

**Fix**: when `approved_packages` is present, query `list_installed_versions(all_approved_names)`. Both packages appear in dpkg output; both verify correctly.

**Rule**: query scope must equal comparison scope. If you compare against N items, query for N items.
