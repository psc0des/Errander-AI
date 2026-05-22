# 47 — docker_hygiene v1.5: Volume and Build Cache Deletion

## What was built and why

v1.1 (Sessions 1–3) shipped dangling image and stopped container removal.
v1.2 added unused image removal (age > 30 days).
Both `volume_unreferenced` and `build_cache` have been `report_only` since day one —
surfaced for visibility but never removed.

v1.5 brings both into the execution scope:

- **Volumes** have high blast radius (data is permanently gone). They get an extra
  friction gate: they can never be selected via `approve all` — the operator must name
  them by 1-based index (`approve volumes 1,3`).
- **Build cache** is lower-stakes (a single prune command, no named data). It can be
  selected by `approve all` like images and containers.
- Both are **default-off** in inventory config. The gate lives entirely at
  classification time — when disabled, the classifier returns `REPORT_ONLY` regardless
  of age, so the approval surface never encounters a cleanup_candidate.

---

## Key concepts

### Classify-time gate (Option A)

When a resource class should be default-off with an operator opt-in flag, the cleanest
pattern is to implement the gate inside the classifier:

```python
def _classify_volume(
    last_mount_days: int | None,
    *,
    threshold: int = VOLUME_LAST_MOUNT_AGE_DAYS,
    enabled: bool = False,
) -> FindingClassification:
    if not enabled:
        return FindingClassification.REPORT_ONLY
    if last_mount_days is None or last_mount_days <= threshold:
        return FindingClassification.REPORT_ONLY
    return FindingClassification.CLEANUP_CANDIDATE
```

This keeps `_EXECUTABLE_CLASSES` static. The approval surface (formatter, `_select_all`,
`_parse_explicit_indices`) works correctly without modification — it simply never sees a
volume cleanup_candidate when the flag is off.

The alternative (Option B: dynamic `_EXECUTABLE_CLASSES` based on config) would require
threading the config flag into three approval-surface sites. Option A is strictly simpler.

### `_EXPLICIT_ONLY_CLASSES` frozenset

Some classes should be selectable by explicit index only, even when classified as
cleanup_candidate. Volumes are in this category.

```python
_EXPLICIT_ONLY_CLASSES: frozenset[DockerResourceClass] = frozenset({
    DockerResourceClass.VOLUME_UNREFERENCED,
})
```

This frozenset is checked in two places:

1. **`_select_all()`** — skips any class in `_EXPLICIT_ONLY_CLASSES`:
   ```python
   for klass in _EXECUTABLE_CLASSES:
       if klass in _EXPLICIT_ONLY_CLASSES:
           continue  # volumes require explicit index
       for f in by_class.get(klass, []):
           if f.classification == effective:
               selected.append(f)
   ```

2. **Formatter marker** — shows `✓ ⚠ explicit-only` instead of `✓` for these classes,
   so the operator knows they must use index approval.

Build cache is intentionally NOT in `_EXPLICIT_ONLY_CLASSES` — it's a singleton per VM
and lower blast radius, so `approve all` is fine.

### build_cache identity: `"build_cache"`, not `"<build_cache>"`

`DockerHygieneFinding.identity` falls back to `f"<{resource_class.value}>"` when neither
`name` nor `object_id` is set. The `errander-docker-remove-v2` wrapper outputs
`id=build_cache` (no angle brackets). This mismatch would cause the parser to treat the
result as unapproved (Contract B: drop results for un-approved objects) and silently
fail the removal.

Fix: `_build_finding()` for BUILD_CACHE now sets `name="build_cache"` explicitly:

```python
return DockerHygieneFinding(
    resource_class=resource_class,
    classification=_classify_build_cache(reclaimable, enabled=build_cache_deletion_enabled),
    name="build_cache",   # identity = "build_cache" — matches wrapper id=build_cache
    reclaimable_bytes=reclaimable,
)
```

**Rule:** For every resource class, verify that `DockerHygieneFinding.identity` returns
exactly the string the wrapper outputs as `id=...`. Trace: `_build_finding()` →
`identity` property → wrapper stdout. Write a round-trip test for each class.

### Backup_verify soft context

When there are volume cleanup_candidates in the assessment, the Slack approval message
optionally shows the result of the most recent backup_verify action for the same VM:

