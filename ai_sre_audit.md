# Errander-AI SRE/AI Audit

Date: 2026-05-10  
Auditor stance: hard production SRE review, AI-safety focused  
Scope: repository inspection only. No existing code was modified.

## Executive Verdict

This project is **not production-ready** and should **not be approved as a finished Autonomous AI SRE Agent**.

The developers have built a useful skeleton: Python package, LangGraph-shaped orchestration, SSH execution layer, action subgraphs, audit storage, metrics/UI, and a sizeable test suite. But the production-critical claims are overstated. The current implementation is closer to a scripted VM maintenance prototype with some AI-adjacent utilities than a safe autonomous SRE agent.

The biggest issue: the AI path is not actually wired into the main maintenance graph. The LLM client is constructed in `main.py`, but the VM planner calls `prioritize_actions(vm_info)` without passing the LLM client. So the advertised "AI decides what to prioritize" path is not used in normal execution.

## Approval Decision

Do **not** let this go live against real production VMs.

Minimum classification:

- Product status: **prototype / alpha**
- AI maturity: **low**
- SRE safety maturity: **not acceptable for production**
- Operational risk: **high**

## What They Did Right

- Clear module layout: `agent`, `execution`, `safety`, `observability`, `config`, `integrations`.
- Uses structured LLM parsing with Pydantic fallback behavior in `errander/integrations/llm.py`.
- Has hardcoded safety ideas for cleanup whitelists and kernel exclusion.
- Uses SSH key auth only, not passwords.
- Has basic locking, audit, metrics, web UI, deferred execution, rolling/canary concepts.
- Has many tests present. In this environment, 643 passed before temp-directory permission errors blocked full completion.

These are good foundations. They are not enough.

## Critical Findings

### 1. The AI decision path is not wired into production execution

Evidence:

- `main.py` builds an `LLMClient` at lines 233-248, but returns it unused at line 252.
- `run_env_batch()` calls `build_batch_graph()` without passing any LLM client.
- `plan_actions_node()` calls `actions = await prioritize_actions(vm_info)` at `errander/agent/vm_graph.py:177`, with no `llm_client`.

Impact:

The project claims AI-powered action prioritization, but the actual graph uses hardcoded fallback ordering. That is not an autonomous AI SRE agent. It is deterministic automation with unused LLM plumbing.

### 2. Dry-run/live execution is dangerously miswired

Evidence:

- `SandboxExecutor` is created once with `dry_run=settings.dry_run_default` in `errander/main.py:220`.
- The batch state gets a separate `"dry_run": dry_run` value in `errander/main.py:551-554`.
- Subgraphs decide whether to execute live based on `executor.dry_run`, for example `errander/agent/subgraphs/patching.py:243`.

Impact:

CLI state and executor state can disagree. `--live` can still simulate if the executor was created with default dry-run. Worse, if settings create a live executor, a supposedly dry-run graph can mutate VMs. This is a ship-stopper.

### 3. Approval happens after work has already run

The batch graph dispatches VM work, collects results, generates the report, then routes to approval.

Evidence:

- VM work runs before `generate_report`.
- `route_after_report()` only sends to approval after results exist: `errander/agent/graph.py:592-598`.
- `approval_gate_node()` handles approval at `errander/agent/graph.py:503-524`.

Impact:

For live runs, approval is too late. The system can execute first and ask later. That violates the advertised Terraform-style plan/apply model.

### 4. Dry-run approval is skipped

Evidence:

- `approval_gate_node()` auto-sets `approved = True` for dry-run at `errander/agent/graph.py:503-506`.

Impact:

The documented model says dry-run creates a plan, posts it, waits for approval, then executes live. Current behavior skips approval during dry-run and does not automatically execute the approved immutable plan inside the same safe flow.

### 5. Patching rollback is advertised but not implemented

Evidence:

- `errander/safety/rollback.py:56-64` explicitly says patching rollback is not yet implemented and returns failure.
- No agent code calls `rollback_action`; search only finds definitions and comments.

Impact:

This is one of the most serious mismatches. Patching VMs without tested rollback is not production-grade autonomous SRE. A failed package upgrade can strand a machine.

### 6. Strict/moderate/relaxed policies are mostly not enforced

Evidence:

- `requires_approval()` exists, but the graph does not use it.
- `validate_action()` has a `policy` argument, but its docstring says it is unused.
- Approval routing only checks max risk tier high/critical, not the environment's configured policy.

Impact:

Production `strict` policy does not mean what the docs imply. Medium-risk patching can be auto-executed in live mode without policy-based human approval.

### 7. Fleet failure threshold is documented but not used

Evidence:

- `validate_targets_node()` only partitions healthy/failed targets and returns both at `errander/agent/graph.py:231-234`.
- No threshold check exists there despite `fleet_failure_threshold` settings.

Impact:

If most hosts fail validation due to network, DNS, credential, or VPN issues, the batch still proceeds on whatever passed. That can be operationally unsafe.

