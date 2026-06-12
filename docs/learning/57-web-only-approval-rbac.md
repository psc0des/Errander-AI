# 57 — Web-Only Approval with Users/Groups RBAC (§8d Step 3, R2)

## What was built and why

R2 removes approve/reject authority from Slack entirely. Before this change,
anyone in the `#errander-approvals` channel could approve a fleet change by
adding a ✅ reaction — Slack had **weak authorization** (channel membership =
authority) but **strong attribution** (the real Slack user ID landed in the
audit row). A single shared web login would have reversed the trade. R2 keeps
both: per-user accounts with group-based RBAC, so approval authority =
authentication, and every decision records a *named* user **and** the group
they acted under.

The owner's framing (fable.md §8a): *"approval authority = authentication,
not channel membership."*

Three Slack decision paths existed and all three were removed:

1. **Batch gate** — the transitional `watch_slack_reactions` task (Step 2
   shipped it explicitly as "until R2 deletes it").
2. **Service-restart CLI** — `main.py` blocked on raw `poll_approval`
   reaction polling. Now it persists a durable `approval_requests` row, posts
   a notify-and-link Slack message (optional!), waits cross-process for the
   web decision, then atomically claims the approval before executing.
3. **docker_hygiene Slack reply parser** — structured thread replies
   ("approve images 1,3") were the same class of hole. The parser + poller
   were deleted; the Slack message keeps the exact-object listing and the
   signed web URL.

## Key concepts

### Schema: groups carry permissions (migration #14)

```
users(username PK, password_hash, created_at, created_by)
groups(name PK, description)
group_permissions(group_name, permission)      ← the load-bearing table
user_groups(username, group_name, added_by, added_at)
sessions(token_hash PK, username, created_at, expires_at)
```

Why a `group_permissions` join table instead of boolean columns on `groups`?
The spec requires that a third group (e.g. `approver`: can decide, cannot
manage users) be **plain INSERTs, never a migration** — separation of duties
is the first thing an auditor asks for. `tests/safety/test_user_store.py::
test_third_group_is_plain_inserts` locks this in: it creates the `approver`
group with two INSERTs and asserts the resolved permissions.

v1 vocabulary: `decide_approvals`, `manage_users`, `manage_settings` (all
granted to `admin`; `reader` has no permission rows — "view" is implicit for
any authenticated account).

### Passwords: stdlib scrypt, no new dependency

`hashlib.scrypt` (n=2^14, r=8, p=1) with a per-hash random salt, stored as
`scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>` — the parameters travel with the
hash so they can be raised later without breaking old rows. Verification is
`hmac.compare_digest`. A pre-computed `_DUMMY_HASH` is verified for unknown
usernames so login latency doesn't reveal whether an account exists.

### Sessions: DB rows, hashed tokens

The cookie holds a random `secrets.token_urlsafe(32)`; the DB stores only its
SHA-256. A leaked DB dump therefore can't be replayed as a cookie. Sessions
survive agent restarts (the old in-memory dict didn't) and — because they're
just rows — they'll work unchanged when R3 splits the web UI into its own
process. `SessionStore.resolve()` re-reads groups/permissions **per request**,
which is what makes acceptance criterion #4 ("membership changes take effect
without restart") true: demote an admin and their very next click is a 403.

### Server-side RBAC: `_require_permission` in handlers, not hidden buttons

The middleware authenticates; the *handlers* authorize:

```python
user = _require_permission(request, PERM_DECIDE_APPROVALS)   # raises 403
...
await approval_store.decide(batch_id, approved=approved,
                            decided_by=f"ui:{user.username}",
                            decided_by_group=user.group_granting(PERM_DECIDE_APPROVALS))
```

This is the §8a acceptance #3 design point: a `reader` (or an anonymous
visitor holding a perfectly valid signed URL) cannot decide **even though the
URL verifies** — the signed token *locates* the pending approval, the session
*authorizes*. `tests/observability/test_rbac.py` drives the real aiohttp app
(auth + CSRF middleware) through `TestClient` to prove it end-to-end.

