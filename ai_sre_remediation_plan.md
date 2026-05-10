# Errander-AI — SRE Audit Remediation Plan

**Source audit:** `ai_sre_audit.md` (2026-05-10)
**Plan date:** 2026-05-11
**Status:** Validated. All 14 findings confirmed against current code on `main`.

---

## Validation summary

Every finding in the audit was checked against the actual source. Result:

| # | Finding | Verdict | Anchor |
|---|---------|---------|--------|
| 1 | LLM client built but not passed into graph | **Valid** | `main.py:233-252`, `vm_graph.py:177`, `decisions.py:108` |
| 2 | Dry-run / live state mismatch | **Valid (nuanced)** | `main.py:220`, `main.py:553`, `patching.py:243` |
| 3 | Approval runs *after* live execution | **Valid** | `graph.py:811-817`, `graph.py:503-524` |
| 4 | Dry-run approval is auto-skipped | **Valid** | `graph.py:503-506` |
| 5 | Patching rollback returns "not implemented" | **Valid** | `rollback.py:60-64`; `rollback_action` only used in tests |
| 6 | Per-env policies (`strict`/`moderate`/`relaxed`) not enforced | **Valid** | `validators.py:50,67,98-109`; `graph.py:596` |
| 7 | `fleet_failure_threshold` defined but never checked | **Valid** | `graph.py:231-234`; setting unused in routing |
| 8 | OS verification = `echo ok`; `verify_os_match` unused | **Valid** | `graph.py:208-214`, `os_detection.py:179-189` |
| 9 | SSH `known_hosts=None` (MITM exposure) | **Valid** | `ssh.py:91-103` |
| 10 | Shell injection via unquoted f-strings | **Valid** | `backup_verify.py:90`, `log_rotation.py:174-201`, `commands.py:91` |
| 11 | `apt-mark hold linux-*` — apt-mark doesn't accept globs | **Valid** | `commands.py:76-84` |
| 12 | `docker system prune -af` is more destructive than tier MEDIUM implies | **Valid** | `docker_prune.py:178` |
| 13 | Audit writes are best-effort and silently swallowed | **Valid** | `audit.py:102-136` |
| 14 | UI binds `0.0.0.0`, auth is opt-in, no CSRF | **Valid** | `metrics.py:1400,578-579,1385-1388` |

**Bottom line:** the auditor was correct. The biggest fish — finding #1, #2, #3, #5 — collectively mean the "AI plan/apply with rollback" story is not what the code does. These are ship blockers, not nits.

---

## Guiding principles for the rebuild

1. **One source of truth for `dry_run`.** It lives on the batch state. The executor reads from state per-action, not from its own field.
2. **Plan/apply, not run/approve.** Dry-run produces an immutable plan artifact. Live execution requires a fresh approval that references that plan's hash. No exceptions for dry-run.
3. **Deterministic safety, advisory AI.** LLM may recommend ordering and write reports. Validators, policies, and rollback are pure Python and have final authority.
4. **Fail closed on audit.** For live production actions, an audit write failure aborts the action. Dry-run can stay best-effort.
5. **No shell string interpolation.** All command construction uses `shlex.quote()` or arg-list-based execution. No exceptions.

---

## Phased plan

Work is grouped so it can be parallelized across ~3 devs. Each item lists the files to touch and the tests that must pass before merge. **Every phase ends with a clean `pytest`, `ruff`, and `mypy --strict` run.**

### Phase 0 — Ship-stoppers (block live execution until done)

Until Phase 0 is merged, **gate live mode** behind a startup check that refuses to run unless the operator passes `--unsafe-legacy-live` with a written justification logged to audit. This is a one-line guard in `main.py`.

#### 0.1 Unify dry_run as single source of truth — *findings #2, #4*

- **Touch:**
  - `errander/execution/sandbox.py` — remove `self.dry_run` attribute; executor becomes stateless w.r.t. mode.
  - `errander/agent/subgraphs/patching.py`, `docker_prune.py`, `log_rotation.py`, `disk_cleanup.py`, `backup_verify.py` — every site that reads `executor.dry_run` now reads `state["dry_run"]` and passes it explicitly into `executor.run(..., dry_run=...)`.
  - `errander/main.py:220` — construct executor without `dry_run` arg.
- **New tests** (`tests/integration/test_dry_run_propagation.py`):
  - CLI `--live` with `settings.dry_run_default=True` actually mutates VMs in the test harness.
  - CLI `--dry-run` with `settings.dry_run_default=False` never calls live execution paths.
  - State change mid-graph (e.g. policy downgrade) does not corrupt executor mode for prior actions.