### 8. OS verification is incomplete

Evidence:

- Target validation only runs `echo ok` at `errander/agent/graph.py:208-214`.
- `verify_os_match()` exists in `errander/execution/os_detection.py`, but is not used in the target validation path.

Impact:

Inventory can say Ubuntu while the machine is RHEL/Debian or something else. The system will discover OS later, but it does not enforce inventory correctness as claimed.

### 9. SSH host key verification is disabled

Evidence:

- `asyncssh.connect(... known_hosts=None ...)` in `errander/execution/ssh.py:91-97`.

Impact:

This accepts man-in-the-middle risk. A VPN lowers risk but does not remove it. For an agent with maintenance privileges, this is not production acceptable.

### 10. Shell command construction is unsafe

Examples:

- Backup path is interpolated unquoted into `stat`: `errander/agent/subgraphs/backup_verify.py:90`.
- Log paths and file paths are interpolated into `find`, `cp`, `gzip`, and `truncate`: `errander/agent/subgraphs/log_rotation.py:174-201`.
- Package names and exclusion patterns are interpolated into package-manager commands: `errander/execution/commands.py:77-91`.

Impact:

Any config/UI-sourced value that reaches shell commands can cause breakage or command injection. Even if only admins edit config, production SRE tooling must treat config as hostile input.

### 11. APT kernel exclusion logic is not robust

Evidence:

- `AptManager.upgrade_all()` builds `apt-mark hold linux-*` style commands from patterns at `errander/execution/commands.py:77-83`.

Impact:

`apt-mark hold` expects package names, not a reliable glob-based policy. Depending on shell behavior and installed package names, this can fail or provide false safety. Kernel exclusion must be based on parsed package names and explicit allow/deny filtering before upgrade.

### 12. Docker prune is risky and mismatched with docs

Evidence:

- Docker prune is documented as low risk, but `ACTION_RISK_TIERS` marks it medium.
- Implementation runs `docker system prune -af` at `errander/agent/subgraphs/docker_prune.py:165-171`.

Impact:

`-a` removes all unused images, not only dangling images. In production this can cause slow recovery or failed restarts if registries are unavailable. This needs per-host policy, image allowlists, and approval semantics.

### 13. Audit trail is not immutable and can silently lose events

Evidence:

- Audit writes are described as best-effort and swallowed after retry at `errander/safety/audit.py:102-136`.

Impact:

For compliance and production incident reconstruction, silently losing audit events is unacceptable. If audit is mandatory, the agent must fail closed or degrade explicitly, not continue silently.

### 14. UI/approval security is weak by default

Evidence:

- UI auth is disabled unless env vars are set.
- Server binds `0.0.0.0` at `errander/observability/metrics.py:1400`.
- Approval POST endpoints rely on optional Basic Auth and have no CSRF protection.

Impact:

On any reachable network, an unauthenticated or weakly protected approval UI can become an operations-control surface.

## AI-Specific Assessment

The project is not "mostly AI" today. The AI surface is:

- LLM client wrapper.
- Direct function tests for LLM parsing/fallback.
- Optional report-generation helper.
- Optional prioritization helper that is not passed into the real graph.

Missing for a real AI SRE agent:

- No LLM-in-the-loop production planner wired to graph execution.
- No model confidence scoring.
- No policy-constrained plan schema with explicit allowed action parameters.
- No prompt-injection or output-adversarial test suite.
- No historical learning from incidents or run outcomes.
- No incident detection from metrics/logs/traces.
- No RCA workflow.
- No remediation hypothesis ranking.
- No human-readable plan/apply artifact that is immutable and signed.
- No eval harness comparing LLM plans against golden SRE decisions.
- No model/version audit for each AI decision.

The current implementation is safer precisely because the LLM is not in control. But then the "Autonomous AI SRE Agent" claim is not true.

## Is LangGraph the Right Choice?

Short answer: **LangGraph is a reasonable architectural choice for this problem, but the current implementation does not use it correctly enough to justify the production claims.**

This kind of SRE system is naturally a state machine:

```text
discover VM -> inspect state -> plan actions -> validate safety -> dry-run
-> request approval -> execute live -> verify -> audit -> rollback/escalate
```

LangGraph is useful for this because it can model explicit workflow states, routing, retries, failure branches, human approval gates, and per-VM fan-out. For a high-risk SRE tool, that is better than a loose script or a single prompt-driven agent loop.

So the issue is **not** that they chose LangGraph. The issue is that the real graph wiring does not match the safety and AI story in the documentation.

The correct LangGraph design for this project should enforce:

1. Discovery and dry-run happen first.
2. A concrete immutable plan is produced.
3. Human approval happens before any live mutation for policy-controlled actions.
4. Live execution follows the approved plan exactly.
5. Drift between dry-run and live execution forces a new approval.
6. Rollback/escalation paths are real graph branches, not unused helper files.
7. AI recommendations are passed into the graph, validated by deterministic safety gates, and audited.

