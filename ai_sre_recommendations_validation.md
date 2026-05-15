# AI SRE Recommendations Implementation Validation

Date: 2026-05-14
Role: Senior SRE auditor

## Revalidation - 2026-05-14

The dev team fixed the major production wiring blockers from the first validation. The high-severity "implemented but not active" concern is now mostly closed.

### Revalidated As Fixed

- `run_env_batch()` now accepts `disk_history_store`, `baseline_store`, and `vm_state_store` at `errander/main.py:594-596`.
- `main()` now initializes `VMDiskHistoryStore`, `BaselineStore`, and `VMStateStore` at `errander/main.py:899-910`.
- `run_env_batch()` passes the SRE stores/settings into `build_batch_graph()` at `errander/main.py:626-634`.
- `build_batch_graph()` passes SRE dependencies into `make_wave_dispatcher()` at `errander/agent/graph.py:1308-1318`.
- `make_wave_dispatcher()` passes them into `build_vm_graph()` at `errander/agent/graph.py:1116-1126`.
- `build_vm_graph()` now builds the patching subgraph with `audit_store` and `vm_state_store` at `errander/agent/vm_graph.py:1127-1131`.
- `critical_services` now flows from inventory targets into runtime target dicts at `errander/main.py:640-647`, into wave dispatch state at `errander/agent/graph.py:1198-1199`, and into the patching subgraph state at `errander/agent/vm_graph.py:584-592`.
- New wiring tests exist in `tests/agent/test_sre_wiring.py`.

### Correction

The earlier revalidation note about `authentication failure` was stale. Current `errander/execution/failed_logins.py:48-49` only greps `Failed password|Invalid user`, which matches the parser at `errander/execution/failed_logins.py:28-30`.

Validation command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\execution\test_failed_logins.py -q -p no:cacheprovider
```

Result:

- 21 passed.

### Still Open

1. Several roadmap recommendations remain intentionally unimplemented.

   Still not seen as product features: stale uptime warning thresholds, top disk consumers, service restart recommendation, full preflight dependency gate, world-writable sensitive path check, and security update count.

### Revalidation Test Result

Focused command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\observability\test_ui_security.py tests\ui\test_web_ui.py tests\agent\test_sre_wiring.py -q -p no:cacheprovider
```

Result:

- 18 passed.
- 25 skipped.