```python
def format_hygiene_approval_message(
    assessment: DockerHygieneAssessment,
    *,
    backup_verify_passed: bool | None = None,
    ...
) -> str:
```

- `backup_verify_passed=True` → `:white_check_mark: Backup status: Verified`
- `backup_verify_passed=False` → `:warning: Backup verify: not run or failed`
- `backup_verify_passed=None` → nothing shown

This is a **soft signal** — it shows context but does not gate the approval. The operator
can still approve volume removal even when backup_verify failed; the message simply makes
the state visible. `vm_graph.py` extracts the backup_verify result from `state["results"]`
before calling the formatter.

---

## Wrapper changes (`install-docker-wrappers-v2.sh`)

The old `volume_unreferenced|build_cache` catch-all branch was replaced with two separate
branches, each with its own drift re-check (Contract A gate 2 — per-object wrapper
re-validation):

**volume_unreferenced branch:**
```bash
still_dangling=$(/usr/bin/docker volume ls --filter dangling=true -q 2>/dev/null \
    | grep -Fx "$obj_id" || true)
if [ -z "$still_dangling" ]; then
    # drift: volume is now referenced or already removed
    echo "result class=$obj_class id=$obj_id status=drift_skipped reason=..."
    continue
fi
/usr/bin/docker volume rm "$obj_id"
```

**build_cache branch:**
```bash
current_reclaim=$(/usr/bin/docker system df ... | awk '...')
if [ "$current_reclaim" != "nonzero" ]; then
    echo "result class=$obj_class id=$obj_id status=drift_skipped reason=no_reclaimable_cache"
    continue
fi
/usr/bin/docker builder prune -f
```

Both branches emit `drift_skipped` when the object's state has changed since approval —
Contract A ensures approvals never execute on drifted state.

---

## Config fields (in `ActionConfig`, also in `EnvironmentSchema.actions`)

```yaml
actions:
  docker_hygiene:
    enabled: true
    command_mode: wrapper
    volume_deletion_enabled: false          # default
    volume_last_mount_days_threshold: 90   # default; must be >= 1
    build_cache_deletion_enabled: false     # default
```

Contradiction guard: `volume_deletion_enabled: true` with `enabled: false` raises
`ConfigError`. Threshold < 1 raises `ConfigError`.

---

## Tests added (+37)

| Class | Count | What it tests |
|---|---|---|
| `TestClassifyVolume` | 5 | below/at/above threshold, None age, disabled always |
| `TestClassifyBuildCache` | 4 | reclaimable>0/=0/None enabled, disabled always |
| `TestParseAssessV2OutputV15` | 5 | volume above/below/at threshold, build_cache enabled |
| `TestParseRemoveV2OutputV15` | 4 | volume removed/drift_skipped, build_cache removed/drift_skipped |
| `TestExecuteNodeV15` | 2 | volume approved calls wrapper, build_cache approved calls wrapper |
| `TestVolumeAndBuildCacheApproval` | 13 | formatter markers, approve all scope, explicit-only, backup context |
| `TestDockerHygieneV15Config` | 4 | field defaults, contradiction guard |

---

## Gotchas

1. **`dry_run=True` in execute tests**: `_base_state()` defaults to `dry_run=True`. Execute
   node tests must pass `dry_run=False` or they return `DRY_RUN_OK` instead of `SUCCESS`.

2. **Mock execute signature**: The execute function is called with positional args
   (`cmd: str, *args, **kwargs`) — use `*args, **kwargs` in the mock to absorb all of them.
   The allowlist text is embedded inside the command string via `printf %s ...`, not passed
   as stdin.

3. **`approve all` scope with explicit-only**: Tests that mix volume_candidates and dangling
   images must assert that `approve all` selects dangling but NOT volumes.

4. **`test_report_only_volume_cannot_be_approved_by_index`**: The old
   `test_report_only_class_cannot_be_approved` matched `"report-only"` (class-level error
   message). After adding VOLUME_UNREFERENCED to `_EXECUTABLE_CLASSES`, the class-level
   guard no longer fires; the classification-level guard fires with `"report_only"` (underscore).
   The test was renamed and the match pattern updated.