The current code has a LangGraph-shaped workflow, but several critical edges are wrong: approval is after execution for live runs, dry-run approval is skipped, rollback is not integrated, and the LLM is not passed into the main planner.

Verdict: **LangGraph is acceptable. The implementation is not yet acceptable.**

## Can This Be Called Agentic AI?

Not honestly in its current form.

To call this an **Agentic AI SRE**, the AI must do meaningful autonomous reasoning inside strict guardrails. At minimum, it should:

1. Observe current VM state.
2. Decide or recommend which actions are needed.
3. Produce a reasoned plan.
4. Select tools/actions through a controlled interface.
5. Respect policy, risk tiers, and maintenance windows.
6. Ask for approval where required.
7. Analyze failures and recommend retry, rollback, or escalation.
8. Record every AI decision, model, prompt context, and result in audit logs.

This project currently has some AI-adjacent pieces: an LLM client, structured JSON parsing, fallback logic, and report generation support. But the main production planner calls `prioritize_actions(vm_info)` without an LLM client, so the real workflow falls back to hardcoded action ordering.

That means the honest label today is:

> **LangGraph-based VM maintenance automation with optional LLM-assisted components.**

The dishonest or premature label is:

> **Autonomous Agentic AI SRE Agent.**

A future version can become agentic if the AI is properly wired into planning and failure analysis, while deterministic code keeps final authority over safety. The right principle is:

```text
AI may recommend. Policy and validators must decide what is allowed. Humans approve risky changes.
```

Until that is implemented and tested, do not let the team market this as a finished Agentic AI SRE product.

## Test Status

Commands attempted:

- `python -m pytest tests -q -p no:cacheprovider`: failed collection under system Python due missing dependencies.
- `.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`: 643 passed, 111 skipped, 124 errors caused by temp-directory permission failures.
- `.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp .pytest-tmp`: still failed during temp cleanup with Windows permission errors.

Important nuance: I did not get a clean full-suite result. The partial result proves many unit tests exist and many pass, but it does not prove release readiness.

Also, several critical issues above are architectural wiring issues that tests apparently do not catch: unused LLM client, approval after execution, rollback not integrated, dry-run/live mismatch.

## Production Readiness Checklist

Before approval, require these as non-negotiable:

1. Wire LLM client into graph planning and reporting, or remove AI claims.
2. Replace current dry-run/live split with one source of truth.
3. Implement true plan/apply: dry-run produces immutable plan, approval happens before live execution, live follows that exact plan.
4. Enforce policies per environment and VM.
5. Implement and test patch rollback, or disable live patching.
6. Add fleet failure threshold enforcement.
7. Enforce OS match against inventory.
8. Enable SSH host key verification.
9. Replace shell-string interpolation with safe quoting/escaping or structured command builders.
10. Make audit logging fail closed for live production actions.
11. Secure UI by default: auth required, bind localhost/private interface by config, CSRF protection for POST.
12. Add end-to-end staging tests against disposable VMs for Ubuntu, Debian, and RHEL.
13. Add AI evals: golden plans, bad LLM outputs, prompt injection, schema violations, unsafe recommendations.
14. Add chaos/failure tests: SSH drop mid-action, package manager lock, disk full, audit DB locked, Slack unavailable, LLM timeout.

## Bottom Line

The developers did not build an optimal finished solution. They built a promising scaffold with serious production gaps.

The most charitable read: this is an early engineering milestone.

The hard SRE read: calling this "over" is not acceptable. Do not approve production deployment until the critical items are fixed and verified with clean tests and real staging runs.

---

# Re-Audit After Dev Fixes - 2026-05-11

## Executive Verdict

The developers made meaningful progress. This is no longer the same weak prototype I reviewed earlier. They added a plan/apply flow, LLM wiring, policy gates, fleet abort logic, stricter SSH host-key behavior, scoped docker cleanup, rollback code for Debian patching, and focused tests.

However, I still would not approve this as a finished live Autonomous AI SRE Agent.

The biggest issue is architectural: the approved plan is not guaranteed to be the plan that gets executed. In an SRE system that can mutate real VMs, that is a hard blocker.

## What Improved

1. LangGraph usage is now more justified.
   - The project now has a clearer graph-level workflow: validation, planning, approval, plan hash check, wave dispatch, execution, health check, and report generation.
   - This is a better use of LangGraph than the previous implementation.

2. LLM is now partially wired into planning.
   - `prioritize_actions(...)` can now call an LLM client.
   - The code records prompt hash, model name, raw response, fallback reason, and selected actions into an AI decision store.
   - This is a real step toward explainable AI operations.

3. Pre-execution approval gate exists.
   - Approval now happens before dispatch, not after execution.
   - Risk tiers are checked against environment policy.
   - Maintenance-window checks can defer live execution.

4. Fleet safety improved.
   - `check_fleet_health_node` now enforces a fleet failure threshold and can abort the batch.

