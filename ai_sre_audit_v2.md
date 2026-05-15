# AI SRE Audit v2

Date: 2026-05-14  
Repository: `E:\AI\Errander-AI`  
Auditor stance: strict production AI SRE review, facts over claims

## Executive Verdict

This project is not production-ready as an autonomous senior AI SRE agent for live VM infrastructure.

The codebase contains a thoughtful control-plane skeleton: dry-run/apply modes, approval concepts, audit logging, SSH execution boundaries, action subgraphs, risk tiers, and deterministic fallbacks when the LLM fails. Those are good foundations.

However, the current implementation does not satisfy the most important enterprise SRE requirement: the exact approved plan is not the exact thing later executed. Deferred execution appears to discard the approved plan and starts a fresh live run. Several live actions can report success even when underlying commands fail. Rollback state is not represented correctly. Static quality gates are also not clean despite documentation claiming strictness.

My conclusion: the engineers should not claim this project is "over" or "optimal." At best, it is a promising prototype/control-plane foundation that needs serious remediation before touching real production systems.

## What I Verified

I reviewed the repository from scratch and inspected the main implementation areas:

- Agent orchestration: `errander/agent/graph.py`, `errander/agent/vm_graph.py`
- AI decisioning: `errander/agent/decisions.py`, `errander/integrations/llm.py`
- SRE action subgraphs: patching, disk cleanup, Docker prune, log rotation, backup verify
- SSH and command execution: `errander/execution/ssh.py`, `errander/execution/commands.py`, `errander/execution/command_builder.py`
- Safety, approval, audit, rollback, validators, deferred execution
- Main service wiring and UI/metrics security
- README/spec/status claims
- Local verification with pytest, ruff, and mypy

I did not modify implementation code.

## High Severity Findings

### P0-1: Approved Plan Is Not an Immutable Execution Plan

The strongest production blocker is that approval is attached to a high-level action summary, not to exact commands, package versions, detected state, or dry-run evidence.

Evidence:

- `errander/agent/graph.py` `plan_vm_node` creates a plan containing mostly:
  - `vm_id`
  - `planned_actions`
  - `action_type`
  - `risk_tier`
  - `params`
  - `os_family`
- `generate_plan_artifact_node` hashes `batch_id`, `env_name`, and `vm_plans`.
- `verify_plan_hash_node` recomputes the same in-memory artifact hash before live execution.
- The live wave dispatcher injects approved action types into VM state, but the VM subgraphs still re-evaluate live host state and build commands during execution.

Impact:

An operator approves "patching" or "disk cleanup," not the exact package list, command set, rollback plan, target state snapshot, and expected effect. That is not sufficient for production autonomous SRE operation.

Required fix:

The dry-run plan artifact must include exact generated commands, package candidates and versions, target files/paths, preflight outputs, state snapshot, risk proof, rollback instructions, and expiration semantics. Live apply must execute that immutable artifact or fail closed when drift is detected.

### P0-2: Deferred Execution Re-Plans Instead Of Executing The Approved Plan

Deferred maintenance windows appear unsafe.

Evidence:

- `errander/safety/deferred.py` persists only:
  - `batch_id`
  - `env_name`
  - `approved_by`
  - `window_start`
  - status metadata
- It does not store the approved plan artifact or plan hash.
- `errander/agent/graph.py` `approval_gate_node` saves deferred execution without the approved plan body.
- `errander/main.py` `_window_opener` later calls `run_env_batch(... dry_run=False, force=True ...)`.

Impact:

At the maintenance window, the system starts a fresh live batch. That can produce a different plan against different host state. The original approval is no longer meaningfully tied to the executed change.

This is a production showstopper.

Required fix:

Deferred execution must persist the full approved artifact, approval identities, approval timestamp, plan hash, expiry, and state snapshot. The window opener must apply that exact artifact, not launch a fresh planning run.

### P0-3: Live Command Failures Can Be Marked Successful

Several action subgraphs mark live actions as successful even when individual commands fail.

Evidence:

- `errander/agent/subgraphs/docker_prune.py` `execute_node` sets status to `SUCCESS` in live mode regardless of `result.success`.
- `errander/agent/subgraphs/disk_cleanup.py` `execute_node` records command outputs and returns `SUCCESS` in live mode without failing on per-command failure.
- `errander/agent/subgraphs/log_rotation.py` `execute_node` can return `SUCCESS` even when file rotation or fallback commands fail.
- `errander/execution/commands.py` `AptManager.upgrade_all` and `DnfManager.upgrade_all` end their generated shell script with `true`, which can mask package manager failure.
- `errander/agent/subgraphs/patching.py` trusts SSH `result.success`; if the command builder masks failures, patching can appear successful.

Impact:

In production, a failed cleanup, failed patch, or partial action can be recorded as successful. That corrupts audit history and can trigger false confidence in subsequent AI decisions.

Required fix:

Every live command must have strict exit semantics. Command wrappers must not end with unconditional success. Multi-command actions need structured per-step status, failed-step propagation, and a clear final action status.

### P0-4: Rollback State Is Not Modeled Correctly

Rollback does not produce first-class machine-readable rollback outcomes.

Evidence:

- `errander/agent/subgraphs/patching.py` `rollback_node` returns `ActionStatus.FAILED` even after rollback succeeds.
- If rollback fails, it also returns `ActionStatus.FAILED`.
- `audit_results_node` maps non-success states into generic failed audit events.

Impact:

The system cannot reliably distinguish:

- action failed and no rollback attempted
- action failed and rollback succeeded
- action failed and rollback failed
- action failed and manual intervention is required

That is unacceptable for autonomous remediation in live infrastructure.

Required fix:

Add explicit terminal states such as `ROLLED_BACK`, `ROLLBACK_FAILED`, and `NEEDS_MANUAL_INTERVENTION`. Alerting, audit, and UI must expose these states.

### P0-5: Local Quality Gates Contradict Documentation Claims