Broader recommendation-suite command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\agent\test_sre_wiring.py tests\execution\test_reboot_check.py tests\execution\test_service_check.py tests\execution\test_disk_trend.py tests\execution\test_failed_logins.py tests\safety\test_disk_history.py tests\safety\test_baselines.py tests\safety\test_vm_state.py tests\safety\drift_checks tests\agent\subgraphs\test_patching.py tests\config\test_inventory.py tests\observability\test_reporting.py tests\models\test_reports.py -q -p no:cacheprovider
```

Result:

- 358 passed.
- 14 errored due local Windows temp-directory permission failure at `C:\Users\THISPC\AppData\Local\Temp\pytest-of-THISPC`.
- No assertion failures were observed before the temp fixture errors.

### Updated Recommendation Verdict

The major wiring claim is now credible. I would accept the SRE recommendation implementation as substantially fixed. The failed-login parser concern is closed; remaining items are roadmap scope, not regressions in the implemented fixes.

## Verdict

The team implemented several useful SRE signal modules, but I would not accept the claim that the recommendations are fully implemented in the product.

The main problem is wiring. A lot of the new code exists and has tests, but the normal production path does not appear to instantiate or pass the new disk-history, drift-baseline, VM-state, or failed-login settings into the VM graph. That means some features are "implemented as library code", not "operationally active in the agent".

## High-Severity Findings

### 1. New SRE signal stores are not wired into the production batch graph

Evidence:

- `errander/main.py:610` builds the batch graph with `settings`, `executor`, `locker`, `audit_store`, `ssh_manager`, LLM, approval, and deferred store.
- `errander/agent/graph.py:1288` calls `make_wave_dispatcher(...)`.
- `errander/agent/graph.py:1109` builds the VM graph with only `executor`, `locker`, `audit_store`, `ssh_manager`, LLM, and AI decision store.
- `errander/agent/vm_graph.py:1098` supports optional `disk_history_store`, `baseline_store`, and `sre_failed_logins_settings`, but these are not passed from `build_batch_graph`.
- `errander/agent/vm_graph.py:1176`, `1188`, and `1200` only add disk trend, drift baseline, and failed-login nodes when those optional objects are provided.

Impact:

Disk growth trend, per-kind security drift, and failed SSH login checks may work in isolated tests, but they are not active in normal agent runs.

Required fix:

Production startup must initialize `VMDiskHistoryStore`, `BaselineStore`, and any needed settings objects, pass them into `build_batch_graph`, then into `make_wave_dispatcher`, then into `build_vm_graph`.

### 2. Critical service checks are not connected to real inventory targets

Evidence:

- Inventory schema supports `critical_services` at target and environment level in `errander/config/schema.py:62` and `errander/config/schema.py:106`.
- `VMTarget` stores `critical_services` in `errander/models/vm.py:42`.
- `errander/main.py:624-634` builds runtime target dictionaries, but does not include `critical_services`.
- `errander/agent/graph.py:1165-1186` sends VM graph state without `critical_services`.
- `errander/agent/vm_graph.py:581-588` invokes the patching subgraph without `critical_services`.
- The patching subgraph service logic depends on `state.get("critical_services")` in `errander/agent/subgraphs/patching.py:482` and `514`.

Impact:

The critical-service feature is mostly dead in production. Operators can configure services in inventory, tests can pass when manually injecting them, but the real maintenance run does not carry them to the patching graph.

Required fix:

Carry `critical_services` from inventory load to runtime target dicts, through batch fan-out/wave dispatch, into VM state, and into the patching subgraph state.

### 3. Reboot-required state persistence is not wired in production

Evidence:

- `errander/agent/subgraphs/patching.py:413` implements `reboot_check_node`.
- It only persists state if `vm_state_store` is provided at `errander/agent/subgraphs/patching.py:448`.
- `errander/agent/vm_graph.py:1121` builds `build_patching_subgraph(executor).compile()` without `vm_state_store`, `audit_store`, or `batch_id`.

Impact:

The reboot check can return a flag inside the patching subgraph, but the durable `needs_reboot` state is not written during normal runs. The product recommendation said "mark VM as `needs_reboot`"; that is not proven in the production path.

Required fix:

Initialize `VMStateStore` in startup and pass it into `build_patching_subgraph`. Also include reboot-required status in the VM action result and final report.

## Medium-Severity Findings

### 4. Failed SSH login parsing misses common "authentication failure" lines - resolved in revalidation

Original evidence:

- The command intentionally greps `authentication failure` in `errander/execution/failed_logins.py:48-50`.
- The regex at `errander/execution/failed_logins.py:28-30` only extracts `Failed password for ... from ...` and `Invalid user ... from ...`.

Original impact:

Some real auth failures are fetched but not counted. The feature will under-report on systems that log PAM-style `authentication failure` messages without the same username/IP format.

Resolution:

The command no longer fetches unsupported `authentication failure` lines. Current code only greps `Failed password|Invalid user`, and `tests/execution/test_failed_logins.py` passes.

### 5. Disk trend is useful but report/UI value depends on wiring

Evidence:

- Disk capture and growth detection are implemented in `errander/execution/disk_trend.py:149`.
- Storage is implemented in `errander/safety/disk_history.py:39`.
- VM graph has optional support at `errander/agent/vm_graph.py:797`.
- Production graph does not pass the store/settings, as noted in finding 1.

Impact:

Good implementation, but not a product feature until active in real runs and visible in reports/UI.

### 6. Several recommendations remain unimplemented

Not found as product features:

- Uptime/stale reboot warning thresholds: 90/180 day warnings are not implemented as report warnings.
- Top disk consumers in safe paths: existing disk cleanup has size commands, but not the recommended top-5 explainability feature.
- Service restart recommendation: no read-only "services may need restart" recommendation workflow.
- Full pre-flight dependency checks: SSH/OS exists, package lock exists, but `sudo` availability, package manager readiness, free disk threshold, and clock sanity are not implemented as a unified preflight gate.
- World-writable sensitive path check.
- Security update count separated from normal updates.

## What Looks Good

- Reboot-required probe exists and is conservative: `errander/execution/reboot_check.py:34`.
- Package manager lock detection exists in the patching subgraph: `errander/agent/subgraphs/patching.py:344`.
- Service health probe exists: `errander/execution/service_check.py:124`.
- Disk history store and growth detection exist: `errander/safety/disk_history.py:39` and `errander/execution/disk_trend.py:149`.
- Lightweight security drift modules exist for sudoers, authorized keys, listening ports, and scheduled jobs:
  - `errander/safety/drift_checks/sudoers.py:53`
  - `errander/safety/drift_checks/authorized_keys.py:79`
  - `errander/safety/drift_checks/listening_ports.py:62`
  - `errander/safety/drift_checks/scheduled_jobs.py:64`
- Tests are broad for helper modules and optional graph nodes.

## Test Result

Command run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\execution\test_reboot_check.py tests\execution\test_service_check.py tests\execution\test_disk_trend.py tests\safety\test_disk_history.py tests\safety\test_baselines.py tests\safety\drift_checks tests\agent\subgraphs\test_patching.py tests\agent\test_vm_graph_drift.py tests\config\test_inventory.py tests\observability\test_reporting.py tests\models\test_reports.py -q -p no:cacheprovider --basetemp C:\tmp\errander-reco-audit-20260514
```

Observed:

- 338 tests passed.
- 14 inventory tests errored due Windows temp-directory `PermissionError`, not assertion failures.
- Rerunning inventory tests with a workspace temp dir hit the same local cleanup permission issue.

Interpretation:

The helper-level implementation is reasonably tested. The production wiring gap is not caught by those tests.

## Acceptance Decision

Do not approve this as "recommendations implemented" yet.

Approve only after:

1. Production graph wiring passes disk, drift, VM-state, and failed-login dependencies into `build_vm_graph`.
2. `critical_services` flows from inventory to actual patching subgraph state.
3. An end-to-end test runs `run_env_batch` with configured `critical_services`, disk trend, drift settings, and failed-login settings, then proves the corresponding nodes execute.
4. Final reports show the new SRE signals.
5. A staging run on real Linux VMs confirms the probes are read-only and do not block safe maintenance except where designed.