5. OS verification improved.
   - Target validation now reads `/etc/os-release` instead of using a meaningless `echo ok`.
   - Declared OS mismatch is now logged.

6. SSH host-key behavior improved.
   - Strict host-key mode exists.
   - If strict mode is enabled without known hosts, the system fails closed.

7. Rollback exists for one patching path.
   - Debian/Ubuntu package rollback was implemented using captured package versions.
   - The command builder validates package names and versions before constructing shell commands.

8. Focused tests passed.
   - Targeted tests for plan/apply, policy, docker prune scope, rollback, and command builder passed:
   - `92 passed`

## Remaining Blockers

### Blocker 1: Approved Plan Is Not What Gets Executed

This is the most important remaining flaw.

The batch graph creates a plan artifact and approval gate, but when it dispatches VM execution it sends:

```python
planned_actions=[]
```

That means the VM graph is free to rediscover and re-plan during execution.

Impact:

- The operator approves one plan.
- The execution graph can run a different plan.
- The plan hash only proves the in-memory plan artifact did not change.
- It does not prove the execution followed the approved artifact.

Required fix:

- The approved `vm_plans` must become immutable execution input.
- The VM execution graph must consume only the approved actions.
- Any runtime re-plan must produce a new plan hash and go back through approval.
- Tests must prove that execution cannot run an action not present in the approved artifact.

### Blocker 2: Approval Plan Does Not Use the LLM Path

The execution VM graph can use `llm_client`, but the batch-level `plan_vm_node` currently calls:

```python
actions = await prioritize_actions(vm_info)
```

It does not pass:

- `llm_client`
- environment policy
- batch ID
- VM ID
- AI decision store

Impact:

- The plan shown for approval may be generated by fallback logic.
- The later execution graph may use the LLM and produce a different action order.
- The AI audit trail does not fully represent the approved plan.

Required fix:

- Pass the same LLM client, policy, batch ID, VM ID, and AI decision store into batch-level planning.
- Store the AI decision record ID or decision hash inside the plan artifact.

### Blocker 3: Live Mode Is Still Explicitly Blocked

The CLI now blocks `--live` unless `--unsafe-legacy-live` is used.

That is a responsible safety choice, but it also means the developers cannot honestly claim the live autonomous workflow is complete.

Current honest status:

> Dry-run and planning workflow improved. Live autonomous remediation is still not production-ready.

Required fix:

- Remove the unsafe bypass only after plan/apply immutability, verification, rollback, and audit guarantees are proven by tests.

### Blocker 4: Dry-Run / Live Execution Semantics Are Still Inconsistent

Mutation commands now often pass per-call `dry_run`, which is good.

But many read-only assessment and verification commands still call `executor.execute(...)` without a per-call `dry_run` override. Because the executor default is often dry-run, these commands can return synthetic dry-run output instead of inspecting the real VM.

Impact:

- Dry-run assessments may not read real state.
- Live verification can accidentally behave like dry-run verification.
- A patch, disk cleanup, docker prune, or log cleanup can appear verified without real verification.

Required fix:

- Separate read-only command execution from mutating execution.
- Read-only assessment and verification should execute against the VM in both dry-run and live modes.
- Mutating commands should respect dry-run.
- Tests must assert that verification commands are real reads, not synthetic dry-run responses.

### Blocker 5: Patching Verification Failure Does Not Reliably Trigger Rollback

The patching subgraph added rollback, but verification failure returns an error without clearly transitioning to failed status and rollback.

Impact:

- A patch can execute.
- Verification can fail.
- The workflow may still report success-like status with an error field.

Required fix:

- Verification failure must set failed status.
- Verification failure after mutation must route to rollback.
- Tests must cover failed verification after a successful patch command.

### Blocker 6: Rollback Is Debian/Ubuntu Only

Rollback uses `apt-get` and `dpkg-query`.

Impact:

- This does not satisfy heterogeneous VM support if RHEL/CentOS/Amazon Linux are in scope.
- On RHEL-like systems, rollback either fails or is not implemented.

Required fix:

- Add DNF/YUM rollback strategy.
- If rollback is unsupported for an OS, the plan must mark that action as higher risk or block live execution.

### Blocker 7: Audit Strictness Setting Appears Incomplete

The project added strict audit behavior, which is good in principle.

But the configured audit mode does not appear consistently wired into `AuditStore`. Dry-run behavior also appears capable of failing closed even where comments imply best-effort mode.

Impact:

- Operators may think they configured best-effort or strict audit behavior, but runtime behavior may not match.

Required fix:

- Wire `settings.audit_mode` into audit store construction.
- Document exact behavior for dry-run and live mode.
- Add tests for audit write failure in strict and best-effort modes.

## Test Results

Focused regression tests:

```text
92 passed, 1 warning
```

Full test suite:

```text
791 passed, 111 skipped, 127 errors, 1 warning
```

The full-suite errors were caused by local Windows temp-directory permission failures around pytest temporary paths, not by normal assertion failures. That said, the suite still did not complete cleanly in this environment, so I cannot give a clean full-regression signoff.