The repository documentation claims strong validation, including a large passing test suite and strict mypy posture. Local verification does not support that claim.

Commands run:

- `.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.\audit_tmp_v2_pytest`
- `.\.venv\Scripts\python.exe -m ruff check errander tests`
- `.\.venv\Scripts\python.exe -m mypy errander`

Observed results:

- Pytest did not finish cleanly in this environment. It reached `1171 passed, 111 skipped, 132 errors` before cleanup crashed with `PermissionError`.
- A previous pytest run using `C:\tmp` failed immediately because this environment cannot create that temp directory.
- Ruff failed with 627 reported errors.
- Mypy failed with 139 errors in 18 files.

Important nuance:

Some pytest failures were caused by local filesystem permission behavior. Still, the ruff and mypy failures are direct evidence that the repository is not currently clean under the advertised quality gates.

Required fix:

Make CI authoritative and reproducible. The project should publish the exact commands, environment, and latest passing CI run. Strict mypy/ruff claims should not exist unless those commands pass cleanly.

## Medium Severity Findings

### P1-1: Approved Action Parameters Are Ignored For Several Actions

The approval plan can include action parameters, but several VM action runners do not pass those parameters into the actual subgraph.

Evidence:

- `errander/agent/vm_graph.py` `_run_disk_cleanup` does not pass approved paths, age thresholds, or sizing parameters.
- `_run_log_rotation` does not pass approved log paths, size threshold, or compression choices.
- `_run_docker_prune` does not pass the aggressive prune flag from action params.
- `_run_backup_verify` does pass backup paths, so this is inconsistent rather than universally broken.

Impact:

An operator may approve one set of parameters while the system executes defaults. That violates approval integrity.

Required fix:

All action subgraphs must receive and enforce the approved action parameters. The live executor should reject parameters that are missing, unknown, or changed after approval.

### P1-2: AI Layer Is Shallow Compared With "Senior Autonomous AI SRE" Claim

The AI component mostly prioritizes action types. It does not currently behave like a senior SRE agent performing deep investigation.

Evidence:

- `errander/agent/decisions.py` asks the LLM for ordered `action_types`.
- The schema does not require causal analysis, evidence chains, confidence, blast-radius reasoning, or remediation alternatives.
- LLM failure analysis exists but does not appear to be meaningfully wired into execution control flow.
- Batch report generation in `errander/agent/graph.py` uses deterministic rendering, not the LLM report path.

Impact:

This is safer than giving the LLM direct SSH access, which is good. But the product claim should be reduced. The current AI is a prioritization assistant wrapped in deterministic workflows, not an autonomous senior SRE.

Required fix:

Introduce structured AI reasoning artifacts:

- incident hypothesis
- evidence list
- confidence score
- selected remediation with rejected alternatives
- risk proof
- expected postcondition
- rollback trigger
- human-readable operator explanation

Those artifacts should be evaluated before approval and stored in audit history.

### P1-3: Package Lock Validation Fails Open

Evidence:

- `errander/safety/validators.py` `validate_no_pkg_lock` treats SSH/probe failure as clear and lets execution continue.

Impact:

If the system cannot determine whether a package manager lock exists, it may continue patching anyway. In production, unknown package lock state should block or skip patching.

Required fix:

For live patching, package lock probe failure should be fail-closed unless an explicit operator override exists.

### P1-4: Approval Timeout Settings Are Not Wired Through

Evidence:

- `errander/safety/approval.py` supports timeout and poll interval arguments.
- `errander/agent/graph.py` `approval_gate_node` calls `await_dual_approval` without passing configured timeout settings.

Impact:

Configuration may claim one approval timeout while runtime behavior uses defaults.

Required fix:

Pass configured approval timeout and polling interval into the approval gate and test the behavior.

### P1-5: Dry-Run Can Mutate Drift Baseline State

Evidence:

- `errander/agent/vm_graph.py` `drift_baseline_node` calls `baseline_store.compare_and_save` even when `dry_run` is true.

Impact:

Strict dry-run should not mutate operational baseline state unless explicitly documented as a read-model update. This can hide real drift later.

Required fix:

Separate read-only comparison from baseline update. If baseline update is intentional, require explicit operator action.

## Lower Severity Findings

### P2-1: Risk Taxonomy Is Inconsistent

Evidence:

- `errander/models/actions.py` marks `BACKUP_VERIFY` as `HIGH`.
- The backup verification implementation is read-only.
- `DOCKER_PRUNE` is marked `MEDIUM`, while docs describe it as low-risk.

Impact:

Approval policy and user expectations may not match actual risk.

Required fix:

Create a risk taxonomy that considers reversibility, blast radius, data deletion risk, command mutability, and environment tier.

### P2-2: `/metrics` And `/health` Are Not Protected By The UI Auth Middleware

Evidence:

- `errander/observability/metrics.py` protects `/ui` routes with Basic Auth.
- `/metrics` and `/health` remain unauthenticated.

Impact:

This may be acceptable on loopback, but is weak if bound to a broader interface in enterprise deployments.

Required fix:

Gate metrics and health exposure by deployment mode, bind address, or separate auth/network policy.

### P2-3: One Connectivity Helper Bypasses Known Hosts

Evidence:

- `errander/execution/ssh.py` `SSHConnectionManager` has strict host key behavior by default.
- `check_connectivity` uses `known_hosts=None`.

Impact:

The core manager is safer, but this helper is a latent trust-on-first-use/insecure path if adopted elsewhere.

Required fix:

Make all SSH entry points follow the same host key policy.

## Good Engineering Decisions Found

The review was strict, but not everything is bad. These parts are worth keeping:

- The LLM does not receive direct SSH/tool execution capability.
- Execution is routed through deterministic subgraphs and SSH managers.
- Critical risk actions are blocked by validation policy.
- Kernel package handling and package-hold logic exist for patching safety.
- LLM JSON parsing falls back safely instead of crashing the orchestrator.
- Audit logging has fail-closed intent.
- Basic UI auth and CSRF protection exist for `/ui`.
- SRE signal stores for incidents, changes, maintenance windows, and baselines are wired into the graph.
- The repository has a meaningful test suite, even though the local run did not finish cleanly.