### Zero-users bootstrap mode (fail closed)

With no accounts: GET pages stay open **on loopback only** (the clone-and-try
dry-run experience), every mutation is 403, and a non-loopback bind refuses to
start. No users = no authorized deciders. Migration path: if
`ERRANDER_UI_USER`/`ERRANDER_UI_PASSWORD` are set and the users table is
empty, startup seeds that account into `admin` once (audited as
`migration:env`) — existing deployments keep logging in with the same
credentials, now against a real account.

### The reconciler claim grace period

New cross-process wrinkle: the `--restart-service` CLI now waits on the store
from a *separate process*. Between the operator's decision and the CLI's
`mark_execution_started()` claim (a ~2 s window), the agent's reconciler
would see an "orphaned approved" row and could steal the claim. Fix: the
reconciler skips approved rows whose `decided_at` is within 120 s
(`_RECONCILER_CLAIM_GRACE_SECONDS`). The atomic claim already guaranteed
*at-most-once* execution; the grace period restores *the-right-executor*.

## Gotchas

- **Test-harness truncation vs seed rows.** `tests/conftest.py` TRUNCATEs all
  tables before each test — including the migration-seeded `groups` /
  `group_permissions`. Fix: the seed lives in `SEED_GROUPS_SQL` +
  `seed_default_groups()` (idempotent `ON CONFLICT DO NOTHING`), applied on
  every `run_migrations` *and* re-applied by conftest after each truncate.
  Lesson: never put re-needed seed data inside a one-shot migration body.
- **aiohttp `AppKey` vs string keys.** Setting `app["loopback_bind"] = False`
  in a test silently does nothing — `web.AppKey("loopback_bind")` and the
  string `"loopback_bind"` are different keys. Build a fresh app instead.
- **Volumes lost their approval path.** The web hygiene form never allowed
  volume selection (deliberately — highest blast radius); the deleted Slack
  reply syntax was the only way to approve `volume_unreferenced` candidates.
  R2 therefore makes volumes report-only everywhere. Fail closed was chosen
  over adding a volume checkbox; the Slack message now says
  "report-only in web UI (v1)".
- **`_format_plan_for_approval` keeps its signed `/plans/{id}` link** — that
  token *is* the locator for plan inspection (no session required by design,
  finding P2-1). Don't confuse it with approval URLs, which carry no
  authority.

## Code map

| Piece | Where |
|---|---|
| Migration #14 + seed | `errander/safety/migrations.py` |
| UserStore / SessionStore / scrypt | `errander/safety/user_store.py` |
| Auth middleware, `_require_permission`, login | `errander/observability/metrics.py` |
| Notify-and-link message | `errander/safety/approval.py::request_approval` |
| Gate (no watcher) | `errander/agent/graph.py::approval_gate_node` |
| Reconciler (no pass 2, claim grace) | `errander/main.py::_approval_reconciler` |
| Restart CLI store flow | `errander/main.py::run_restart_service` |
| User-management CLI | `errander/main.py::run_user_management` |
| Hygiene formatter (reply syntax gone) | `errander/safety/hygiene_approval.py` |

## Quiz yourself

1. Why does a *valid* signed hygiene URL return 302/403 for an anonymous or
   reader user? What does the token authorize, and what does the session
   authorize?
2. Why is `group_permissions` a join table instead of boolean columns on
   `groups`? What auditor requirement does that serve?
3. The reconciler already used an atomic claim. Why was the 120 s grace
   period still needed after the restart CLI moved to the store?
4. Why must `seed_default_groups` run on every `run_migrations` call instead
   of living only inside migration #14's SQL?
5. An admin's groups are changed to `reader` while they're logged in. Exactly
   which code path makes their next decision attempt fail?