#### 0.2 Plan/apply: approval gate before live execution — *finding #3*

- **New module:** `errander/models/plans.py` — already in target architecture but unused. Build `ImmutablePlan` Pydantic model with: `plan_id`, `batch_id`, `env_name`, `created_at`, `actions: list[PlannedAction]`, `plan_hash` (sha256 of canonical JSON), `llm_model_used`, `llm_recommendations_audit`.
- **Graph rewrite (`errander/agent/graph.py`):**
  - New node order: `validate_targets → plan_actions (no execution) → generate_plan_artifact → approval_gate → execute_plan → verify → generate_report`.
  - `plan_actions` runs the discovery + LLM + validator pipeline but produces only the `ImmutablePlan`. No SSH writes happen here.
  - `execute_plan` replays the plan; if discovered VM state has drifted (different package list, different disk-free figures by more than X%), the node aborts and routes back to `plan_actions` with a `drift_detected` flag.
- **Approval gate must:**
  - Post the plan artifact (not a free-text report) with its `plan_hash`.
  - Require dual approval for any plan containing MEDIUM-tier actions in environments with `policy=strict`.
  - Reject if `plan_hash` changes between approval and execution.
- **New tests** (`tests/agent/test_plan_apply_flow.py`):
  - Live batch cannot reach any subgraph executor without an `approved=True` state with a matching `plan_hash`.
  - Drift between plan and execute aborts the run cleanly.
  - Rejection routes to `generate_report` with status=`REJECTED`, no execution side effects.

#### 0.3 Dry-run no longer auto-approves — *finding #4*

- Remove the early-return at `graph.py:503-506`. Dry-run produces a plan; the plan still needs approval to graduate to live. Dry-run can *execute* against the sandbox without approval, but graduating dry-run → live is an explicit operator action with its own audit event.
- **Tests:** dry-run still ends without Slack approval; promoting to live requires a separate approval flow.

#### 0.4 Patch rollback — implement or disable live patching — *finding #5*

Two acceptable outcomes; pick one before merging Phase 0:

- **Option A (preferred):** Implement `_rollback_patching` properly:
  1. Before upgrade: `dpkg --get-selections > /var/lib/errander/pre-upgrade-<batch_id>.snapshot`.
  2. On failure: parse snapshot, build `apt-get install --allow-downgrades pkg=version ...` arg list, execute.
  3. Verify post-rollback package list matches snapshot; on mismatch, raise `CRITICAL` audit + Slack alert + abort batch.
  4. Test under fault injection: package not in cache, network drop mid-rollback, dpkg lock held.
- **Option B (interim):** Set a hard guard in `errander/agent/subgraphs/patching.py` that raises `LivePatchingDisabledError` whenever `dry_run=False`. Document explicitly in `SETUP.md`. Ship Option A in Phase 1.

In both cases, wire `rollback_action` into the failure path of `execute_plan` so it is reachable from the graph, not only from tests.

#### 0.5 Audit must fail-closed for live production — *finding #13*

- Add `audit_mode: Literal["best_effort", "strict"]` to settings, default `"strict"` for environments with `policy != "relaxed"`.
- In `errander/safety/audit.py`, raise `AuditWriteError` after retry exhaustion when `mode="strict"`. Graph catches and aborts the action.
- Dry-run stays best-effort regardless of mode.

---

### Phase 1 — Security hardening

#### 1.1 SSH host key verification — *finding #9*

- Add `ssh_known_hosts_path` and `ssh_strict_host_keys: bool` (default `True` in prod) to settings.
- `errander/execution/ssh.py:101`: pass `known_hosts=<path>` from settings. Fallback to TOFU (trust-on-first-use) only if `strict_host_keys=False`, and emit a high-severity audit event each connect when TOFU is used.
- Add `errander --bootstrap-known-hosts <env>` CLI command that connects once to every inventory host and pins keys.

#### 1.2 Kill shell-string interpolation — *finding #10*

- Introduce `errander/execution/command_builder.py` with helpers:
  - `safe_path(p: str) -> str` — `shlex.quote` + path sanity check.
  - `build_cmd(parts: list[str]) -> str` — quotes each part.
