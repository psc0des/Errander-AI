# 49 — SRE Residual Fixes (2026-05-23)

## What Was Built and Why

After the previous session fixed 7 SRE gap-analysis findings, Opus 4.7 performed a second validation pass and issued a "not signed off" verdict with 5 residual issues. This session resolves all 5 before deployment testing.

---

## Finding 1 (P1) — Docker wrapper image ID format mismatch

### The problem

`errander-docker-assess-v2` (the assess wrapper in `scripts/install-docker-wrappers-v2.sh`) called:

```bash
docker images --filter dangling=true --format '{{.ID}}|{{.CreatedAt}}|{{.Size}}'
```

`{{.ID}}` in Docker Go templates emits the **12-character short ID** (e.g., `sha256:abc123def456`). The remove wrapper's revalidation loop uses:

```bash
docker images --filter dangling=true -q --no-trunc | grep -Fx "$obj_id"
```

`grep -Fx` requires an **exact line match**. A 12-char short ID will never match a 64-char full SHA256. The result: every dangling image was classified as `drift_skipped reason=image_re_tagged` — the drift gate always tripped, no images were ever removed.

### The fix

Add `--no-trunc` to both `docker images` calls in the assess wrapper:

```bash
# Before:
docker images --filter dangling=true --format '{{.ID}}|...'
# After:
docker images --filter dangling=true --no-trunc --format '{{.ID}}|...'
```

`--no-trunc` forces the full `sha256:<64 hex chars>` output, matching what `grep -Fx` expects.

### Tests added (`TestWrapperIdFormat`)

```python
def test_full_id_matches_remove_wrapper_grep_pattern(self) -> None:
    import re
    full_id_pattern = re.compile(r"^sha256:[a-f0-9]{64}$")
    assert not full_id_pattern.match("sha256:abc123")          # short ID fails
    assert full_id_pattern.match("sha256:" + "a" * 64)         # full ID passes
```

**Key lesson:** `{{.ID}}` in docker format strings ≠ `--no-trunc`. Always use `--no-trunc` when the ID will be used for exact string matching downstream.

---

## Finding 2 (P1) — `service_restart` bypassed VM lock and maintenance window

### The problem

`run_restart_service()` in `errander/main.py` had two safety gaps:
1. No call to `FileLocker.acquire()` — two concurrent restarts on the same VM could race.
2. No call to `check_window_from_config()` — restarts executed at any hour, including outside declared maintenance windows.

Both controls existed in the batch graph but were not wired into the operator-triggered restart path.

### The fix

**Maintenance window check** (added after settings load):

```python
window = _build_maintenance_window(env)
if window is not None:
    now = datetime.now(tz=UTC)
    if not check_window_from_config(now, window):
        if force:
            if not force_reason:
                print("Error: --restart-force requires --restart-force-reason <reason>")
                return 1
            logger.warning("Maintenance window bypassed: %s", force_reason)
        else:
            next_open = next_window_open(now, window)
            print(f"Error: outside maintenance window. Next window: {next_open}. Use --restart-force --restart-force-reason to override.")
            return 1
```

**VM locking** (added inside the per-VM loop):

```python
locker = FileLocker(lock_dir=Path(".errander-locks"))
acquired = await locker.acquire(vm_id, batch_id, ttl_seconds=300)
if not acquired:
    print(f"  [{vm_id}] SKIPPED — VM is locked by another maintenance batch")
    overall_success = False
    continue
try:
    final = await subgraph_compiled.ainvoke(sub_state)
finally:
    await locker.release(vm_id, batch_id)
```

**New CLI args:**
```
--restart-force           bypass maintenance window (requires --restart-force-reason)
--restart-force-reason    mandatory reason string when using --restart-force
```

### Tests added (`TestRestartServiceWindowAndLock`)

- `test_outside_window_returns_1` — window check fails, no Slack approval attempted
- `test_force_bypasses_window` — force=True with reason proceeds to approval
- `test_force_without_reason_returns_1` — force without reason is rejected
- `test_locked_vm_skips_execution` — locked VM is skipped, overall result is nonzero

**Key lesson:** Any operator-triggered execution path must be audited against the same safety checklist as the automated path. Maintenance windows and locking are not optional "batch-only" controls.

---

## Finding 3 (P1/P2) — Docs showed bare unit names (`nginx` instead of `nginx.service`)

### The problem

`safe_systemd_unit_name()` (added in the previous session) requires a type suffix (`.service`, `.socket`, etc.). But 18 locations across 5 files showed bare names like `--unit nginx`, which would immediately fail validation.

### The fix

Systematic find-and-replace across:
- `README.md` — 2 locations
- `SETUP.md` — 4 locations
- `RUN.md` — 5 locations
- `example/inventory.yaml` — 4 locations (comments + YAML values)
- `docs/learning/40-service-restart-module.md` — 1 location

All `nginx` → `nginx.service`, `gunicorn` → `gunicorn.service`, `redis-server` → `redis-server.service`.

