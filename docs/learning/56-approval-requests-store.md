# 56 — Durable Approval Requests Store (R3 keystone)

## What was built and why

Until this change, a pending approval lived in one Python dict (`ApprovalManager._pending`)
inside the agent process. That worked only because the agent and the web UI share a process,
and it had a fatal flaw (fable.md finding F9): **kill the agent while an approval is pending
and the approval is gone** — the Slack message stays up, the operator reacts ✅, and nothing
ever happens.

This change moves approvals into PostgreSQL (`approval_requests`, migration #13) behind a new
store, `errander/safety/approval_store.py`. The in-memory `ApprovalManager`, `PendingApproval`,
`await_dual_approval`, and `BatchApprovalResult` are deleted. Three things become true:

1. **Approvals survive restarts.** The pending row is durable; a reconciler job adopts it.
2. **Decisions are race-safe by construction.** One SQL `UPDATE ... WHERE status='pending'`
   settles Slack-vs-UI-vs-timeout races — exactly one writer sees `rowcount == 1`.
3. **The approval record is audit evidence by construction** — who decided, when, which exact
   items, and when an executor claimed it, all in one row.

It also fixed a latent bug that made **every deferred replay abort**: the plan hash is computed
over `{batch_id, env_name, vm_plans}`, but replay always generated a *fresh* `batch_id`, so
`load_deferred_artifact_node`'s recompute never matched. Fix: `preloaded_batch_id` flows from
the deferred record through `run_env_batch` into `init_batch_node`, which reuses it.

## Key concepts

### Durable-first ordering

```
persist pending row  →  post to Slack  →  spawn reaction watcher  →  wait on store
```

The row is written BEFORE the Slack post. If the agent crashes between the two, the worst case
is a recoverable pending row with no Slack message (the reconciler re-posts nothing, but the
web UI shows it and it expires honestly). The reverse order would leave an *invisible* approval:
a Slack message whose reactions nobody will ever read.

### Atomic decide — the row is the lock

```sql
UPDATE approval_requests
SET status = :status, decided_by = :decided_by, decided_at = :decided_at, ...
WHERE batch_id = :batch_id AND status = 'pending'
```

No Python locks, no asyncio coordination across processes. The `WHERE status = 'pending'`
clause makes the database the arbiter: concurrent deciders each run the UPDATE, and Postgres
guarantees exactly one sees `rowcount == 1`. The loser logs "already decided" and moves on.
The same pattern guards the **execution claim**:

```sql
UPDATE approval_requests SET execution_started_at = :now
WHERE batch_id = :batch_id AND status = 'approved' AND execution_started_at IS NULL
```

`execution_started_at` means "an executor owns what happens next" — stamped both for immediate
execution and for handing off to the deferred store. This is what makes the reconciler unable
to double-execute a batch (locked in by `test_orphan_executed_only_once_across_ticks`).

### wait_for_decision — poll + event hybrid

The gate waits with a 2-second DB poll, *plus* an in-process `asyncio.Event` registry so a
same-process decision (the web UI today) wakes it instantly. The poll half is not redundant:
it is what makes Step 4 (process split) work without any design change — a decision written by
a different process has no way to set this process's event, but the poll sees the row.

Timeout is owned by the waiter (and the reconciler), not by the Slack watcher: `poll_approval`
returning `(False, None)` means "no reaction seen", which is **not a decision** — writing it
as a rejection would let a Slack network blip overwrite a UI approval race. Single source of
truth for timeouts: `mark_timeout` / `expire_overdue`, both themselves atomic against decisions.

### The restart reconciler (main.py, 60 s interval job)

Three passes, in dependency order:

1. **Expire** — pending rows past `expires_at` → `timeout` + audit event.
2. **Resume** — orphaned pending rows (no in-process waiter, detected via `store.has_waiter`)
   that have a Slack message get their reaction watcher re-spawned with the *remaining* TTL.
3. **Execute** — `approved AND execution_started_at IS NULL` rows are claimed and replayed
   through the existing P0-2 deferred-replay path (`preloaded_plan_json` + `preloaded_batch_id`
   + `preloaded_approved_items`). Outside the window → handed to `DeferredExecutionStore`
   instead, which the window-opener already knows how to replay.

### Exact-object approval survives restarts and defers now

Per-item UI selections (`approved_items`) used to evaporate when a batch was window-deferred
(the defer happened before the per-item dict was built, and the deferred record never carried
it). The approval row stores `approved_items_json`, and both replay paths (window opener and
reconciler) recover it and pass `preloaded_approved_items` → `operator_approved_packages` →
the existing `_filter_patching_packages` at wave dispatch. The plan hash still verifies because
it commits to the *full* approved plan; filtering happens downstream of verification.

## Gotchas hit during implementation

- **`ON CONFLICT ... DO UPDATE ... WHERE`**: the `create()` upsert refreshes a same-batch_id
  row *only if it is still pending* — a crash-restart re-entering the gate must not resurrect
  or overwrite a decided row. Tests pre-decide a row and then let the gate "re-create" it to
  prove the decision sticks (this is also how gate unit tests avoid concurrent deciders).
- **Slack watcher writes need namespacing**: `decided_by` is now `slack:U123` / `ui:admin`,
  so the audit row records the channel, and R2 (RBAC) can later validate the `ui:` namespace.
- **The metrics nav badge hit ~10 handlers**: every UI page computes `pending_count`. The old
  `len(manager.get_pending())` became `await store.count_pending()` — a dedicated COUNT(*)
  instead of materializing rows.
- **Module-global watcher registry in tests**: `_resumed_slack_watchers` lives at module level
  in `main.py`; the reconciler tests need an autouse fixture that cancels and clears it,
  otherwise one test's resumed watcher leaks into the next.

## Tests that lock the contracts in

- `tests/safety/test_approval_store.py::TestDecide::test_concurrent_decide_race_has_exactly_one_winner` (AC4)
- `tests/test_approval_reconciler.py::test_executes_orphaned_approved_via_replay` (AC3)
- `tests/test_approval_reconciler.py::test_orphan_executed_only_once_across_ticks` (claim atomicity)
- `tests/agent/test_deferred_replay.py::test_init_batch_node_reuses_preloaded_batch_id` (hash-bug fix)
- `tests/agent/test_plan_apply_flow.py::test_per_item_selection_flows_into_state` (exact-object continuity)

## Quiz yourself

1. Why must the pending row be written *before* the Slack post, not after?
2. Two operators click Approve and Reject at the same instant. What guarantees exactly one
   decision is recorded, and where does the loser find out?
3. The Slack watcher's poll times out. Why must it NOT write a rejection?
4. Why did every deferred replay abort at hash verification before this change, and why does
   reusing the original `batch_id` fix it without weakening the integrity check?
5. What stops the reconciler from executing a batch that a live approval gate is about to
   execute itself? (Two mechanisms — one advisory, one authoritative.)