## AI-Specific Assessment

The system is currently more "workflow automation with an LLM ranking step" than "senior autonomous AI SRE."

A real enterprise-grade AI SRE agent should have:

- explicit evidence gathering before proposing remediation
- causal hypothesis generation
- deterministic safety policy evaluation
- confidence and uncertainty reporting
- exact dry-run artifact generation
- human approval over exact actions
- immutable apply artifact
- postcondition verification
- rollback verification
- incident/change/maintenance-window memory
- evals proving it behaves safely under edge cases

This project has pieces of that architecture, but the implementation does not yet enforce the full loop.

## Recommended Remediation Plan

### Phase 1: Stop-The-Line Fixes

Do not allow live production apply until these are fixed:

1. Persist immutable approved plan artifacts.
2. Make deferred execution apply the exact approved artifact.
3. Remove all command failure masking.
4. Add first-class rollback outcome states.
5. Ensure action params in approval equal action params in execution.
6. Make patching preflight checks fail closed on unknown lock state.
7. Produce clean CI evidence for pytest, ruff, and mypy.

### Phase 2: Production Safety Hardening

1. Add state snapshot and drift detection between approval and apply.
2. Add exact package/version/command lists to dry-run plans.
3. Add per-step execution records and postcondition checks.
4. Add environment-tier policy controls.
5. Add authenticated or network-restricted metrics/health endpoints.
6. Add config tests proving approval timeout and policy settings are honored.

### Phase 3: Make The AI Worth The Claim

1. Require structured AI reasoning artifacts.
2. Add AI safety evals with known-bad scenarios.
3. Add model output regression tests.
4. Add confidence thresholds and mandatory human review triggers.
5. Add incident memory and change-correlation reasoning.
6. Add operator-facing explanations that cite evidence, not just action types.

## Questions I Would Grill The Team On

1. When an operator approves a dry-run, are they approving exact commands and package versions, or only broad action categories?
2. Where is the approved plan persisted for deferred execution?
3. Why does the window opener start a new forced live batch instead of applying the original approved artifact?
4. Why do cleanup and prune actions report success without propagating command failures?
5. Why do apt/dnf upgrade scripts end with unconditional `true`?
6. What state distinguishes rollback succeeded from rollback failed?
7. Why are approved params not passed into disk cleanup, log rotation, and Docker prune runners?
8. Where are the AI evaluation results proving safe behavior in dangerous live scenarios?
9. What was the last CI run where pytest, ruff, and mypy all passed?
10. What production incident would this system be allowed to remediate without a human, and what evidence proves that is safe?

## Final Call

The developers built a useful and ambitious foundation, but they did not finish a production-grade autonomous AI SRE agent.

The most dangerous gap is not cosmetic. It is architectural: approval, deferred execution, and live apply are not bound to an immutable, fully specified remediation artifact. Until that is fixed, this system should not be trusted to make live infrastructure changes autonomously.

## Second-Pass Validation After Claimed Fixes

Validation date: 2026-05-14  
Validated commit: `20fd374 fix: address AI SRE audit v2 - P0/P1/P2 safety findings`

### Summary

The claimed fixes are mostly present in code. The project is safer than it was during the first audit pass. However, the two architectural production blockers remain open:

1. P0-1: approved plan artifact is still not an immutable, exact execution artifact.
2. P0-2: deferred execution still needs proof that it applies the exact approved artifact instead of re-planning.

I would upgrade the short-term verdict from "unsafe prototype" to "improved prototype with several tactical safety fixes." I would not upgrade it to production-ready autonomous AI SRE.

### Findings Rechecked

| Finding | Second-pass status | Evidence |
|---|---:|---|
| P0-3 execute failures masked | Mostly fixed | `docker_prune.py`, `disk_cleanup.py`, and `log_rotation.py` now propagate failed command status in live mode. `commands.py` apt/dnf upgrade wrappers capture package-manager exit codes instead of ending with unconditional success. Residual edge: live `log_rotation.execute_node` can still return success when `logrotate` fails and there are no fallback `large_files`. |
| P0-4 rollback state unreadable | Fixed | `ActionStatus` now includes `ROLLED_BACK` and `ROLLBACK_FAILED`; `patching.rollback_node` returns those states. |
| P1-3 package lock probe fails open | Fixed | `validate_no_pkg_lock` now blocks live patching when the lock probe fails and only stays permissive in dry-run. |
| P1-5 dry-run mutates drift baseline | Fixed | `drift_baseline_node` skips `compare_and_save` while `dry_run=True`. |
| P2-3 connectivity helper TOFU | Partially fixed | `check_connectivity` now warns when using TOFU. It does not fully follow `SSHConnectionManager` policy because it has no `strict_host_keys` path that refuses connection when no known-hosts file is configured. |
| P2-1 risk taxonomy | Fixed | `BACKUP_VERIFY` is now `LOW`, matching its read-only behavior. |
| P1-1 approved params ignored | Fixed with type-safety caveat | VM runners now pass disk cleanup, log rotation, and Docker prune params into subgraphs. Mypy still flags the params extraction as unsafe object-to-dict typing. |
| P1-4 approval timeout ignored | Fixed | `approval_timeout_seconds` and `approval_poll_interval_seconds` are now passed into `await_dual_approval`. |
| Static/test gate claim | Not fixed | Targeted tests around the fixed areas mostly pass, but full local verification is still not clean in this environment. Ruff still reports 629 errors. Mypy still reports 142 errors in 18 files. |

### Verification Performed

Targeted commands that passed:

- `pytest tests\safety\test_validators.py`: 31 passed
- `pytest tests\agent\subgraphs\test_disk_cleanup.py tests\agent\subgraphs\test_log_rotation.py tests\agent\subgraphs\test_docker_prune_scope.py`: 63 passed
- `pytest tests\agent\test_vm_graph_drift.py`: 22 passed