## Can This Now Be Called Agentic AI?

Partially, but carefully.

It is fair to say:

> This is a LangGraph-based AI-assisted SRE automation system with emerging agentic workflows.

It is not yet fair to say:

> This is a production-ready autonomous AI SRE agent for live heterogeneous VM remediation.

Why:

- It has graph orchestration.
- It has tool execution.
- It has policy gates.
- It has partial LLM planning.
- It has some auditability.

But:

- The approved plan is not enforced as execution input.
- The LLM decision path is not consistently used for the approved plan.
- Live mode remains blocked unless an unsafe bypass is used.
- Verification and rollback behavior are not complete enough for real production mutation.

## Updated Approval Recommendation

Do not approve this as finished.

Approve the dev work as a serious improvement milestone, but require another hardening round before live production use.

Minimum signoff criteria:

1. Approved plan is immutable and is the only source of execution actions.
2. LLM decision records are attached to the approved plan artifact.
3. Live execution cannot re-plan without re-approval.
4. Read-only assessment and verification always inspect real VM state.
5. Verification failure after mutation triggers rollback.
6. Rollback support exists for every claimed OS family, or unsupported OS/action combinations are blocked.
7. Audit strict/best-effort behavior is wired, documented, and tested.
8. Full test suite runs cleanly in CI.

Until those are done, this is still not an optimal production solution. It is closer, but the control plane still has holes exactly where a live AI SRE system cannot afford holes.

---

# Re-Audit After Second Dev Fixes - 2026-05-12

## Executive Verdict

The team fixed several of the previous hard blockers. This is now much closer to a credible AI-assisted SRE control plane.

Most importantly:

- Batch-level planning now passes `llm_client`, environment policy, batch ID, VM ID, and AI decision store into `prioritize_actions(...)`.
- Wave dispatch now passes approved actions into the VM graph instead of always sending `planned_actions=[]`.
- The VM graph now skips re-planning when pre-approved actions are present.
- Patch verification failure now routes to rollback.
- Audit strict mode is now wired from `settings.audit_mode`.
- The unsafe live-mode bypass appears to have been removed.
- DNF/YUM-family rollback support has been added in principle.

That said, I still would not call this fully production-ready. The old blockers are mostly reduced, but a few important SRE-grade issues remain.

## What Is Now Fixed Or Mostly Fixed

1. **LLM planning is now wired into batch-level approval planning**
   - `plan_vm_node(...)` now passes `llm_client`, policy, batch ID, VM ID, and AI decision store to `prioritize_actions(...)`.
   - This fixes the previous problem where the approval plan was fallback logic while execution could use LLM logic.

2. **Approved actions are now injected into VM execution**
   - `make_wave_dispatcher(...)` now builds a VM-to-approved-actions lookup from `vm_plans`.
   - VM execution receives those actions through `planned_actions`.

3. **VM graph avoids re-planning when approved actions exist**
   - `route_after_drift_check(...)` now skips `plan_actions` when `planned_actions` is non-empty.

4. **Patch verification failure now routes to rollback**
   - `verify_node(...)` now sets failed status on verification failure.
   - `route_after_verify(...)` routes failed/error states to rollback.

5. **Read-only execution improved in several subgraphs**
   - Patching, disk cleanup, docker prune, and backup verification now mostly force `dry_run=False` for real state reads.

6. **Audit mode is now wired**
   - `AuditStore(...)` is now constructed with `strict_mode=(settings.audit_mode == "strict")`.

7. **The unsafe live CLI bypass appears removed**
   - I no longer see `--unsafe-legacy-live` or the Phase 0 live block in `main.py`.

## Remaining Blockers / Risks

### Blocker 1: Empty Approved Plan Still Triggers Re-Planning

The VM graph uses this condition:

```python
if state.get("planned_actions"):
    return "dispatch_action"
return "plan_actions"
```

That means an empty approved plan is treated the same as no approved plan.

Impact:

- If the approved plan for a VM is intentionally empty, execution can re-plan and run actions anyway.
- If batch planning fails for one VM and returns no plan, that VM can still be included in waves and re-plan during execution.
- This weakens the plan/apply immutability guarantee.

Required fix:

- Distinguish between "approved plan present with zero actions" and "approved plan missing."
- Live mode must never fall back to re-planning after approval.
- A VM with an approved empty plan must execute zero actions.
- A VM missing from the approved plan must be skipped or fail closed.

### Blocker 2: Missing Approved Plan Falls Back To Re-Planning

`make_wave_dispatcher(...)` still does:

```python
planned_actions=vm_id_to_approved_actions.get(str(t["vm_id"]), [])
```

The comment says this fallback "triggers normal planning."

That is not acceptable for live plan/apply.

Required fix:

- For live execution, missing approved plan must be a hard failure for that VM or the whole batch.
- No fallback re-plan after approval.