**Key lesson:** When adding a new validator, grep all docs and examples immediately. Stale examples in documentation are indistinguishable from working ones to a reader.

---

## Finding 4 (P2) — orphaned-deps approval showed no exact package names; no drift detection

### The problem

The `assess_node()` ran `apt-get autoremove --simulate 2>/dev/null | tail -1` — only the last summary line ("N packages will be removed"). The approval message showed `orphaned-deps` as a categorical action with no package names. At execution, `autoremove()` ran without re-checking whether the candidate list had changed since assessment.

This violated the Exact-Object Approval invariant for a destructive action (package removal).

### The fix

**New helper `_parse_autoremove_candidates(output, os_family)`:**

```python
def _parse_autoremove_candidates(output: str, os_family: str) -> list[str]:
    packages: list[str] = []
    if os_family in ("debian", "ubuntu"):
        for line in output.splitlines():
            m = re.match(r"^Remv (\S+)", line)
            if m:
                packages.append(m.group(1))
    else:  # rhel/dnf
        in_removing = False
        for line in output.splitlines():
            if line.strip().startswith("Removing:"):
                in_removing = True
                continue
            if in_removing and line.startswith(" "):
                packages.append(line.split()[0])
            elif in_removing:
                in_removing = False
    return sorted(set(packages))
```

**assess_node:** Remove `| tail -1`. Store full output. Call `_parse_autoremove_candidates`. Store result in `state["orphaned_candidates"]`.

**execute_node drift gate:**

```python
current_candidates = _parse_autoremove_candidates(sim_result.stdout, os_family)
assessed_candidates = set(state.get("orphaned_candidates", []))
if set(current_candidates) != assessed_candidates:
    logger.warning("orphaned-deps candidate list drifted on %s — skipping", vm_id)
    output["orphaned-deps"] = "[SKIPPED — candidate list drifted since assessment]"
    continue
```

**Approval message** (in `graph.py`):

```python
candidates = preview.get("orphaned_candidates", [])
if candidates:
    lines.append(f"    - orphaned-deps: {len(candidates)} packages to autoremove")
    for pkg in candidates[:10]:
        lines.append(f"      • {pkg}")
    if len(candidates) > 10:
        lines.append(f"      … and {len(candidates) - 10} more")
```

Coverage label changes from `[CATEGORICAL]` to `[MIXED]` when `orphaned_candidates` is present.

### Tests added (`TestOrphanedDepsExactPreview`)

- `test_candidates_extracted_from_apt_simulate_output` — `Remv libfoo1 [...]` → `["libfoo1"]`
- `test_candidates_extracted_from_dnf_simulate_output` — dnf indented format
- `test_empty_simulate_output_returns_empty_list`
- `test_drift_causes_skip` — different candidates at execute time → `[SKIPPED — candidate list drifted`
- `test_no_drift_proceeds_to_removal` — matching empty candidates → autoremove runs

---

## Finding 5 (Decision) — Categorical actions acceptable for v1

**Decision:** Categorical approval is acceptable for LOW-risk, whitelist-bounded, non-destructive actions:
- `/tmp` cleanup (temp files only; nothing permanent)
- `apt-cache` / `yum-cache` (regenerable on next package operation)
- `journal` vacuum (logs, not data)
- `log_rotation` (compress/rotate, not delete)

These are honestly labeled `[CATEGORICAL]` in the approval message. The scope is hardcoded whitelist — never LLM-decided.

`orphaned-deps` is the exception: it removes installed packages (destructive), so it receives exact-object treatment (Finding 4).

Documented in `CLAUDE.md` Risk Tiers section.

---

## Key Numbers

| Metric | Before | After |
|---|---|---|
| Tests passing | 2354 | 2366 |
| New tests | — | +12 |
| Residual findings | 5 | 0 |

## Files Changed

| File | Finding |
|---|---|
| `scripts/install-docker-wrappers-v2.sh` | 1 |
| `tests/agent/subgraphs/test_docker_hygiene.py` | 1 |
| `errander/main.py` | 2 |
| `tests/agent/test_service_restart_cli.py` | 2 |
| `README.md`, `SETUP.md`, `RUN.md`, `example/inventory.yaml`, `docs/learning/40-service-restart-module.md` | 3 |
| `errander/agent/subgraphs/disk_cleanup.py` | 4 |
| `errander/agent/graph.py` | 4 |
| `tests/agent/subgraphs/test_disk_cleanup.py` | 4 |
| `CLAUDE.md` | 5 |

## Quiz Yourself

1. Why does `docker images --format '{{.ID}}'` break exact-match grep against `--no-trunc` output?
2. What are the two mandatory safety controls `run_restart_service()` must now check before executing on a VM?
3. What does `--restart-force` require in addition to being set, and why?
4. Which apt simulate output line prefix does `_parse_autoremove_candidates` key on for Debian/Ubuntu?
5. Why is orphaned-deps treated as exact-object approval while `/tmp` cleanup is categorical?
