# 60 — Detect-and-Propose, Phase 1: the proposal bridge

## What was built and why

Errander is a *supervised agentic* platform, but until now the "agentic" part was
thin: the LLM annotated plans a deterministic scheduler already made. Nothing in the
system *originated* work from what it observed. **Detect-and-propose** closes that gap
at the origination end without weakening a single safety boundary.

Phase 1 (this doc) is the **proposal bridge** — deliberately LLM-free:

1. The daily probe already collects signals (disk growth, config drift, failed SSH
   logins). A new **deterministic detector** turns those signals into `AgentProposal`
   records.
2. Proposals land in a durable store and render in a new Web UI queue
   (`/ui/proposals`), badged **AGENT-ORIGINATED**, with their evidence chain.
3. A named operator approves / rejects / snoozes (RBAC: `decide_approvals`).
4. An approved **actionable** proposal is executed by a **proposal reconciler** that
   runs the *existing* deterministic sub-graph (`disk_cleanup` / `log_rotation`) for
   the one target VM — approval originates work, it never bypasses the safety gates.

The whole point: **agency lives in origination, never in execution.** A proposal is a
*suggestion record*, not an authorization. See `docs/AI-ARCHITECTURE.md` →
"Detect-and-propose" and `tasks/fable-plan.md` for the full multi-phase plan.

## Key concepts

### D1 — proposal approval is work origination, not execution authorization

The subtle design decision. When you approve a proposal, the reconciler does **not**
invent a new execution path. It mirrors the `--restart-service` operator-triggered
pattern: acquire the VM lock → run the same compiled sub-graph the batch graph uses →
per-action audit. Because execution re-assesses fresh VM state, staleness between
"proposed last night" and "executed now" is handled by the machinery that already
exists — we reuse the Exact-Object drift protection, we don't parallel it.

### LLM-optional at every stage (D2)

The detector is pure Python. If the LLM is down, proposals still flow — with thinner
evidence. This preserves Errander's "never blocked by LLM" rule and means Phase 1
ships real value with zero LLM dependency. The Phase 2 investigation loop will only
ever *enrich* what the detector already admits.

### Validation is the guardrail

`AgentProposal` is a Pydantic model whose validators are load-bearing:

- `action_type` must be in `PROPOSABLE_ACTIONS = {"disk_cleanup", "log_rotation"}` —
  LOW-risk, whitelist-bounded, categorical-approvable actions only. `docker_hygiene`
  (destructive, object-level) is deliberately **not** proposable.
- `vm_id` / `env_name` / `signal_kind` must match `_IDENTIFIER_RE` — no shell
  metacharacters, no path traversal. An attacker-influenced log line can't smuggle a
  payload into a proposal field.
- A `model_validator` enforces kind/action consistency: ACTION proposals must carry an
  action; REVIEW proposals must not.

### Dedup via a partial unique index

```sql
CREATE UNIQUE INDEX idx_agent_proposals_open
    ON agent_proposals (vm_id, action_key)
    WHERE status = 'pending';
```

One *open* proposal per (vm, action). `create_or_refresh()` does an
`INSERT ... ON CONFLICT (vm_id, action_key) WHERE status='pending' DO UPDATE` — a
re-probe refreshes evidence on the open row instead of spawning a duplicate. A queue
with three good proposals a week beats thirty noisy ones a day, so dedup is a Phase 1
requirement, not a later nicety. Decided/expired proposals don't block a fresh one
(the index only covers `status='pending'`).

## Code walkthrough

### The store mirrors `ApprovalRequestStore`

`ProposalStore` copies the established conventions exactly: async DB-backed rows, an
atomic `decide()` (`UPDATE ... WHERE status='pending'` → exactly one race winner),
`expire_overdue()`, and `mark_execution_started()` for the claim. It is intentionally
*separate* from the approval store — a proposal is a suggestion, an approval is an
authorization; conflating them would blur the trust boundary.

`mark_execution_started()` refuses anything that isn't `status='approved' AND
kind='action' AND execution_started_at IS NULL`, so a *review* proposal can never
become executable even if approved, and a proposal executes at most once.

### The reconciler's gates (main.py `_proposal_reconciler`)

Three passes on a 60s interval: expire overdue pending, wake elapsed snoozes, execute
approved-actionable. The execution pass fails **closed** at every step:

- unknown env/VM → claim + record failed (stops the row looping) — never silently drop
- action disabled in inventory since approval → refuse (config-drift gate), audit it
- outside maintenance window → leave unclaimed, retry next tick
- VM locked → leave unclaimed, retry next tick
- `agent_dry_run` → never claim or execute (honest dry-run — an approved proposal waits
  for a live agent rather than being marked done by a rehearsal)

### The UI carries provenance

`/ui/proposals` shows the AGENT-ORIGINATED badge, the signal kind, confidence, origin,
probe id, and the full evidence chain per card. For actionable proposals it states
plainly that approval originates a targeted run; for review-only it states nothing
executes. That honesty *is* the exact-object principle applied to origination — the
protection comes from evidence quality, not the approval gesture.

## Gotchas encountered

### A latent circular import, exposed by collection order

Adding `errander/safety/proposal_store.py` and its tests surfaced a **pre-existing**
circular import that had nothing to do with the feature:

```
errander.safety.validators          (module-level: import ...subgraphs.disk_cleanup)
  → errander.agent.subgraphs.__init__ (imports patching)
    → errander.agent.subgraphs.patching (module-level: import ...safety.validators)  ✗
```

`import errander.safety.validators` fails standalone on clean HEAD. The full suite only
ever passed because some earlier test imported the subgraph chain first and populated
`sys.modules`. The new test files shifted collection order and exposed it as ~180
cascading errors in the alphabetically-last `tests/web/`.

**Fix:** make the weakest edge a function-level import — `validate_no_pkg_lock` in
`patching.py` is used in exactly one node, so importing it there (not at module top)
breaks the cycle with zero behavior change. Lesson: fix import cycles at the source;
a green suite that depends on import order is a landmine the next new module steps on.
Verify with `python -c "import <the module that failed>"`, not just via pytest.

### mypy and heterogeneous `ainvoke`

The two sub-graphs have different TypedDict states, so a single `compiled` variable
assigned either one fails mypy's overload check. Resolved by `cast()`-ing the shared
`dict` sub-state to each branch's state type inside the `if/else`, matching how
`vm_graph` already handles it.

## Quiz yourself

1. Why is `docker_hygiene` excluded from `PROPOSABLE_ACTIONS` when the detector could
   easily emit it? (Hint: exact-object approval, destructive-action contract.)
2. What stops an approved *review* proposal (drift, login spike) from executing
   anything? Name the exact guard.
3. The reconciler runs in `agent_dry_run` mode. What must it *not* do, and why would
   claiming-and-marking-done be wrong?
4. A signal recurs on three consecutive nightly probes. How many rows exist in
   `agent_proposals`, and which SQL construct guarantees that?
5. Why is the proposal store a separate type from the approval store rather than a
   new status on the existing one?

## References

- Plan: `tasks/fable-plan.md` (all phases); diagram: `docs/diagrams/detect-and-propose.md`
- Canonical safety model: `docs/AI-ARCHITECTURE.md`
- Mirrored reference: `errander/safety/approval_store.py`