### High Risk 1: Log Rotation Verification Still Does Not Force Real Read

`log_rotation.verify_node(...)` still calls `executor.execute(...)` without `dry_run=False`.

Impact:

- Verification can use synthetic dry-run behavior instead of real VM state.
- This is lower risk than patching but still violates the "verification always reads reality" rule.

Required fix:

- Set `dry_run=False` on log rotation verification reads.
- Add a regression test for this exact behavior.

### High Risk 2: DNF Rollback Verification Is Too Weak

DNF rollback now exists, which is progress.

But the verification path only runs `rpm -q` and treats command success as verified. It does not compare installed versions against the pre-patch snapshot the way the apt path does.

Impact:

- Rollback can report success even if the target versions were not restored.

Required fix:

- Parse RPM output and compare every package/version against the snapshot.
- If any version mismatches, return rollback failure and require manual intervention.

### Medium Risk 1: Plan Artifact Does Not Include Full Action Parameters

The approved `vm_plans` currently serialize only:

```python
{"action_type": ..., "risk_tier": ...}
```

The `Action` model supports `params`, but batch planning drops them.

Impact:

- If future actions need package lists, paths, thresholds, exclusions, or other parameters, the approval artifact will not capture exactly what execution intends to do.

Required fix:

- Include validated `params` in the plan hash and approval summary.
- Ensure execution consumes only those approved params.

### Medium Risk 2: Plan Model Exists But Graph Uses Raw Dicts

`errander/models/plans.py` defines `ImmutablePlan`, but the graph hand-builds plan hashes with raw dictionaries.

This is not immediately broken, but it creates two sources of truth for plan hashing.

Required fix:

- Use the `ImmutablePlan` model directly in the graph or delete it.
- Keep exactly one canonical plan hashing implementation.

## Test Results

Focused tests that do not depend on broken Windows temp fixtures passed:

```text
127 passed, 1 warning
```

Broader focused run:

```text
177 passed, 12 errors, 1 warning
```

The 12 errors were all Windows temp-directory permission errors around pytest `tmp_path` / basetemp cleanup, not assertion failures. This environment still cannot provide a clean full regression result.

## Updated Approval Recommendation

The project is significantly improved.

I would now classify it as:

> A serious pre-production AI-assisted SRE automation platform.

I still would not approve it as:

> A production-ready autonomous AI SRE agent for live heterogeneous VM remediation.

Minimum remaining signoff items:

1. Empty approved plan must mean "execute nothing."
2. Missing approved plan must fail closed in live mode.
3. No re-planning after approval in live mode, ever.
4. Log rotation verification must force real VM reads.
5. DNF rollback must compare actual restored versions against the snapshot.
6. Plan artifact must include action params or explicitly forbid param-bearing actions.
7. CI must produce a clean full test result outside this local temp-permission issue.

My current SRE verdict: **much better, but still not final.** The team is moving in the right direction, but the last remaining issues are exactly the kind that matter when software is allowed to touch live machines.

---

# Re-Audit After Third Dev Fixes - 2026-05-12

## Executive Verdict

The dev team fixed most of the remaining hard safety issues from the previous review.

This is now a materially stronger implementation. The plan/apply path is no longer obviously broken, rollback verification is stronger, and verification reads are more consistently forced against real VM state.

I would now move the project from:

> Blocked for architectural safety reasons

to:

> Conditionally acceptable for controlled staging and pre-production validation

I still would not approve unrestricted production autonomy yet, but the project has crossed an important line: the previous plan/apply blockers are mostly addressed.

## What Is Now Fixed

1. **Approved empty plan now means "do nothing"**
   - `VMGraphState` now has `pre_approved_plan_set`.
   - The VM graph distinguishes between an explicitly approved empty plan and no plan.
   - If `pre_approved_plan_set=True` and `planned_actions=[]`, the VM routes to audit instead of re-planning.

2. **Missing approved plan now fails closed in live mode**
   - Wave dispatch now detects VMs missing from the approved plan.
   - In live mode, it sets an error instead of allowing normal re-planning.
   - This fixes the previous high-risk "missing plan becomes re-plan" issue.

3. **Log rotation verification now forces real reads**
   - Log verification now calls `executor.execute(..., dry_run=False)`.
   - This closes the previous dry-run verification weakness for log rotation.

4. **DNF rollback verification now compares versions**
   - DNF rollback now parses `rpm -q` output and compares installed versions to the pre-patch snapshot.
   - Version mismatch now returns rollback failure instead of false success.

5. **Regression tests were added for key fixes**
   - VM graph routing tests cover pre-approved non-empty and empty plans.
   - DNF rollback tests cover matching and mismatched versions.

## Remaining Risk

### Medium Risk: Batch Plan Still Drops Action Params

The per-VM graph planning path now serializes:

```python
{
  "action_type": ...,
  "risk_tier": ...,
  "params": ...
}
```

But the batch-level planning artifact still serializes only:

```python
{
  "action_type": ...,
  "risk_tier": ...
}
```

