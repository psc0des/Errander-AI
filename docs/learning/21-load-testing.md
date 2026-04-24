# 21 — Load Testing: Wave Machinery, Canary Logic, and Concurrent Locking

## What Was Built and Why

After Phase 3 hardening, the agent's wave/canary/locking machinery was well-unit-tested at the node level but had never been exercised at fleet scale or under concurrent load. This doc covers the two new test files:

1. `tests/agent/test_load.py` — 20 tests across large-fleet wave partitioning, full batch graph integration (mocked VM graphs for speed), and concurrent lock operations
2. `tests/ui/test_approvals_playwright.py` — 22 Playwright tests for the approvals UI (`/ui/approvals`), including approve/reject button actions and cross-page navigation

---

## Part 1: Load Tests (`test_load.py`)

### Key Design Decision: Mock `build_vm_graph`, Not Individual Nodes

The batch graph integration tests need to run 10–15 VMs without real SSH. The temptation is to mock SSH at the `execute()` level — but that makes the test fragile (node-level detail leaks into the test). Instead, we mock `build_vm_graph` itself:

```python
with patch("errander.agent.graph.build_vm_graph", return_value=_make_fast_vm_mock()):
    final = await build_batch_graph(...).compile().ainvoke(state)
```

`_make_fast_vm_mock()` returns a `MagicMock` whose `.compile().ainvoke()` resolves instantly with a valid result dict — bypassing the entire VM graph while keeping the batch graph's wave/health/collect logic real.

### Wave Partitioning — Pure Math Tests

`_partition_into_waves(targets, pct)` is a pure function (no I/O), so its tests are synchronous and cheap:

```python
def test_100_vms_25_percent_creates_4_waves(self) -> None:
    waves = _partition_into_waves(_make_targets(100), 25)
    assert len(waves) == 4
    assert all(len(w) == 25 for w in waves)
```

Edge cases worth testing: odd-sized fleets (47 VMs at 30% — wave sizes are ceil(14.1)=15, 15, 17), 1% (each VM in its own wave), and the math identity `sum(wave_sizes) == total_vms`.

### Wave Abort — SSH Call Counting

The wave abort test injects failure via a call counter on `ssh.execute()`. The key is counting correctly:

- `validate_targets` makes **1 SSH call per VM** — 12 VMs = 12 calls
- `check_wave_health_node` makes **1 SSH call per VM in the wave** — wave 0 = 3 calls (calls 13–15)
- Wave 1 health checks are calls 16–18 — these are made to fail

```python
call_count = 0

async def _ssh(*args, **kwargs):
    nonlocal call_count
    call_count += 1
    return _ssh_ok() if call_count <= 15 else _ssh_ok("", 1)
```

Expected: 6 results (waves 0 and 1 dispatched), `wave_aborted=True`.

### Canary Abort — Call 11 Fails

With 10 VMs: 10 validate calls (1–10), then canary health check (call 11). Failing call 11 triggers `canary_passed=False` and the fleet is skipped:

```python
return _ssh_ok() if call_count <= 10 else _ssh_ok("", 1)
# Expected: 1 result (canary only), wave_aborted=True, canary_passed=False
```

### VM Crash Recovery

The batch graph's `run_vm_node` wraps `ainvoke()` in a bare `Exception` guard (see Phase 3 hardening). The test verifies this by making the first `ainvoke()` call raise, then confirming the other 4 VMs still produce results:

```python
async def _ainvoke(state):
    nonlocal call_count
    call_count += 1
    if call_count == 1:
        raise RuntimeError("vm graph exploded")
    ...  # normal result
```

Expected: 5 results total — 1 FAILED (caught crash), 4 SUCCESS.

### Concurrent Lock Tests — `asyncio.gather()` at Scale

The `FileLocker` uses `os.O_CREAT | os.O_EXCL` for atomic creation. The 50-coroutine race test is the definitive proof:

```python
results = await asyncio.gather(
    *[locker.acquire("vm-shared", f"batch-{i:03d}") for i in range(50)],
)
assert sum(results) == 1  # exactly one winner
```

This works because `os.O_EXCL` is a kernel-level atomic operation — the OS serialises concurrent `open()` calls on the same path.

---

## Part 2: Playwright Approvals Tests (`test_approvals_playwright.py`)

### Module-Scoped Fixture with Pre-Seeded Approvals

The fixture starts an aiohttp server in a background thread with an `ApprovalManager` pre-loaded with 5 pending approvals using distinct batch IDs:

```python
manager.register("batch-view-01", "Freed 1.2 GB on prod/web-01\nPatched 3 packages.")
manager.register("batch-approve-01", "Log rotation completed on staging/app-01.")
manager.register("batch-reject-01", "Disk cleanup dry-run on dev/db-01.")
```

Using distinct batch IDs per test class means clicking Approve for `batch-approve-01` doesn't affect `batch-reject-01` tests — the tests are independent despite sharing a single server (module scope).

### The Report-in-Details Gotcha

The approval report is rendered inside a collapsed `<details>` element:

```html
<details class="apv-report">
  <summary>View dry-run report</summary>
  <pre class="apv-pre">Freed 1.2 GB on prod/web-01...</pre>
</details>
```

Playwright's `to_be_visible()` fails on elements inside closed `<details>` — the `<pre>` resolves but is hidden. The fix: click the `<details>` to expand it first:

```python
page.locator("details.apv-report").first.click()
expect(page.get_by_text("Freed 1.2 GB")).to_be_visible()
```

### Idempotent Click Guard

The reject test uses an idempotent guard because module-scoped server state persists across test methods — if the batch was already consumed, `reject_form.count() == 0`:

```python
if reject_form.count() > 0:
    reject_form.get_by_role("button", name="Reject").click()
    expect(page).to_have_url(f"{approvals_base_url}/ui/approvals")
```

---

## Gotchas

### `ActionStatus.COMPLETED` Does Not Exist

`ActionStatus` (a `StrEnum`) has: `PENDING`, `SKIPPED`, `DRY_RUN_OK`, `SUCCESS`, `FAILED`, `ROLLED_BACK`, `ROLLBACK_FAILED`, `NEEDS_MANUAL`. There is no `COMPLETED` — use `SUCCESS`.

### `with` Nesting → `with (...,)` (SIM117)

Python 3.10+ supports parenthesised `with` for multiple context managers. Ruff SIM117 flags nested `with` statements:

```python
# Before (flagged)
with patch.object(ssh, "execute", ...):
    with patch("errander.agent.graph.build_vm_graph", ...):
        ...

# After (correct)
with (
    patch.object(ssh, "execute", ...),
    patch("errander.agent.graph.build_vm_graph", ...),
):
    ...
```

### `Path` in `TYPE_CHECKING` (TC003)

Ruff TC003 flags stdlib imports used only in type annotations — they should move to `TYPE_CHECKING` blocks to avoid runtime import overhead:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
```

With `from __future__ import annotations`, all annotations are strings at runtime, so `Path` is never evaluated outside type checking.

---

## Quiz Yourself

1. Why does mocking `build_vm_graph` rather than `ssh.execute` make the batch graph integration tests more robust?
2. In the wave abort test, why are there exactly 15 successful SSH calls before failure is injected?
3. Why does `os.O_EXCL` guarantee that exactly one of 50 concurrent `acquire()` calls wins?
4. What does Playwright's `to_be_visible()` check that differs from "element exists in the DOM"?
5. Why do the approve/reject tests use distinct batch IDs rather than re-using `batch-view-01`?