Targeted command with local environment errors:

- `pytest tests\chaos\test_fault_injection.py`: code tests mostly ran, but two tests using `tmp_path` errored in this Windows workspace and pytest then crashed while cleaning the base temp directory with `PermissionError`.

Static verification:

- `ruff check errander tests`: failed with 629 errors.
- `mypy errander`: failed with 142 errors in 18 files.

### Updated Auditor Position

The developers did fix several important tactical safety bugs. That deserves credit.

But the production-go/no-go answer remains no. The system still lacks the core enterprise safety contract: an approved dry-run artifact must be the exact immutable artifact applied later, including commands, versions, target state, drift proof, rollback plan, and expiry. Until P0-1 and P0-2 are closed, this should not autonomously modify live VM infrastructure.

## Third-Pass Validation

Validation date: 2026-05-14  
Validated commit: `01d8b3d fix: address AI SRE audit v2 second-pass residuals`

### What Changed Since Second Pass

The new commit addresses the two residual tactical issues from the second-pass review:

- `log_rotation.execute_node` now tracks a failed `logrotate` call and only clears that failure when per-file fallback rotation actually succeeds.
- `check_connectivity` now has `strict_host_keys=True` by default and refuses to connect without a known-hosts file, matching the safer `SSHConnectionManager` behavior.
- VM action parameter extraction is more defensive: it only converts `params` to a dict when the raw value is actually a dict.

### Test Verification

Focused verification passed:

- `pytest tests\execution\test_ssh.py tests\agent\subgraphs\test_log_rotation.py tests\agent\subgraphs\test_disk_cleanup.py tests\agent\subgraphs\test_docker_prune_scope.py`: 84 passed

Full suite verification:

- First sandboxed full run failed because the sandbox could not create `C:\tmp\errander-audit-v4-full`.
- Rerun outside the sandbox succeeded:
  - `1307 passed`
  - `111 skipped`
  - `1 warning`

This means the earlier pytest concern is now cleared for this environment. The user's reported "1305 tests pass" is slightly stale; the current observed count is 1307 passing tests.

### Static Gates

Static quality gates are still not clean:

- `ruff check errander tests --statistics`: failed with 631 errors.
- `mypy errander`: failed with 112 errors in 18 files.

Mypy improved from the prior 142 errors, but the project still cannot honestly claim strict clean static typing.

### Remaining Production Blockers

P0-1 and P0-2 remain open.

Evidence for P0-1:

- `generate_plan_artifact_node` still hashes only `batch_id`, `env_name`, and `vm_plans`.
- The plan still does not include exact live commands, exact package versions, dry-run command outputs, pre-apply state snapshots, postcondition checks, rollback instructions, or expiry.
- `verify_plan_hash_node` recomputes the hash from the same in-memory plan structure. This validates local state stability, not infrastructure drift or exact approved-command integrity.

Evidence for P0-2:

- `DeferredExecutionStore` still stores only batch/window approval metadata.
- The deferred table has no plan artifact body and no full plan hash field.
- `_window_opener` still calls `run_env_batch(... dry_run=False, force=True ...)`, which starts a fresh live run instead of applying an approved immutable artifact.

### Updated Final Position

The tactical SRE safety bugs I flagged are now substantially fixed and tested. That is good work.

The architectural verdict is unchanged: this is still not an enterprise-ready autonomous AI SRE for live production apply. The system needs immutable approved execution artifacts and exact deferred-artifact application before it should be trusted to change production VMs autonomously.

## Fourth-Pass Validation

Validation date: 2026-05-14  
Validated commit: `26f3f2d feat: enforce HITL guardrails while P0-1/P0-2 deferred`

### What Improved

This commit addresses the operating-posture concern raised after the dev team's explanation.

Good changes found:

- `approval_gate_node` now has `require_live_approval=True` by default.
- When `require_live_approval=True`, all live risk tiers require human approval regardless of `relaxed`, `moderate`, or `strict` policy.
- `EnvironmentSchema.approval_policy` now defaults to `strict` instead of `moderate`.
- Deferred window execution now marks the run as `is_deferred_reapproval=True`.
- `_window_opener` now describes deferred execution as fresh re-planning plus fresh human re-approval, not as replaying the original approval.
- Slack approval text now warns operators that they are approving action categories and parameters, not exact pinned commands or package versions.
- `docs/SPEC.md` now includes a current-implementation note admitting that exact commands, dry-run output, and rollback commands are P0-1 target fields, not current fields.

This is the correct direction. The project is now much more honest about its current safety boundary.

### Verification

Focused guardrail tests:

- `pytest tests\agent\test_plan_apply_flow.py tests\config\test_schema.py`: 55 passed

Full suite:

- `pytest tests -q -p no:cacheprovider --basetemp=C:\tmp\errander-audit-v5-full --tb=short`: 1308 passed, 111 skipped, 1 warning

Static gates:

- `mypy errander`: still failed with 112 errors in 18 files.
- `ruff check errander tests --statistics`: still failed with 632 errors.

### Remaining Guardrail Gaps

The HITL posture is much better, but I still found three issues to fix before I would call the guardrail complete.

1. Approval gate can still fail open when no approval manager is supplied.

Evidence:

- `approval_gate_node` only waits for approval when `max_tier in _approval_tiers and approval_manager is not None`.
- If `require_live_approval=True` but `approval_manager is None`, execution falls through to the `else` branch and sets `approved=True`.

Normal `main.py` creates an `ApprovalManager`, so the standard CLI/server path is probably protected. But this is still a bad lower-level invariant. A required approval with no approval mechanism must fail closed, not auto-approve.

Required fix:

- If `not dry_run`, `require_live_approval=True`, and `approval_manager is None`, return `approved=False` with an explicit error.
- Add a test proving live approval fails closed when the approval manager is unavailable.

2. `autonomous_live_apply_enabled` is currently a declared setting, not an enforced product gate.

