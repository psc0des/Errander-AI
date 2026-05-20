# 43 — `errander vm-facts` CLI (Project B3)

## What was built and why

`errander vm-facts` is a read-only operator CLI for spot-checking the operational learning facts that OperatorAssistant (Phase B1+B2) surfaces to the LLM.

The problem: an LLM might summarise "patching often fails on prod/db-01 due to dpkg lock" based on `VMFactsStore` data. But how does an SRE verify that claim before trusting it? There was no way to inspect those facts without reading Python source or querying SQLite manually.

B3 closes that gap: a single command prints the same three fact types that land in the LLM prompt, with human-readable formatting.

## Key concepts

### Three fact types — all from existing data

No new tables. `VMFactsStore` (Phase B1) already computes these on demand from `audit_events`:

| Fact type | Data source | Query |
|---|---|---|
| `ActionOutcomeFact` | `action_completed` / `action_failed` events | Last 20 per (vm, action_type); computes success rate |
| `VMRebootPatternFact` | `reboot_required_detected` + patching terminal events | Ratio of reboot detections to patching runs |
| `ActionRejectionFact` | `approval_rejected` + batches that contain the rejection | Count + reasons per action_type, last 90 days |

### Two operating modes

**Per-VM mode** (`--vm-facts prod/web-01`): all three tables for one VM.

**Cross-fleet mode** (`--vm-facts-action patching`): outcomes only, across every VM that has run the action. No reboot section (doesn't make sense fleet-wide — every VM has its own reboot pattern).

### Visual rate indicator

`_fmt_rate()` turns a float into a colour-free string the terminal can render without ANSI:

```python
✓ 100%   # ≥ 90% — healthy
~  75%   # 60–89% — marginal
✗  33%   # < 60% — problem
```

No ANSI colours because the output may be piped to logs or tickets.

## Code walkthrough

### `errander/commands/vm_facts.py`

Three async helpers, one main async handler, one sync entry point:

```python
async def _print_outcomes(db_path, vm_id, action_type):
    async with VMFactsStore(db_path) as store:
        if vm_id is not None:
            facts = await store.action_outcomes(vm_id, action_type=action_type)
        else:
            # Cross-fleet: discover all VMs for this action_type first
            async with aiosqlite.connect(db_path) as _db:
                rows = await _db.execute_fetchall(...)
            for vid in vms:
                facts.extend(await store.action_outcomes(vid, ...))
    # Print table

async def cmd_vm_facts(args, db_path) -> int:
    # Validate args, ensure schema, print three sections, return exit code

def dispatch_vm_facts(args, db_path) -> int:
    return asyncio.run(cmd_vm_facts(args, db_path))
```

`dispatch_vm_facts` is synchronous because `main.py`'s dispatch loop calls it before the event loop is running (same pattern as `runs.py`'s `dispatch_runs`).

### `errander/main.py` wiring

Two new args added to `_parse_args()`:

```python
parser.add_argument("--vm-facts", metavar="VM_ID", nargs="?", const="",
                    dest="vm_facts_vm_id")
parser.add_argument("--vm-facts-action", metavar="ACTION_TYPE",
                    dest="vm_facts_action")
```

`nargs="?"` with `const=""` lets `--vm-facts` be used with or without a value:
- `--vm-facts prod/web-01` → `args.vm_facts_vm_id = "prod/web-01"`
- `--vm-facts` alone → `args.vm_facts_vm_id = ""`  
- Not provided → `args.vm_facts_vm_id = None`

The dispatch block checks `vm_facts_vm_id is not None OR vm_facts_action is not None` (either flag activates the sub-command). If `vm_id == ""` it's treated as `None` inside `cmd_vm_facts`.

## Gotchas

**Connection leak in cross-fleet mode**: the original implementation opened `aiosqlite.connect(db_path)` without a context manager. The coroutine held the connection open until GC. Fixed to `async with aiosqlite.connect(db_path) as _db`.

**`const=""` not `None`**: `argparse` distinguishes "flag not given" (`None`) from "flag given without value" (`const`). Using `const=None` would make them indistinguishable. The empty string sentinel lets `cmd_vm_facts` detect the "cross-fleet, no specific VM" case while still having a way to check if the flag was given at all.

**`dispatch_vm_facts` is sync, not async**: `main.py` calls it from `async_main()`, which runs in an event loop. `asyncio.run()` inside an already-running event loop would raise `RuntimeError`. However, the `--vm-facts` branch is dispatched before the agent's event loop starts (same as `--runs`), so `asyncio.run()` is safe here.

## Quiz

1. What does `nargs="?"` do on a `--vm-facts` argument? What is `const=""` for?
2. Why does cross-fleet mode skip the reboot pattern section?
3. The rejection facts table is always fleet-wide regardless of `vm_id`. Why?
4. How does `VMFactsStore` compute `ActionRejectionFact.rejection_reasons`? What's the join?
5. What would break if `dispatch_vm_facts` used `await` instead of `asyncio.run()`?