- Audit every f-string passed to `executor.run(...)` across `subgraphs/` and `execution/commands.py`. Replace each one. Known sites: `backup_verify.py:90`, `log_rotation.py:174-201`, `commands.py:77-91`.
- Add a `ruff` custom rule (or simple grep-based pre-commit hook) that rejects new f-strings containing both `{` and shell metacharacters in files under `errander/agent/subgraphs/` and `errander/execution/`.
- Tests: parametrized injection corpus — paths and package names containing `; rm -rf /`, backticks, `$(...)`, unicode separators. Must be neutralized or rejected.

#### 1.3 Fix kernel exclusion — *finding #11*

- `errander/execution/commands.py`: replace glob-based `apt-mark hold linux-*` with:
  1. Query installed packages: `dpkg-query -W -f='${Package}\n'`.
  2. In Python, filter by regex `^linux-(image|headers|modules|generic|aws|azure|gcp).*`.
  3. Build `apt-mark hold <exact-pkg-1> <exact-pkg-2> ...` as an arg list.
- Mirror logic for `yum versionlock` / `dnf versionlock` in the RHEL package manager strategy.
- Tests: golden fixtures of `dpkg-query` output → expected hold list. Negative cases (no kernel installed, weird vendor kernels).

#### 1.4 Docker prune scope — *finding #12*

- Default to `docker image prune` (dangling only) + `docker container prune --filter "until=24h"`. Drop `-a`.
- Add a `docker_prune_aggressive: bool` per-env setting; when `True`, runs `prune -a` but is reclassified as `RiskTier.HIGH` and requires approval.
- Add a registry allowlist: any image whose tag matches `allowed_image_prefixes` is preserved via `docker image ls` + `xargs docker tag` dance, or simpler: query `docker image ls --format` and skip pruning if any candidate matches the allowlist.

#### 1.5 UI security — *finding #14*

- `errander/observability/metrics.py:1400`: bind default = `127.0.0.1`. New setting `ui_bind_address` (operators with a reverse proxy can override).
- Auth becomes mandatory whenever bind is non-loopback. Startup fails if `ui_bind != 127.0.0.1` and `ERRANDER_UI_USER`/`ERRANDER_UI_PASSWORD` are unset.
- CSRF: add `itsdangerous`-based double-submit token on every approval POST. Token issued by GET on the approval page, validated on POST.
- Switch Basic Auth to session cookies + signed tokens; Basic Auth stays as a fallback for `curl`-based ops.

---

### Phase 2 — Policy enforcement and fleet safety

#### 2.1 Wire `requires_approval()` into the graph — *finding #6*

- `errander/agent/graph.py` approval routing reads `env.policy` and the plan's action set:
  - `strict`: every MEDIUM, HIGH, CRITICAL action requires approval.
  - `moderate`: HIGH and CRITICAL only.
  - `relaxed`: CRITICAL only (and CRITICAL should still be blocked outright per CLAUDE.md).
- `validate_action(policy=...)`: remove the "unused" docstring, actually consult policy when deciding pass/block. Add policy-aware rejection reason to the audit event.

#### 2.2 Enforce `fleet_failure_threshold` — *finding #7*

- After `validate_targets_node`, compare `len(failed)/len(targets)` against `settings.fleet_failure_threshold`.
- If exceeded: emit `FLEET_ABORT` audit event, Slack alert, route to `generate_report` with status=`ABORTED_PRE_FLIGHT`. No actions run.
- Tests: 100% failed → abort; below threshold → continue with healthy subset; exact threshold → configurable inclusive/exclusive (document the choice).

#### 2.3 Strict OS verification — *finding #8*

- Replace `echo ok` in `validate_targets_node` with `detect_os()` + `verify_os_match(detected, declared)`.
- Inventory mismatch → that target moves to `failed_targets` with reason `OS_MISMATCH`. Operator can override with `inventory.allow_os_mismatch: true` per host.

---

### Phase 3 — Honest AI integration

This is what unlocks the "Agentic AI SRE" framing. Do **not** ship until Phase 0 is merged — running an LLM-driven planner without plan/apply is the worst possible combination.

#### 3.1 Pass LLMClient into the graph — *finding #1*

- `build_batch_graph(... llm_client: LLMClient | None = None)`.
- Thread `llm_client` from `main.py:run_env_batch` → `build_batch_graph` → `build_vm_graph` → `plan_actions_node`.
- `plan_actions_node` calls `prioritize_actions(vm_info, llm_client=llm_client, policy=env.policy)`.
- Fallback path stays — LLM unavailable means hardcoded ordering, with an audit event noting `llm_used=false`.

#### 3.2 Constrained plan schema