Evidence:

- `Settings` declares `autonomous_live_apply_enabled=False`.
- I found no runtime enforcement using this setting.

Required fix:

- Either wire it into live approval logic or remove it until it is real.
- If kept, it should explicitly prevent disabling `require_live_approval` until P0-1/P0-2 are implemented.

3. `require_live_approval` is declared but not clearly configurable through schema/loading.

Evidence:

- `Settings` declares `require_live_approval=True`.
- `AgentSettingsSchema` does not include it.
- `load_settings` does not load an env/YAML/DB override for it.

This is not dangerous right now because the default is safe. But the comment says an operator can explicitly set it false, while the loader does not appear to support that.

Required fix:

- Preferably keep it hardcoded true until P0-1/P0-2 are done.
- If configurability is intended, add schema/env loading, audit the override, and block it unless `autonomous_live_apply_enabled` is safely implemented.

### Updated Position

This latest commit makes the project acceptable as a pre-production HITL automation assistant, assuming operators understand that approvals cover action categories and parameters, not exact command replay.

It is still not an autonomous production SRE agent. P0-1 and P0-2 remain the production autonomy gate.

Before using live apply, I would require one more small guardrail fix: make required approval fail closed if no approval manager is available. That is a simple but important correctness fix.

## Fifth-Pass Validation

Validation date: 2026-05-14  
Validated commit: `cc1c468 fix: close fourth-pass guardrail gaps - fail-closed approval, enforce autonomous gate`

### Guardrail Findings Rechecked

The three fourth-pass guardrail gaps are now closed in code.

1. Fail-closed when no approval manager is configured: fixed.

Evidence:

- `approval_gate_node` now returns `{"approved": False, "error": "live approval required but no approval manager configured"}` when a live batch requires approval and `approval_manager is None`.
- This prevents the previous silent auto-approve fallthrough.

2. `autonomous_live_apply_enabled` is now enforced: fixed.

Evidence:

- `approval_gate_node` now accepts `autonomous_live_apply_enabled`.
- If `autonomous_live_apply_enabled=False` and a caller tries `require_live_approval=False`, the node logs a warning and forces `require_live_approval=True`.
- `build_batch_graph` passes both settings into the approval gate.

3. `require_live_approval` is intentionally not configurable: accepted.

Evidence:

- `Settings` documents `require_live_approval` as hardcoded true and not loadable from settings YAML or environment variables until P0-1/P0-2 are implemented.
- No loader path was found for disabling it through normal config.

This is a good guardrail posture for a HITL-first pre-production system.

### Verification

Focused guardrail tests:

- `pytest tests\agent\test_plan_apply_flow.py tests\agent\test_graph.py`: 60 passed

Full suite:

- `pytest tests -q -p no:cacheprovider --basetemp=C:\tmp\errander-audit-v6-full --tb=short`: 1310 passed, 111 skipped, 1 warning

Static gates:

- `mypy errander`: still failed with 112 errors in 18 files.
- `ruff check errander tests --statistics`: still failed with 641 errors.

### Updated Production Readiness Position

The HITL guardrail posture is now acceptable.

Current safe positioning:

- Supervised agentic AI SRE platform
- HITL SRE automation assistant
- Agentic SRE workflow engine with human approval gates

Still not acceptable:

- Fully autonomous production SRE
- Autonomous live production remediation without human approval

Reason:

P0-1 and P0-2 remain open. The system still does not have immutable approved execution artifacts or exact deferred artifact replay.

Final current verdict:

The project is now much safer and more honest. It can be evaluated as a pre-production, HITL agentic SRE automation system. It should not be marketed or operated as a fully autonomous production SRE until P0-1/P0-2 and static quality gates are closed.

## Owner Intent And Usefulness Note

Added context from project owner on 2026-05-15:

The owner is not primarily trying to sell this as a product. The goal is to build something genuinely useful for the community and eventually useful enough to push to internal production teams. The key concern is whether the project is useful at all in its current direction, or whether it is just a dressed-up Python automation script.

Auditor response:

This project is useful if it is scoped honestly.

It should not currently be framed as a complete SRE replacement or a fully autonomous production SRE. That would overstate the system and invite justified skepticism from experienced operators.

It is useful as a supervised Linux fleet maintenance agent that reduces repetitive operational toil around:

- patching
- package lock detection
- disk cleanup
- log rotation
- Docker cleanup in controlled environments
- backup verification
- maintenance windows
- human approval
- audit trails
- rollback and rollback verification
- service health regression checks

That is a real problem space. Many teams still maintain mutable Linux VMs manually or with fragile cron scripts. A safer, auditable, HITL maintenance agent can help those teams.

The strongest community positioning is:

> A supervised Linux fleet maintenance agent with approval, audit, rollback, and safety gates.

The stronger future direction is not to support every app directly. The better direction is:

- be OS/fleet-maintenance strong first
- be app-aware enough to avoid unsafe actions
- integrate with existing observability tools
- add narrow runtime/action packs only when they have proper detect/plan/approve/execute/verify/audit flows

For internal production use, the recommended adoption path is staged:

1. Run read-only discovery and reporting only.
2. Run dry-run maintenance plans only.
3. Enable HITL live apply in a dev environment.
4. Enable HITL live apply on a small non-critical production VM group.
5. Require approval, audit export, rollback verification, and post-maintenance health checks.
6. Do not enable autonomous live apply until immutable plan artifacts and exact deferred replay are implemented.

Final note:

The project is not useless. It is useful if the team resists the temptation to call it more than it is. The right near-term mission is not "replace SREs." The right mission is "remove boring, risky, repetitive Linux maintenance toil while keeping humans in control."

## AI Versus Python Automation

Added context from project owner on 2026-05-15:

The owner keeps receiving the question: why can this not just be a good Python automation script? Why does it need AI?

Auditor answer:

For the current safety-critical execution path, AI is not required and should not be trusted.

