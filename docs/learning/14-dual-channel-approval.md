# 14 — Dual-Channel Approval: Slack Reactions + Web UI Buttons

> **Superseded twice:** (2026-06-11, §8d Step 2) the in-memory `ApprovalManager` /
> `await_dual_approval` race described here was replaced by the durable
> `ApprovalRequestStore` (PostgreSQL `approval_requests` table) — see
> `56-approval-requests-store.md`. Then (2026-06-12, §8d Step 3 / R2) the Slack
> channel lost decision authority entirely: approval is web-only with users/groups
> RBAC, and Slack is notify-and-link — see `57-web-only-approval-rbac.md`.
> Kept for the asyncio.Event race-pattern learning content.

## What Was Built and Why

The approval flow previously worked through Slack only: the agent posted a dry-run report
to #errander-approvals, then polled for ✅/❌ emoji reactions every 30 seconds. This is
fine at a desk but inconvenient if Slack is unavailable or if the operator prefers a web
interface.

The new flow supports **both channels simultaneously**:
- An operator can approve/reject by clicking a button at `/ui/approvals`
- Or by reacting to the Slack message with ✅/❌
- Whichever channel responds first wins; the other is cancelled
- If Slack is unreachable or misconfigured, UI-only mode works automatically

---

## Key Concepts

### `asyncio.wait(FIRST_COMPLETED)` — racing two coroutines

The core of the implementation is a race between two concurrent async tasks:

```python
slack_task = asyncio.create_task(_poll_slack())
ui_task    = asyncio.create_task(_wait_ui())

done, running = await asyncio.wait(
    {slack_task, ui_task},
    return_when=asyncio.FIRST_COMPLETED,
)

# Cancel the loser
for t in running:
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass

winner = next(iter(done))
approved, user_id = winner.result()
```

Key points:
- `asyncio.wait()` returns two sets: `done` (completed tasks) and `running` (still running)
- `FIRST_COMPLETED` returns as soon as ANY task finishes — no waiting for both
- The losing task is explicitly cancelled and awaited to drain cleanup (e.g., cancelling a
  `asyncio.sleep` inside the Slack poller, or an `asyncio.Event.wait()` inside the UI task)
- `winner.result()` raises if the task raised an exception — since both return `(bool, str|None)`,
  this is safe

### `asyncio.Event` — signalling between coroutines

`PendingApproval._event` is an `asyncio.Event()` created when the approval is registered.
The HTTP handler (when the operator clicks a button) sets it:

```python
# HTTP handler — runs in the same event loop as the waiting task
manager.decide(batch_id, approved=True, user_id="ui")
# Inside decide():
approval._event.set()  # wakes up anyone waiting on _event.wait()
```

The UI task waits on it:

```python
async def _wait_ui() -> ApprovalResult:
    try:
        await asyncio.wait_for(pending._event.wait(), timeout=float(timeout_seconds))
    except asyncio.TimeoutError:
        return False, None
    return pending.result
```

This is the simplest inter-coroutine communication pattern:
- No queues, no locks, no shared mutable state beyond the event flag
- Works because both coroutines run in the **same event loop** (the aiohttp server and the
  agent graph both use asyncio — same process)

### Idempotent `decide()` with `dict.pop`

The tricky part of dual-channel racing: both channels might try to record the decision.
For example: Slack polls while the UI handler also fires. The second `decide()` call must
be a no-op.

```python
def decide(self, batch_id: str, approved: bool, user_id: str | None = None) -> None:
    approval = self._pending.pop(batch_id, None)
    if approval is None:
        return  # Already decided — no-op
    approval.approved = approved
    approval.decided_by = user_id
    approval._event.set()
    self._history.append(approval)
```

`dict.pop(key, default)` atomically removes the entry and returns it (or the default).
If the batch was already decided, `approval` is `None` and we return immediately.
This makes `decide()` **idempotent** — safe to call from both channels without coordination.

---

## Architecture Walkthrough

### `PendingApproval` dataclass

