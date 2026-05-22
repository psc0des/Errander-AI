# 46 — Docker hygiene v1.2: unused image execution scope

## What was built and why

v1.2 extends the docker_hygiene execution scope to include unused (non-dangling) images
that are older than 30 days. The execution wiring (`_EXECUTABLE_CLASSES`, wrapper
`image_unused` branch) was already in place from v1.1, but the **approval surface** had
a critical gap: it enforced scope at the *class* level instead of the *finding* level.

**The gap**: `IMAGE_UNUSED` was in `_EXECUTABLE_CLASSES`, so the formatter showed `✓` for
ALL `image_unused` findings regardless of age. A young image (age 5 days → `report_only`)
would show `✓` and could be force-approved by index. `approve all` would also have selected
it.

v1.2 closes this gap with three coordinated fixes and adds 6 tests that lock in the scope
boundary.

## Key concepts

### Per-finding classification vs per-class gating

The initial design gated approval at the class level:
```python
executable = " ✓" if klass in _EXECUTABLE_CLASSES else " (report-only)"
```

This is insufficient when a class has findings at mixed classifications. `image_unused`
has two sub-groups — `cleanup_candidate` (age > 30) and `report_only` (age ≤ 30) — and
both were shown as executable.

The fix pushes the check to the individual finding:
```python
if f.classification == FindingClassification.CLEANUP_CANDIDATE and klass in _EXECUTABLE_CLASSES:
    executable = " ✓"
elif f.classification == FindingClassification.INVESTIGATE:
    executable = " ⚠ investigate"
else:
    executable = " (report-only)"
```

### Three places to enforce the scope boundary

Any approval surface must enforce scope at three sites — missing any one creates a bypass:

1. **Formatter** — operator-facing markers (`✓` vs `(report-only)`) must reflect the
   individual finding's classification, not just the class.

2. **`_select_all`** — `approve all` must default to `cleanup_candidate` only. Without
   this, `approve all` would select every finding in `_EXECUTABLE_CLASSES` including
   `report_only` ones.

3. **`_parse_explicit_indices`** — explicit index approval (`approve images 1`) must
   check the finding's classification before adding it to the selected set. Without this,
   an operator can bypass the scope by selecting a `report_only` finding directly.

```python
# In _parse_explicit_indices (hygiene_approval.py):
if f.classification != FindingClassification.CLEANUP_CANDIDATE:
    raise HygieneReplyError(
        f"{class_key}.{idx} is classified {f.classification.value!r} "
        f"— only cleanup_candidate findings can be approved for removal"
    )
```

### `_select_all` default + error on non-removal classifications

```python
def _select_all(assessment, classification_filter):
    if classification_filter in (FindingClassification.INVESTIGATE, FindingClassification.REPORT_ONLY):
        raise HygieneReplyError(
            f"classification {classification_filter.value!r} cannot be approved for removal"
        )
    effective = classification_filter if classification_filter is not None else FindingClassification.CLEANUP_CANDIDATE
    ...
```

`approve all investigate` and `approve all report_only` are now explicit errors, not silent
no-ops. This is safer: a confused operator gets a clear message rather than an empty approval.

## Code walkthrough

### Classification (unchanged from v1.1)

`_classify_image()` in `docker_hygiene.py` already produced the right classifications:

```python
def _classify_image(resource_class, age_days):
    if resource_class == DockerResourceClass.IMAGE_DANGLING:
        return FindingClassification.CLEANUP_CANDIDATE
    if (
        resource_class == DockerResourceClass.IMAGE_UNUSED
        and age_days is not None
        and age_days > UNUSED_IMAGE_CLEANUP_AGE_DAYS  # 30
    ):
        return FindingClassification.CLEANUP_CANDIDATE
    return FindingClassification.REPORT_ONLY
```

### Wrapper (unchanged from v1.1)

`errander-docker-remove-v2` already had the `image_unused` branch with per-object
re-validation:

```bash
image_unused)
    # Re-validate: still unreferenced by any container?
    referenced=$(/usr/bin/docker ps -a --format '{{.Image}}' | grep -Fx "$obj_id" || true)
    if [ -n "$referenced" ]; then
        echo "result class=$obj_class id=$obj_id status=drift_skipped reason=now_referenced"
        continue
    fi
    /usr/bin/docker rmi "$obj_id" ...
```

### New test: execute path for image_unused

```python
async def test_unused_image_cleanup_candidate_execute_path(self) -> None:
    unused_finding = DockerHygieneFinding(
        resource_class=DockerResourceClass.IMAGE_UNUSED,
        classification=FindingClassification.CLEANUP_CANDIDATE,
        object_id="sha256:unused-old",
        age_days=60,
    )
    # ... approval injected, wrapper returns removed ...
    assert result["status"] == ActionStatus.SUCCESS.value
    assert result["removal_results"][0].status == RemovalStatus.REMOVED
```

## Execution scope (current after v1.2)

| Resource class | Condition | Classification | Removable |
|---|---|---|---|
| `image_dangling` | always | `cleanup_candidate` | ✓ |
| `image_unused` | age > 30 days, unreferenced | `cleanup_candidate` | ✓ |
| `image_unused` | age ≤ 30 days | `report_only` | No |
| `container_stopped` | exit 0, age > 7 days | `cleanup_candidate` | ✓ |
| `container_stopped` | exit 137/139 | `investigate` | No |
| `container_stopped` | other | `report_only` | No |
| `volume_unreferenced` | always | `report_only` | No (v1.5) |
| `build_cache` | always | `report_only` | No (v1.5) |

## Gotchas

- **Don't gate on class alone.** A class being in `_EXECUTABLE_CLASSES` does not mean
  every finding in it is removable. Always check `finding.classification`.

- **Three sites, not one.** Missing the per-finding check in even one of (formatter,
  `_select_all`, `_parse_explicit_indices`) creates a bypass. If you add a new
  class to `_EXECUTABLE_CLASSES` that has mixed classifications, update all three.

- **`approve all` meaning.** Users expect `approve all` to mean "approve everything safe
  to remove". If it silently included `report_only` items, that expectation would be
  violated. The default is now `cleanup_candidate` — explicit `approve all cleanup_candidate`
  is synonymous.

## Questions to test understanding

1. Why wasn't `image_unused` removal broken in v1.1 for the happy path (age > 30)?
2. What would happen without the `_parse_explicit_indices` check if an operator typed
   `approve images 1` and item 1 was a 5-day-old image?
3. Why does `_select_all` raise on `INVESTIGATE` instead of silently skipping those
   findings?
4. What needs to change when v1.5 adds volume deletion to bring volumes into execution
   scope?