Patching, disk cleanup, log rotation, Docker prune, backup verification, sudo preflight, maintenance windows, approvals, audit logs, health checks, and rollback should all be deterministic Python automation.

That is a feature, not a weakness.

The correct internal answer is:

> We do not need AI to run `apt-get`. We need AI to reduce the human cognitive toil around deciding, explaining, prioritizing, correlating, and reviewing fleet maintenance.

The execution engine should remain deterministic. The AI layer should help with:

- prioritizing which VMs need attention first
- explaining why a maintenance batch matters
- summarizing results for operators
- correlating weak signals across audit, drift, disk, failed logins, package state, and service health
- producing post-maintenance reports
- helping humans choose the next action after a failure
- future CVE-aware prioritization

What the AI must not do:

- invent shell commands
- bypass approval
- override deterministic safety gates
- silently remediate production issues
- turn noisy metrics into noisy alerts with confident language

Best positioning:

> Deterministic maintenance automation with an AI-assisted operator layer.

This is different from a plain Python script because the product combines deterministic execution with:

- HITL approval
- auditability
- rollback verification
- fleet-level planning
- risk gates
- drift and preflight checks
- human-readable summaries
- signal correlation

The AI is useful when it helps humans understand and prioritize. It is dangerous when it becomes the execution authority.

## Proactive SRE Direction

The owner wants Errander to become proactive, not only reactive. Example idea: have AI look at CPU or memory trends for the last 24 hours and send the team a heads-up.

Auditor answer:

Yes, that direction makes sense, but it must be implemented carefully.

Naive proactive alerting is worse than no alerting. A message like "CPU was high today" is not SRE value. SRE value is:

> This VM is trending toward a concrete risk, here is the evidence, here is the likely impact, here is the recommended human action, and here is why the system is not auto-remediating.

### Useful Proactive Signals

Start with signals that are actionable and aligned with the current maintenance product:

1. Disk growth forecast

This is the best first proactive feature.

Examples:

- root disk grew from 68% to 81% in 24 hours
- forecast to hit 90% in 18 hours
- top known cleanup candidates: journal, apt cache, Docker dangling images
- recommended action: approve disk cleanup batch

2. Memory leak suspicion

More useful than generic memory alerting.

Examples:

- memory usage increased steadily for 18 hours
- swap started increasing
- service restarts or OOM logs detected
- recommended action: investigate service, do not auto-restart unless a runbook exists

3. CPU saturation trend

Useful only when correlated with service impact.

Examples:

- CPU above 85% for 4 hours
- load average above core count
- request latency or error rate also increased
- recommended action: scale, investigate top process, or check recent deploy

CPU alone is often noisy. CPU plus service SLI degradation is useful.

4. Patch/CVE urgency

Very SRE-aligned.

Examples:

- public-facing VM has vulnerable OpenSSL/nginx package
- CVE severity is high
- package is patchable without kernel update
- recommended action: prioritize this VM in next maintenance window

5. Maintenance readiness risk

Before a scheduled maintenance window, proactively warn:

- sudo preflight will fail
- package lock is present
- disk too full to patch safely
- backup stale
- service already unhealthy
- host drift detected

This is extremely useful because it prevents failed maintenance windows.

6. Backup freshness and restore confidence

Examples:

- backup path has not changed in 3 days
- last backup verification failed
- production VM has no recent backup before patching
- recommended action: block live maintenance until backup is verified

7. Drift and security posture

Examples:

- sudoers changed since last baseline
- new SSH key appeared
- new listening port opened
- failed login rate increased
- recommended action: require human security review

### What To Build First

Recommended proactive MVP:

1. Prometheus connector

Do not build a monitoring system. Integrate with the one teams already use.

Start with Prometheus because it is common, open, and simple to query.

2. Deterministic trend engine

The trend engine should calculate:

- current value
- 24-hour min/max/average
- slope
- forecast to threshold
- confidence
- evidence points
- affected VMs

The LLM should not calculate raw math. Python should.

3. Proactive daily digest

Start with a daily Slack digest, not paging.

Example:

```text
Errander proactive maintenance digest

3 VMs need attention:

1. prod-web-02
   Disk / is 84%, growing 2.1% per hour.
   Forecast: 90% in ~3 hours.
   Suggested action: approve disk cleanup.

2. prod-api-01
   Memory has grown steadily for 20 hours; swap started 2 hours ago.
   No maintenance action recommended automatically.
   Suggested action: investigate service memory leak.

3. prod-worker-04
   Sudo preflight would fail for logrotate.
   Suggested action: fix sudoers before maintenance window.
```

4. Pre-maintenance risk report

Before a maintenance window, generate:

- which VMs are ready
- which VMs should be skipped
- what needs fixing before the window
- what approval is needed

5. Post-maintenance verification with metrics

After maintenance, query Prometheus:

- CPU
- memory
- disk
- service up/down
- request errors
- latency if available

Then report regressions.

### Recommended Data Model

Add a generic proactive signal object:

```text
ProactiveSignal
  signal_type: disk_growth | memory_leak | cpu_saturation | cve_exposure | backup_stale | drift_risk
  vm_id
  severity: info | warning | critical
  window: 24h
  evidence: structured metrics/logs/audit facts
  forecast: optional
  confidence: low | medium | high
  recommended_action
  auto_action_allowed: false by default
```

The LLM can summarize this object. It should not be the source of truth.

### Architecture Direction

Use adapters:

- Prometheus adapter for metrics
- ELK/OpenSearch adapter for logs later
- audit DB adapter for Errander's own history
- CVE source adapter later

Keep the core model stable so each adapter produces structured signals.

Pipeline:

```text
collect telemetry -> calculate deterministic signals -> rank/prioritize -> AI summary -> Slack/UI digest -> human-approved action
```

### Guardrails For Proactive Mode

Proactive mode must have strict guardrails:

- No auto-remediation from trend alerts by default.
- Deduplicate alerts so the same VM does not spam Slack.
- Require evidence in every message.
- Include confidence and recommended human action.
- Allow silencing/maintenance windows.
- Keep all AI summaries traceable to raw facts.
- Never let LLM-generated text become an execution command.