```python
@dataclass
class PendingApproval:
    batch_id: str
    report: str
    posted_at: datetime
    slack_message_ts: str | None = None
    # Set by decide() — None while pending
    approved: bool | None = field(default=None, init=False)
    decided_by: str | None = field(default=None, init=False)
    # Signalling event — not in __init__
    _event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
```

`field(init=False)` means these fields are NOT constructor parameters — they're set
after construction by `decide()`. `repr=False` hides `_event` from `repr()` output.

### `ApprovalManager`

```
register(batch_id, report, slack_ts?)
  → creates PendingApproval, stores in _pending dict

decide(batch_id, approved, user_id?)
  → pops from _pending (idempotent), sets event, appends to _history

wait_for_decision(batch_id, timeout=1800)
  → awaits _event.wait() with timeout, auto-rejects on TimeoutError

get_pending() → list of pending (copy)
get_history(limit=20) → recent decisions, newest first
```

### `await_dual_approval()` flow

```
1. Post to Slack → get slack_ts (or log warning if failed)
2. Register with ApprovalManager → get pending object
3. Create two tasks:
   - _poll_slack: calls poll_approval() which polls Slack every N seconds
   - _wait_ui: awaits pending._event.wait() (set when HTTP handler calls decide())
4. asyncio.wait(FIRST_COMPLETED) → wait for first to finish
5. Cancel the loser
6. Call manager.decide() with winner's result (idempotent — safe if UI already decided)
7. Return result
```

### Web UI routes

| Route | Method | Handler |
|---|---|---|
| `/ui/approvals` | GET | `_ui_approvals` — lists pending + history |
| `/ui/approvals/{id}/approve` | POST | `_ui_approval_decide` — calls decide(True) |
| `/ui/approvals/{id}/reject` | POST | `_ui_approval_decide` — calls decide(False) |

The POST handlers use HTML form submit (not JavaScript fetch), so they work in any browser
with no JS required. They redirect to `/ui/approvals` after deciding.

The `{action:(approve|reject)}` regex pattern validates the action at the routing level —
only valid values reach the handler.

```python
app.router.add_post(
    r"/ui/approvals/{batch_id:[^/]+}/{action:(approve|reject)}",
    _ui_approval_decide,
)
```

---

## Gotchas and Mistakes

### Patching `asyncio.sleep` globally breaks concurrent tasks

The original test patched `asyncio.sleep` to speed up Slack polling:

```python
with patch("asyncio.sleep", side_effect=lambda t: asyncio.sleep(0)):
    ...
```

Problem: `side_effect=lambda t: asyncio.sleep(0)` calls `asyncio.sleep` which IS the mock
at patch time — infinite recursion. Also: `_ui_decides()` used `await asyncio.sleep(0.02)`
which became the mock, causing coroutine-never-awaited warnings.

Fix: Use `asyncio.Event().wait()` as a blocking primitive instead of sleep:

```python
async def _blocking_reactions(ts: str) -> list:
    await asyncio.Event().wait()  # blocks until cancelled
    return []
client.get_reactions = _blocking_reactions
```

This blocks the Slack path indefinitely (until cancellation) without using sleep at all.

### `asyncio.Event()` in Python 3.10+

Before Python 3.10, `asyncio.Event()` was bound to the running event loop at creation time.
Creating it in a dataclass field factory (outside an event loop) would fail.

Since Python 3.10, `asyncio.Event()` is loop-agnostic at creation — it attaches to the
running loop when first awaited. This project requires Python 3.12, so `asyncio.Event` as
a `field(default_factory=asyncio.Event, init=False)` is safe.

---

## Quiz Yourself

1. Why is `asyncio.wait(FIRST_COMPLETED)` better than `asyncio.gather` for this use case?
2. What happens if both the Slack poller and the UI handler call `decide()` at the same
   millisecond? Which wins, and how?
3. Why is `await t` (where `t` is a cancelled task) important before returning from
   `await_dual_approval`?
4. The `_event` field uses `init=False`. What does that mean, and why is it important here?
5. What would happen if `decide()` was NOT idempotent and both channels called it?
6. Why does the UI approval flow still work even when `slack_client=None`?