Impact:

- Today this may be harmless because most generated actions appear to have empty params.
- But the `Action` model supports params, validators inspect params, and backup verification already consumes action params.
- If future AI planning starts attaching package lists, backup paths, cleanup paths, thresholds, or exclusions, those details may not be captured in the approved plan hash.

Required fix:

- Include `params` in batch-level `vm_plans`.
- Include params in the plan hash.
- Include meaningful params in the approval summary where operator judgment depends on them.
- Add a test proving params survive planning, approval hashing, and execution dispatch.

## Test Results

Clean focused subset:

```text
97 passed, 1 warning
```

VM graph routing/dispatch subset:

```text
13 passed, 33 deselected, 1 warning
```

Broader focused run:

```text
133 passed, 10 errors, 1 warning
```

The 10 errors were the same local Windows `tmp_path` permission issue seen in previous audits, not assertion failures.

## Updated Approval Recommendation

This is now much closer.

I would approve the project for:

- controlled staging tests,
- disposable VM environment validation,
- limited non-production live runs,
- deeper AI evaluation,
- operator workflow testing.

I would not yet approve:

- fully autonomous production remediation,
- broad heterogeneous fleet rollout,
- production patching without staging soak evidence,
- marketing claims of a finished autonomous AI SRE agent.

Remaining signoff criteria:

1. Batch plan artifact includes action params.
2. CI runs cleanly outside this local Windows temp-permission issue.
3. Staging soak tests pass against Ubuntu/Debian and RHEL-like VMs.
4. A human operator validates approval UX with real plan summaries.
5. AI evals prove malformed, unsafe, or prompt-injected LLM outputs cannot escape validators.

Current SRE verdict:

> This is no longer a toy or a broken prototype. It is a serious pre-production AI SRE automation system. It still needs staging proof and one more plan-artifact hardening pass before I would call it production-ready.

---

# Clarification On Windows `tmp_path` Test Failures - 2026-05-12

The dev team's explanation is mostly fair, with one important qualification.

## What I Verified

1. The hardcoded `/tmp/test-locks` bug appears fixed.
   - I no longer found `FileLocker(lock_dir=Path("/tmp/test-locks"))`.
   - The relevant tests now use `tmp_path / "locks"`.

2. The remaining `/tmp` strings I found are not the same bug.
   - Some are Linux target paths for disk cleanup.
   - Some are command-injection test inputs.
   - `tests/conftest.py` still contains `/tmp/test_key`, but that is a fake SSH key path value in test config, not a file operation.

3. The full test suite still does not pass in my current execution sandbox.
   - Running:

```text
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp .pytest-tmp
```

   still produced:

```text
798 passed, 111 skipped, 127 errors
```

4. The failure is not normal assertion failure.
   - Pytest fails while creating/removing/listing temp directories.
   - The stack traces are still Windows `PermissionError: [WinError 5] Access is denied`.

5. I reproduced the environment problem outside the project tests.
   - A direct Python check showed that a directory created with restrictive mode can become unreadable in this environment:

```python
Path("pycreates700").mkdir(mode=0o700, exist_ok=True)
list(Path("pycreates700").iterdir())
```

   This failed with:

```text
PermissionError: [WinError 5] Access is denied
```

That strongly suggests this sandbox/toolchain has a Windows ACL behavior around Python-created restrictive temp directories. Pytest uses restrictive temp directory permissions, so this explains why `tmp_path` tests fail here even after code fixes.

## Was My Earlier Call Wrong?

Not wrong, but incomplete.

Correct:

- I correctly said the errors were not product assertion failures.
- I correctly treated them as a test-environment blocker for local signoff.

Incomplete:

- I did not initially separate the old hardcoded `/tmp/test-locks` bug from the broader Windows temp-dir cleanup/ACL behavior.
- I also cannot reproduce the dev team's claimed `925 passed, 111 skipped, 0 errors` in this sandbox because pytest temp directories become inaccessible here.

## Updated Test Signoff Position

I accept that the old hardcoded `/tmp/test-locks` issue was a real bug and appears fixed.

I also accept that the remaining `tmp_path` failures in my environment are not evidence of broken SRE product logic.

However, I cannot personally certify the `925 passed, 111 skipped, 0 errors` claim from this sandbox. For final signoff, require the dev team to provide a CI artifact or terminal transcript from a clean environment showing:

```text
925 passed, 111 skipped, 0 errors
```

That CI run should include:

- OS name and version,
- Python version,
- pytest version,
- exact command,
- full summary output,
- confirmation that it ran from a clean checkout or clean test temp directory.

Current position:

> Treat the local `tmp_path` failures as environmental in this sandbox, not as evidence of product failure. But require a clean CI run before production approval.

---

# Audit After Latest Dev Fix Claim - 2026-05-12

## Executive Verdict

The dev team appears to have fixed the last application-level blocker I previously identified: action `params` are now included in the batch-level approved plan.