### Better Product Direction

To become more SRE-like, Errander should not add random Tomcat, Nginx, Ansible, Kafka, and Kubernetes commands.

The better direction is:

1. Be excellent at Linux fleet maintenance.
2. Become app-aware through discovery and health checks.
3. Integrate with external observability tools.
4. Add controlled action packs only when they have:
   - detect
   - plan
   - approve
   - execute
   - verify
   - audit
   - rollback or escalation

For example:

- Docker pack: safe prune, image pressure, daemon health
- Kubernetes node pack: cordon/drain readiness, kubelet health, image GC signals
- Nginx/Tomcat pack: health verification, not full app management
- CVE pack: patch priority and exposure reasoning

### Final Recommendation To Engineering

Errander can become proactive in a useful way.

Do not start by letting AI stare at CPU and memory graphs and chat about them. Start by building deterministic proactive signals, then let AI explain and prioritize those signals for humans.

First proactive milestone:

- Prometheus integration
- disk growth forecast
- memory leak suspicion
- CPU saturation only when correlated with service health
- pre-maintenance readiness report
- Slack daily digest

This makes Errander meaningfully more than a Python automation script while keeping the execution model safe.

## Observability Integration Position

Added context from project owner on 2026-05-15:

Question: What if an environment does not have Prometheus? Can we still say the solution becomes better when integrated with Prometheus and ELK?

Auditor answer:

Errander must not require Prometheus, ELK, OpenSearch, Splunk, Datadog, or any external observability stack to be useful.

The default should remain:

> Native SSH checks plus Errander's own historical snapshots.

Prometheus and ELK-style integrations should be optional evidence adapters that make the system stronger when available.

### Correct Statement

Recommended wording:

> Errander works with native SSH-based checks out of the box, but becomes significantly more powerful when integrated with existing observability platforms like Prometheus and ELK/OpenSearch. These integrations let Errander correlate maintenance actions with real service metrics and logs, enabling stronger pre-maintenance risk detection, post-maintenance verification, and proactive recommendations.

Even shorter:

> Prometheus and ELK tell us what is happening. Errander uses that evidence to plan, explain, approve, execute, and verify maintenance safely.

### What Not To Say

Avoid:

- "Errander requires Prometheus."
- "Errander replaces Prometheus."
- "Errander replaces ELK."
- "Errander is better than your monitoring stack."

The right posture is:

> Errander is not a monitoring system. Errander is a supervised maintenance and remediation workflow agent that can use monitoring and log systems as evidence sources.

### Fallback Without Prometheus

If there is no Prometheus, Errander should still collect useful signals via SSH-native probes:

- CPU/load: `/proc/loadavg`, `uptime`, optionally `mpstat`
- memory: `/proc/meminfo`, `free`
- disk: `df`, selected `du`
- service status: `systemctl is-active`
- failed logins: `journalctl` or auth log fallback
- Docker pressure: Docker wrapper assess command when enabled
- package state: apt/dnf queries
- sudo readiness: sudo preflight
- drift: sudoers, SSH keys, listening ports, scheduled jobs

Errander should store these snapshots in its own database. That allows basic trend detection even without external observability.

Examples:

- root disk was 71 percent three runs ago, 78 percent yesterday, 84 percent today
- sudo preflight failed before the maintenance window
- package locks are repeatedly blocking patching on the same VM
- service health was already degraded before maintenance

This is enough for a useful readiness report and basic proactive digest.

### With Prometheus

Prometheus improves time-series quality.

Use it for:

- 24h/7d CPU and load trends
- memory growth and swap trends
- disk forecast using high-resolution samples
- service-level metrics such as latency, error rate, saturation
- post-maintenance regression detection
- correlation between maintenance and service impact

Prometheus should feed structured facts into Errander. The LLM should summarize those facts; it should not invent metric conclusions.

### With ELK/OpenSearch

ELK/OpenSearch improves log-based evidence.

Use it for:

- error spikes after maintenance
- repeated service crash messages
- OOM events
- authentication failures
- package manager errors
- application-specific warning patterns

Again, the log adapter should produce structured findings first. The LLM should explain and prioritize them.

### Suggested Config Model

Recommended shape:

```yaml
observability:
  provider: ssh_native
```

or:

```yaml
observability:
  provider: prometheus
  prometheus_url: http://prometheus:9090
```

Future:

```yaml
logs:
  provider: opensearch
  url: https://opensearch.example.com
```

Adapters can be added later:

- `ssh_native`
- `prometheus`
- `cloudwatch`
- `azure_monitor`
- `datadog`
- `elk`
- `opensearch`
- `splunk`

### Engineering Principle

Pipeline:

```text
collect evidence -> calculate deterministic signals -> rank/prioritize -> AI summary -> human decision -> deterministic action -> verification
```

External observability enriches the evidence. It must not become a hard dependency.

### Final Position

Errander should be useful for teams with no monitoring stack and better for teams with one.

The right claim is:

> Errander runs with native SSH checks by default. When connected to Prometheus and ELK/OpenSearch, it becomes more proactive and more reliable because it can correlate maintenance plans with real metrics and logs.

This is a strong and honest architecture statement.

## Two-Layer AI Architecture Validation

Added context from project owner and engineering team on 2026-05-15:

The team proposed a two-layer AI architecture:

- Layer A: Operator Assistant
- Layer B: Safe Execution Engine

Auditor validation:

I agree with the model, with strict boundaries.

This is the right way to make Errander more agentic without destroying the safety story.

### Approved Model

Layer A: Operator Assistant

Purpose:

- investigate
- explain
- summarize
- correlate
- prioritize
- recommend
- answer operator questions
- draft maintenance proposals

Allowed capabilities:

- LLM reasoning
- MCP tools
- CLI tools
- Skills
- Prometheus queries
- ELK/OpenSearch/Splunk searches
- CVE lookups
- Slack context
- GitHub/commit context
- CMDB/service ownership lookup
- Errander audit DB queries
- runbook/document retrieval