- LLM output validated against `PlannedAction` Pydantic model with strict allow-listed `action_type` and parameter bounds.
- LLM never returns shell commands. It returns *action selections* over a fixed vocabulary. Subgraphs translate to commands deterministically.
- Reject and log any LLM output that proposes actions outside the allow-list or violates policy.

#### 3.3 AI eval harness

- New directory `tests/ai_evals/` with:
  - Golden VM states → expected plan shapes (deterministic, doesn't grade on exact LLM output, grades on safety properties: no kernel patches, no off-whitelist cleanup, ordering respects risk tier).
  - Prompt injection corpus: VM hostnames, OS strings, package descriptions containing classic injection payloads. The LLM must not propose unsafe actions; the validator must reject if it does.
  - Schema-violation corpus: malformed JSON, wrong types, unknown action_type. Must fall back cleanly.
- Eval runner: `uv run python -m errander.evals --suite ai-safety`. Wire into CI as a non-blocking job initially, blocking after baseline stabilizes.

#### 3.4 Per-decision AI audit

- Every LLM call captures: model, base URL, prompt template id, prompt hash, response, latency, token counts.
- Stored in `ai_decisions` audit table with FK to `batch_id`. Surface in the UI plan-detail view.

---

### Phase 4 — End-to-end verification

#### 4.1 Staging soak

- Three disposable VMs (Ubuntu 22.04, Debian 12, RHEL 9). Owned by the team, destroyed after each run.
- Nightly job runs every action type in dry-run, then approves and runs live, then verifies state. Failures page on-call.

#### 4.2 Chaos suite

Add scripted failures and assert correct behavior:

- SSH connection dropped mid-action → action marked `FAILED`, rollback triggered for patching, batch continues with remaining VMs (or aborts based on threshold).
- `dpkg` / `yum` lock held by another process → action retries N times then fails cleanly, no partial state.
- Disk full on target → cleanup action partial-success behavior is well-defined.
- Audit DB locked → in `strict` audit mode, live action aborts; in `best_effort`, continues with degraded-audit alert.
- Slack unreachable → approval times out per config; batch state is preserved for retry, no silent auto-approve.
- LLM unreachable / slow / malformed → fallback ordering used, audit event records LLM-unavailable.

#### 4.3 Test infra fix

The auditor saw 124 errors from Windows temp-directory permission failures. Before approval, the test suite must run clean on Linux CI **and** Windows dev boxes. Likely culprits: `tempfile.TemporaryDirectory()` without `ignore_cleanup_errors=True` on Windows. Sweep `tests/conftest.py` and any test that creates sqlite files.

---

## Suggested ownership split (3 devs)

| Dev | Phase 0 | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|---|
| A (graph/state) | 0.2, 0.3 | — | 2.1 | 3.1, 3.2 |
| B (execution/security) | 0.1, 0.4 | 1.1, 1.2, 1.3, 1.4 | 2.3 | — |
| C (safety/audit/UI) | 0.5 | 1.5 | 2.2 | 3.3, 3.4 |

Phase 4 is shared and gates the public "production-ready" claim.

---

## What gets reclaimed from the audit's harshest line

> *"LangGraph-based VM maintenance automation with optional LLM-assisted components."*

After Phase 0–2, that line is fair and we should adopt it on the README. After Phase 3 ships with passing evals, **and** Phase 4 staging soak shows green for two weeks, the "Agentic AI SRE" framing is earned.

Anything before that, we keep the modest label. The auditor's instinct was right: the hard SRE crowd will pull this apart at any production incident, and the only defense is a system whose code matches its claims.

---

## Open questions for the team before kickoff

1. **Patching rollback — Option A or Option B for Phase 0?** A is more work but unblocks live patching; B is one PR and ships safely but punts the feature.
2. **Approval channel for plan artifacts.** Slack reactions work for free-text reports; for plan hashes we probably want a real link to the UI plan view. Are we OK requiring UI access for live approvals?
3. **`strict` vs `moderate` defaults.** CLAUDE.md says strict for prod. Do we want the graph to *refuse* to accept `relaxed` for any environment with `production=true` in inventory?
4. **Drift detection threshold (Phase 0.2).** How much VM state change between plan and execute is "drift" vs "noise"? Suggest starting at: any new/removed package, disk free delta > 10%, kernel version change.
5. **AI eval CI gating.** Block PRs on eval regressions from day one, or run as canary for the first sprint? Suggest canary, then block after baseline.
