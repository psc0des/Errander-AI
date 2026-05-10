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