Hard restriction:

Layer A must never directly mutate infrastructure.

It may produce:

- text answers
- evidence summaries
- risk explanations
- proposed maintenance batches
- recommended next human actions

It must not execute:

- SSH commands
- package installs
- docker prune
- kubectl drain
- service restarts
- file deletes
- sudo commands
- rollback commands

Layer B: Safe Execution Engine

Purpose:

- deterministic maintenance execution
- HITL approval
- risk gates
- sudo preflight
- whitelisted actions
- audit logging
- rollback
- post-action verification

Allowed capabilities:

- deterministic Python
- fixed action subgraphs
- approved command builders
- configured SSH executor
- policy validators
- rollback handlers
- audit stores

Hard restriction:

No LLM, MCP, arbitrary CLI tool calls, or free-form tool execution in Layer B.

Layer B is the production safety boundary.

### Handoff Contract

The handoff between Layer A and Layer B must be explicit and typed.

Layer A may submit a proposal such as:

```text
MaintenanceProposal
  environment
  target_vms
  proposed_actions
  evidence
  risk_summary
  reason
  confidence
```

Layer B must treat this as untrusted input.

Layer B must:

- validate target VMs
- re-run deterministic discovery
- re-evaluate action eligibility
- apply policy gates
- generate its own plan artifact
- request human approval
- execute only known action types
- audit everything

Layer A recommendations are advisory. They are not executable authority.

### Required Safety Rules

1. Layer A cannot call Layer B execution APIs directly.

There should be no code path where the Operator Assistant can invoke live execution.

Allowed:

- "create proposal"
- "show suggested batch"
- "open approval draft"

Not allowed:

- "run batch"
- "approve batch"
- "execute command"

2. Layer A outputs must be schema-validated.

Do not pass raw LLM text into the execution planner.

Use strict schemas:

- known action types only
- known environments only
- known VM IDs only
- no shell command fields
- no arbitrary arguments

3. Layer B must independently verify all facts.

If Layer A says "VM has Docker," Layer B must still confirm Docker through deterministic discovery.

If Layer A says "disk is high," Layer B must still run its own disk check or use trusted telemetry adapter output.

4. Human approval remains mandatory for live changes.

The Operator Assistant cannot approve its own recommendation.

5. Audit must record the AI role.

Audit should distinguish:

- AI recommended
- human approved
- deterministic engine executed
- verification result

This is important for trust and post-incident review.

6. Tool access in Layer A must still be bounded.

"Full ecosystem access" should not mean uncontrolled access to every connector in production.

Layer A should have a tool allowlist per deployment:

- which MCP servers are enabled
- read-only versus write-capable tools
- query limits
- timeout limits
- redaction rules
- tenant/environment boundaries

Recommendation:

Layer A should default to read-only tools.

7. Layer A cannot use write-capable external tools without separate review.

Examples of risky tools:

- GitHub write access
- Slack posting in incident channels
- Jira ticket mutation
- cloud provider mutation
- Kubernetes write APIs
- shell/SSH tools

These should be excluded from Phase D unless separately designed and approved.

### Phase Ordering

The proposed order is mostly right, but I would phrase it as:

Phase A: Privilege and setup correctness

- sudo model
- Docker wrappers
- target readiness checks
- privilege preflight tests

Phase B: Deterministic proactive signals

- native SSH snapshots
- signal catalog
- disk growth forecast
- memory growth suspicion
- maintenance readiness report

Phase C: Deterministic observability adapters

- Prometheus direct adapter
- ELK/OpenSearch direct adapter
- structured evidence ingestion
- post-maintenance verification

Phase D: Operator Assistant

- LLM-driven investigation
- MCP/CLI/Skills ecosystem
- read-only tool access by default
- proposal generation only
- no execution authority

Do not build Phase D before Phase B is stable. Otherwise the assistant will have too little trusted internal evidence and will become a chat interface over weak data.

### MCP/CLI/Skills Position

Approved for Layer A.

Rejected for Layer B.

This is the key architecture line.

MCP is excellent for:

- investigation
- evidence gathering
- runbook lookup
- metrics/log exploration
- context enrichment

MCP is not acceptable for:

- production command execution
- sudo operations
- live VM mutation
- rollback
- approval bypass

The phrase to use with the team:

> MCP belongs in the operator brain, not in the execution hands.

### Product/Team Positioning

This two-layer model strengthens the answer to "why AI?"

The answer becomes:

> The execution engine is deterministic automation. The AI layer is an operator assistant that can investigate across tools, correlate evidence, and recommend safe maintenance plans. Human approval and deterministic execution remain the trust boundary.

This is credible to SREs.

### Required Implementation Checks For Phase D

Before implementing the Operator Assistant, require:

1. Tool registry with explicit allowlist.
2. Read-only tools only for first release.
3. Structured proposal schema.
4. No shell command field in proposals.
5. No direct call path from Layer A to live execution.
6. Audit log for every AI investigation and recommendation.
7. Evidence citations in AI summaries.
8. Timeout and cost controls for tool calls.
9. Redaction of secrets and credentials from prompts.
10. Tests proving Layer A cannot execute actions.

### Final Sign-Off

I approve the two-layer architecture with the above constraints.

Approved:

- Layer A can use LLM, MCP, CLI, and Skills for read-only investigation and recommendation.
- Layer B remains deterministic Python with HITL approval, audit, rollback, and verification.
- Layer A recommendations must go through Layer B's normal approval and execution pipeline.
- Phase D should come after deterministic proactive signals and observability adapters are stable.

Not approved:

- LLM-driven execution
- MCP/CLI tool calls in the live execution path
- AI-generated shell commands
- Operator Assistant approving or running its own recommendations
- write-capable external tools in Phase D without separate design review

This architecture is a good way to make Errander more powerful while preserving the safety boundary that makes the project trustworthy.