That is a real fix, not cosmetic.

However, the full pytest suite still cannot be independently verified from this sandbox because the Python/Windows temp-directory ACL issue remains reproducible here.

## What I Verified As Fixed

### Action Params Now Flow Through The Approved Plan

Previous concern:

- The batch plan included only `action_type` and `risk_tier`.
- It dropped `params`.
- That meant operator approval and plan hashing might not include package lists, paths, thresholds, or exclusions.

Current state:

- `plan_vm_node(...)` now serializes `params` into each planned action.
- `generate_plan_artifact_node(...)` hashes `vm_plans`, so params now affect `plan_hash`.
- `_format_plan_for_approval(...)` includes non-empty params in the Slack approval summary.
- Wave dispatch passes the approved planned action dicts through to VM execution.

This closes the previous medium-risk blocker.

## Tests Run

Focused plan/apply, params, rollback, log verification, and command-builder tests:

```text
101 passed, 1 warning
```

Focused VM planning/routing/dispatch tests:

```text
13 passed, 33 deselected, 1 warning
```

These are the most relevant tests for the previous safety blockers, and they passed.

## Full Suite Status

I still cannot reproduce a clean full-suite run in this sandbox.

The command:

```text
.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp .pytest-tmp
```

still failed locally with temp-directory permission errors, not assertion failures.

I also re-tested the underlying environment issue directly:

```python
Path("acl_probe_latest").mkdir(mode=0o700, exist_ok=True)
list(Path("acl_probe_latest").iterdir())
```

This still fails here with:

```text
PermissionError [WinError 5] Access is denied
```

That means the local test environment remains unsuitable for full `tmp_path`-based verification. This is not proof of product failure, but it also means I cannot independently certify the dev team's claimed full-suite result from this sandbox.

## Current Approval Position

The core application-level issues I previously raised are now mostly addressed:

1. Approved plan is injected into execution.
2. Empty approved plan does not re-plan.
3. Missing live plan fails closed.
4. LLM planning is wired into batch planning.
5. Audit store is used for AI decisions.
6. Patching rollback exists for apt and dnf paths.
7. Rollback verification compares package versions.
8. Read-only verification is mostly forced to real VM reads.
9. Action params are now part of the approved plan/hash path.

Remaining signoff requirements:

1. Provide clean CI proof of the full suite:

```text
925 passed, 111 skipped, 0 errors
```

2. Run staging soak tests against disposable Ubuntu/Debian and RHEL-like VMs.
3. Capture real approval artifacts showing params, plan hash, and executed actions.
4. Prove live execution follows the approved artifact exactly in staging.
5. Run AI safety evals for malformed LLM output, prompt injection, unsafe action requests, and schema violations.

## Updated SRE Verdict

I no longer see a major code-level reason to block controlled staging.

I would approve:

- controlled staging validation,
- disposable VM live tests,
- operator approval workflow testing,
- CI-based full regression review.

I would still not approve:

- unrestricted production autonomy,
- production patching across heterogeneous fleets,
- external marketing as a finished autonomous AI SRE agent,
- final signoff without a clean CI artifact and staging evidence.

Current label:

> Strong pre-production AI-assisted SRE automation system, pending CI and staging proof.

---

# AI Evals And CI Evidence Clarification - 2026-05-12

## AI Evals Status

The dev team is correct that AI eval coverage exists.

I inspected `tests/ai_evals/test_golden_plans.py`. It covers the right safety classes:

- injection payloads in LLM action output,
- unknown or unsafe action names,
- schema-violation fallback behavior,
- LLM unavailable / malformed response fallback,
- action applicability filtering,
- AI decision audit logging,
- deterministic prompt hashing.

I ran:

```text
.venv\Scripts\python.exe -m pytest tests\ai_evals -q -p no:cacheprovider
```

Result:

```text
32 passed
```

Updated position:

> The AI evals concern is addressed at unit/eval-test level.

This does not replace staging validation against real VMs, but it does satisfy the previous request for AI safety eval coverage around malformed and unsafe LLM output.

## CI Transcript Status

I have not independently verified the claimed full-suite CI result from this sandbox.

If the dev team has a CI transcript showing:

```text
925 passed, 111 skipped, 0 errors
```

then that should be accepted only if it includes:

- exact command,
- OS and runner type,
- Python version,
- pytest version,
- commit SHA,
- full final pytest summary,
- proof it ran from a clean checkout or clean workspace.

Because this local sandbox still has a reproducible Windows ACL issue around Python-created restrictive temp directories, I cannot use this environment to confirm or deny that CI result.

## Updated Bottom Line

The dev team's latest summary is mostly accurate:

- I have no new major code findings.
- The AI evals are present and passing.
- The remaining signoff items are operational/infrastructure:
  - clean CI evidence,
  - staging soak on real disposable VMs,
  - production-readiness review of the operator workflow.

Current verdict remains:

> Strong pre-production AI-assisted SRE automation system, pending CI and staging proof.
