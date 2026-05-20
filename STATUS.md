# Errander-AI — Project Status

## Last Updated
2026-05-20

## Current Phase
**Node Exporter flag + configure.sh interactive setup (2026-05-20).**

Operator-controlled metrics source strategy: each VM in `inventory.yaml` now carries a `node_exporter: true/false` flag. When true, `MetricsCollector` scrapes the Node Exporter HTTP endpoint (`:9100`) for zero-auth-log metrics; when false (or when `:9100` is unreachable despite being flagged true), it falls back to SSH probe transparently. Configure.sh runs an interactive setup flow: SSH connectivity check → Node Exporter HTTP check → "Install? [Y/n]" prompt (default Y) → install via SSH → verify → writes `node_exporter: true/false` per VM into `inventory.yaml`.

**Real metrics collection + live UI wired up (2026-05-20).**

VM Detail Resource Trends card now backed by real SSH-collected data (not just fixtures). CPU/MEM/disk probed from target VMs every 60 seconds via asyncssh, stored in `vm_metrics` SQLite table (migration #4), served via `/api/vm/{hostname}/metrics`, and auto-refreshed in the browser every 60 s. Fixture fallback preserved for demo mode.

External SRE reviewed the running Operations Hub UI and graded enterprise trust/audit 3/10, decision support 4/10, operator safety 3/10. All P0 + P1 items landed. VM Detail sparkline trends added per user request.

### Shipped (P0)
- **`errander/web/evidence.py`** (new, 200+ lines) — additive enrichment overlay: UI_MODE, APPROVAL_EVIDENCE, AUDIT_EVIDENCE, VM_EVIDENCE, BATCH_EVIDENCE. Shape mirrors the real audit DB / immutable execution artifact so it becomes the integration seam when wired to real data.
- **Mode banner** in `layout()` via `_mode_banner_html()` — every page renders DEMO/LIVE · env · DRY-RUN/LIVE-EXEC · freshness · backend · build above breadcrumb. Color escalates for LIVE + PROD.
- **CSS additions** — ~300 lines of SRE-readiness styles (mode-banner, evidence-grid, layer-section, countdown-big, deeplink-chip, confirm-modal, destructive-hdr, layer-partition, vm-trends).
- **Approvals page** (`page_approvals`) — full SRE evidence chain, Layer A/B/Policy split, typed-confirm modal.
- **Admin page** (`page_admin`) — "DESTRUCTIVE — AUDITED" red banner, typed-phrase modal per action, TRUNCATE AUDIT LOG blocked in UI.
- **Agent page** (`page_agent`) — Layer A vs Layer B visually partitioned with "⚠ SAFETY BOUNDARY" divider.

### Shipped (P1)
- **Fleet Dashboard** — `_operator_queue()` priority aggregator (CRITICAL → HIGH → MED → INFO). VM cards demoted to "Fleet Inventory" below KPIs.
- **Audit Log** — click-to-expand evidence rows, client-side filters, Export CSV + JSON.
- **VM Detail — Metricbeat-style Resource Trends** (user request 2026-05-20):
  - New `_sparkline_svg()` helper: SVG sparkline with gradient fill, dashed 75% warning + 90% critical threshold lines, endpoint dot.
  - New `_mini_sparkline_svg()`: 80×18 inline sparklines for disk partition rows with 24h trend delta (↑/↓ N% 24h).
  - New `_vm_resource_trends()`: full "Resource Trends" card with 24h/7d JS toggle, CPU + Memory panels each showing current value + sparkline + min/avg/max stats + x-axis labels.
  - `VM_EVIDENCE` expanded with `cpu_history`, `mem_history`, `cpu_history_7d`, `mem_history_7d`, `disk_history` for all 11 VMs. History endpoint pinned to live `vm["cpu"]`/`vm["mem"]` so sparkline always lands on current value.
  - Verified on prod-api-01 (gradual MEM climb), prod-db-01 (OOM pattern: MEM 70→94% over 7d, /var ↑16% 24h). 7d toggle switches x-axis labels and chart data correctly.

### Shipped (P1 follow-up)
- **VM Detail — VM_EVIDENCE wired (2026-05-20)**: lock holder red alert, noop badge, last_patched + ssh_key_fp in identity card, window from evidence. Deep-link strip: Last Batch → Approval → Audit Slice → Patch History.
- **Batches — BATCH_EVIDENCE click-to-expand (2026-05-20)**: each row expands to show plan_hash, approver, approval_source, outcome counts (ok/failed/partial/rolled_back). URL fragment auto-expands matching row. Deep links: Approval, Audit Slice.

### Deferred (P1, follow-up session)
Glossary verify, Inventory + Settings polish, mobile responsive sweep.

### Files Changed This Session
- `errander/web/evidence.py` — VM_EVIDENCE expanded with time-series history for all 11 VMs
- `errander/web/server.py` — CSS additions (vm-trends), three new helper functions (_sparkline_svg, _mini_sparkline_svg, _vm_resource_trends), disk_rows enhanced with mini-sparklines, _vm_resource_trends call inserted in page_vm; page_vm gains metrics_by_window param, handle_vm queries DB for all 4 windows, handle_metrics_api added (/api/vm/{hostname}/metrics), _auth_middleware returns JSON 401 for /api/ paths, _on_startup/_on_cleanup hooks wire DB open/migration/APScheduler collection loop, create_app registers new route + hooks
- `errander/safety/migrations.py` — migration #4 added: vm_metrics table (hostname, metric, value_pct, ts) with compound PK + index
- `errander/observability/vm_metrics.py` — complete rewrite: MetricsCollector class with flag-driven discover(), _parse_prom_text(), _extract_ne_metrics() (stateless mem/disk + stateful CPU delta), _probe_node_exporter(), _probe_ssh() with persistent asyncssh connections, collect_all(), source_map property; module-level cleanup_old_metrics() and query_metrics() unchanged
- `errander/models/vm.py` — `node_exporter: bool = False` added to VMTarget dataclass
- `errander/config/schema.py` — `node_exporter: bool | None = None` added to TargetSchema; `node_exporter: bool = False` added to EnvironmentSchema
- `errander/config/inventory.py` — `_resolve_single_target()` resolves host-level flag with env-level inheritance; `node_exporter=` passed to VMTarget constructor
- `errander/config/configure.py` — NEW: interactive Node Exporter setup flow (check SSH → check :9100 → prompt install → install via SSH → write inventory.yaml)
- `configure.sh` — NEW: thin bash wrapper invoking `errander.config.configure`
- `example/inventory.yaml` — env-level `node_exporter: false` default + per-host field docs
- `tests/observability/test_vm_metrics.py` — 35 tests: flag-driven discover tests (5), Prometheus text parser (8), NE metric extraction (10), SSH probe parser (6), collect_all DB integration (3), query_metrics (3)
- `tests/safety/test_migrations.py` — migration count assertions updated for migration #4

## Previous Phase
**P0-1 immutable execution artifact — final closure (2026-05-19).**

## Previous Phase 2
**P0-1 immutable execution artifact — final closure (2026-05-19).**

Third (final) SRE pass found one remaining P1 bug. Fixed:

**verify_node partial-update query scope** (patching.py): `verify_node` was querying `list_installed_versions(pending_updates)` but comparing results against all `approved_packages`. In a partial-update scenario (some approved packages already at their target version, only others in `pending_updates`), packages not in `pending_updates` never appeared in the dpkg output, causing false verification failures. Fix: when `approved_packages` is present, query `list_installed_versions(all_approved_names)` so every approved package is visible in the dpkg output for comparison.

Two new tests added: partial-update scenario passes correctly; query arg is inspected to confirm all approved names reach the SSH command.

**P0-1 and P0-2 fully closed. 1991 tests passing, 0 failures.**

## Previous Phase
**P0-1 immutable execution artifact — second closure (2026-05-19).**

Second SRE pass found three remaining gaps. All three closed:

1. **assess_node approved-artifact path** (patching.py): with `approved_packages`, assess no longer calls `apt list --upgradable`. Instead it queries `list_installed_versions()` for exactly the approved packages and compares against approved targets. Fresh repo state cannot override the approved artifact. Already-at-target → `nothing_to_do=True`. Differs → only those packages in `pending_updates`.
2. **verify_node exact match** (patching.py): with `approved_packages`, verify now asserts `installed_version == approved_target_version` for every approved package. Any mismatch or missing package → `FAILED` with the specific discrepancy in the error. Falls back to "did anything change" only when no artifact is present.
3. **Missing `approved_at` fails closed** (graph.py): artifact replay without a valid `preloaded_approved_at` now returns an error rather than skipping the age check. Unparseable timestamps also fail closed. Legacy records without a stored artifact (re-plan/re-approve path) are unaffected since they never enter `load_deferred_artifact_node`.

**1989 tests passing, 0 failures.**

## Previous Phase
**Glossary overhaul — current with v1 codebase (2026-05-19).**

Updated `_GLOSS` and `_WF_JS` to match the actual codebase state:
- Added Backup Verify and Service Restart to ACTIONS section (both v1 sub-graphs)
- Added Layer A / Layer B (AI safety invariant) to SAFETY section
- Renamed "vLLM" → "LLM Endpoint", leading with "any OpenAI-compatible endpoint"
- Fixed Plan Enrichment workflow badge: "P0-1" → "PRE-APPROVAL"
- Fixed Plan Enrichment node sublabel: removed "P0-1"
- Updated Action Exec. popup and sublabel to reflect all 6 v1 sub-graphs
- Updated action descriptions with risk tiers (Low/Medium/High)

## Previous Phase
**Login screen + Godmode E2E sweep + 5 UI fixes (2026-05-19).**

Login screen: dark indigo full-page card, HMAC-signed 8h session cookie (hashlib/hmac stdlib — no new deps), auth middleware protecting all routes, Sign out in sidebar.

Godmode E2E sweep found and fixed: duplicate agent page buttons, missing ▶ on fleet topnav, sparse Settings/VM-detail/Inventory pages. Settings now has a full env vars reference table. VM detail has a fleet siblings section. Inventory has an environment breakdown.

## Previous Phase
**UI overhaul — information density, actionability, inline audit detail + /agent page (2026-05-19).**

Comprehensive UX improvement to the dev UI driven by operator pain points:
- VM cards now show CPU / MEM / DISK tri-bars, pending patch count, uptime, IP, last action type
- Fleet dashboard adds a "Needs Attention" callout for warning/failed/pending VMs with specific reasons and direct links
- Approval cards gain a "VM Health at Request Time" panel (CPU, MEM, DISK, load average) and a trigger line so the operator understands WHY the approval is needed
- "If Rejected" consequences are now shown below every approval to communicate risk clearly
- Audit log now shows the full `detail` field inline under every action — previously hidden behind a broken "Details →" link that went nowhere
- Audit detail text enriched for all events (which packages changed, which logs rotated, disk before→after)
- Batch history error column now shows a one-line error summary and links to affected VMs
- VM detail page shows pending patch count as a prominent callout, CPU/memory in the identity card, and richer KPI tiles
- New `/agent` page: live agent status, LangGraph execution trace (log-scale bars), per-VM stage outcome matrix, LLM planning decisions, scheduler timeline, daily probe history, deferred execution queue
- All 111 UI tests still pass, 0 regressions

## Files Changed (UI overhaul)
- `errander/web/data.py` (REWRITTEN — added cpu, mem, pending_patches, last_action_type to every VM; vm_cpu/vm_mem/trigger/reject_consequence/rollback_strategy to approvals; enriched all audit event detail strings; added error_summary/failed_vms to batches; enriched VM_ACTIONS for more VMs; added AGENT_STATUS, EXECUTION_TRACE, VM_TRACE, LLM_DECISIONS, SCHEDULER_TIMELINE, PROBE_HISTORY, DEFERRED_QUEUE)
- `errander/web/server.py` (MODIFIED — new CSS classes for metric bars, attention box, health panel, consequences panel, inline audit detail, agent page components; page_fleet/page_approvals/page_audit/page_vm/page_batches/page_agent written; handle_agent + /agent route added)

---

## Previous Phase
**Fix — audit log detail strings for patching, log_rotation, and disk_cleanup (2026-05-18).**

Audit log `detail` fields were misleading after live execution:
- Patching showed `"updates: 17 packages; snapshot: 17 packages"` — both counts were PRE-execution (pending + snapshot). The `changed` dict computed in `verify_patch_node` was logged but never stored in state.
- Log rotation showed `"rotated: ['logrotate']"` — `rotation_output` key is the string `"logrotate"` (the tool name), not a file path.
- Disk cleanup showed raw dicts — no space freed amounts.

Fix: `PatchingGraphState` gains `changed_packages` field; `verify_patch_node` stores it; detail builder uses it to show `"installed: N package(s)"`. Log rotation detail now shows `"rotated: N file(s) via logrotate"` or `"rotated: N file(s) manually"`. Disk cleanup detail shows cleaned paths + before/after disk usage %.

Also fixed 5 Playwright test assertions using old nav link names ("Approvals" → "Approval Queue", "Dashboard" → "Fleet Dashboard", "Batches" → "Batch History") left stale from the UI redesign.

**1969 tests passing, 0 failures.**

## Files Changed (detail string fix)
- `errander/agent/subgraphs/patching.py` (MODIFIED — `changed_packages` field added to `PatchingGraphState`; `verify_patch_node` returns `changed_packages`)
- `errander/agent/vm_graph.py` (MODIFIED — patching, log_rotation, disk_cleanup detail builders rewritten)
- `tests/agent/test_deferred_replay.py` (FIXED — `vm_plans` → `enriched_vm_plans` assertion)
- `tests/agent/test_enrich_plan.py` (FIXED — `vm_plans` → `enriched_vm_plans` assertion)
- `tests/ui/test_approvals_playwright.py` (FIXED — stale nav link name assertions)
- `tests/ui/test_web_ui.py` (FIXED — stale nav link name assertions)
- `tests/ui/test_settings_playwright.py` (FIXED — stale nav link name assertion)
- `tests/ui/test_inventory_playwright.py` (FIXED — stale nav link name assertion)

---

## Previous Phase
**UI redesign — "Sovereign Architect" design system from Stitch project 695805871329192760 (2026-05-18).**

Replaced the dark theme UI with the agreed Stitch design: light surfaces, deep indigo sidebar (`#1e1b4b`), Space Grotesk headlines, JetBrains Mono for system data, gradient buttons (primary `#3525cd` → secondary `#712ae2` at 135°), no 1px borders anywhere. Also wired the missing "Test Connection" button on the Settings page (endpoint existed but had no UI trigger).

Also diagnosed and fixed during live DR dry-run validation:
- Stale lock at `.errander-locks/` (not `/var/lib/errander/locks/`) was blocking VM execution
- Metrics server bound to `127.0.0.1` — requires `ERRANDER_UI_BIND=0.0.0.0` + `ERRANDER_UI_USER` + `ERRANDER_UI_PASSWORD` in `.env` for public-IP access

**1969 tests passing — UI-only change, no logic touched.**

## Files Changed (UI redesign)
- `errander/observability/metrics.py` (MODIFIED — full CSS rewrite to Sovereign Architect design system; font swap to Space Grotesk + Inter + JetBrains Mono; sidebar redesigned to deep indigo; Test LLM button added to Settings page)

---

## Previous Phase
**Phase D1 — Full prompt + context capture in ai_decisions (2026-05-18).**

Added three nullable columns to `ai_decisions` (`prompt_full`, `context_snapshot`, `model_params`) so every LLM decision call records the full rendered prompt, a JSON snapshot of the VM info + available actions passed to the LLM, and the model parameters used. Enables future replay and AI quality evaluation (Project D).

- `AIDecisionStore.initialize()` creates the columns idempotently via `ALTER TABLE ... ADD COLUMN` (suppresses OperationalError if they already exist — safe for both fresh installs and existing DBs).
- `_CREATE_TABLE_SQL` includes all three columns for fresh installs.
- `decisions.py` `prioritize_actions()` now passes `prompt_full=prompt`, `context_snapshot=json.dumps(...)`, `model_params=json.dumps(...)` at success and fallback call sites. The `no_llm` path passes `context_snapshot` only (no prompt was built).
- New helper `_as_float()` normalizes LLM temperature to `float | None` before JSON serialization (avoids `MagicMock` serialization failure in tests).

**1969 tests passing, 0 skipped, 0 regressions.**

## Files Changed (Phase D1)
- `errander/safety/ai_audit.py` (MODIFIED — 3 new columns in schema + dataclass + INSERT/SELECT SQL; `initialize()` calls `run_migrations()` + idempotent ALTER TABLE; `_row_to_decision()` reads cols 13/14/15)
- `errander/agent/decisions.py` (MODIFIED — `import json`, `from dataclasses import asdict`, `_as_float()` helper; success + fallback call sites pass `prompt_full`, `context_snapshot`, `model_params`; no_llm path passes `context_snapshot`)
- `tests/safety/test_ai_audit.py` (NEW — 16 tests: lifecycle, schema migration, D1 column round-trip, per-field log/get, hash_prompt)

---

## Previous Phase
**Phase A1 + B1/B2 — Durability measurement, orphan-batch scan, and VMFactsStore (2026-05-18).**

Implemented in parallel:
- **A1.2** Startup orphan-batch scanner (`errander/observability/startup_scan.py`): detects BATCH_STARTED with no terminal event in last 7 days, logs each as WARNING, increments `BATCHES_INTERRUPTED_TOTAL` counter.
- **A1.3** `--measure-durability` CLI: queries `audit_events` for batch completion rate, batch duration percentiles (p50/p95/max), approval wait stats, per-action duration stats, and interrupted-batch count. No external deps — pure SQL against existing schema.
- **A1** Prometheus counters: `AGENT_STARTS_TOTAL` and `BATCHES_INTERRUPTED_TOTAL` added to `metrics.py`. Startup instrumentation in `main.py` increments both on agent start.
- **B1** `VMFactsStore` (`errander/safety/vm_facts.py`): three Pydantic fact models (`ActionOutcomeFact`, `VMRebootPatternFact`, `ActionRejectionFact`) derived on demand from existing `audit_events` rows. No new tables.
- **B2** `OperatorAssistant` fact integration: `_build_context()` queries `VMFactsStore` when provided; `_format_prompt()` adds "Operational history facts" section; `_fallback_response()` flags low success-rate and frequently-rejected actions.

**`--measure-durability` output (current errander.sqlite, 14-day window): zero batches — BATCHES_INTERRUPTED_TOTAL stays 0.**

**1953 tests passing, 0 skipped, 0 regressions.**

## Files Changed (Phase A1 + B1/B2)
- `errander/observability/metrics.py` (MODIFIED — AGENT_STARTS_TOTAL, BATCHES_INTERRUPTED_TOTAL counters)
- `errander/observability/startup_scan.py` (NEW — scan_orphan_batches)
- `errander/observability/durability.py` (NEW — DurabilityReport, compute_durability_report, print_durability_report)
- `errander/safety/vm_facts.py` (NEW — VMFactsStore, ActionOutcomeFact, VMRebootPatternFact, ActionRejectionFact)
- `errander/models/analysis.py` (MODIFIED — action_outcomes, reboot_patterns, frequently_rejected_actions fields on FleetContext)
- `errander/agent/operator_assistant.py` (MODIFIED — vm_facts_store param, fact integration in _build_context/_format_prompt/_fallback_response)
- `errander/main.py` (MODIFIED — --measure-durability/--window-days CLI, startup instrumentation)
- `pyproject.toml` (MODIFIED — ruff per-file-ignores for tests/ and scripts/)
- `tests/observability/test_startup_scan.py` (NEW — 8 tests)
- `tests/observability/test_measure_durability.py` (NEW — 15 tests)
- `tests/safety/test_vm_facts.py` (NEW — 21 tests)
- `tests/agent/test_operator_assistant_facts.py` (NEW — 13 tests)

---

## Previous Phase
**SRE audit fix Round 2 — route_plan_vms passes enabled_actions in Send payload, manifest-derived binary checks (2026-05-17).**

Round 1 (commit 525b143) partially fixed the two SRE audit findings. Round 2 closes the remaining gaps:

1. **Blocker (Round 2)**: `_route_plan_vms()` (LangGraph fan-out router) did not include `enabled_actions` in the `Send()` payload — so `plan_vm_node` always received an empty state dict without the filter. Fixed: extracted to module-level `route_plan_vms()` (now importable and testable); passes `enabled_actions` through when present; omits the key (not `[]`) when absent so DEFAULT_PRIORITY fallback is preserved.
2. **Medium (Round 2)**: `target_validation.py` used a hand-written `_ACTION_BINARIES` table that was wrong for `disk_cleanup` and `backup_verify`. Fixed: `_binaries_for_enabled_actions()` now derives binaries from `BUILTIN_ACTIONS` manifests (single source of truth). OS-appropriate package managers filtered via `_OS_PKG_MANAGERS`.
3. **Patching manifest**: `apt-mark` was missing from `patching.py` MANIFEST `required_binaries`. Added.
4. **Tests**: `TestRoutePlanVms` class added (5 new tests) verifying Send payload carries `enabled_actions`; missing key → key omitted from payload.

**1898 tests passing, 0 skipped, 0 regressions.**

## Files Changed (SRE audit fix Round 2)
- `errander/agent/graph.py` (MODIFIED — `route_plan_vms()` module-level function; `enabled_actions` in Send payload; `_route_plan_vms` closure delegates to it)
- `errander/agent/subgraphs/patching.py` (MODIFIED — `apt-mark` added to `required_binaries`)
- `errander/execution/target_validation.py` (MODIFIED — `_OS_PKG_MANAGERS`, `_ALL_PKG_MANAGERS`, `_DOCKER_BINARIES`; manifest-derived `_binaries_for_enabled_actions()` and `_binaries_for_os()`)
- `tests/agent/test_enabled_actions_planning.py` (MODIFIED — `TestRoutePlanVms` class with 5 tests; imports for `Send`, `BatchGraphState`, `route_plan_vms`)

---

## Previous Phase
**RUN.md catch-up — --migrate-inventory and --restart-service CLI sections added (2026-05-17).**

`RUN.md` was missing two CLI additions introduced during the v1-action-opt-in plan: `--migrate-inventory` (commit 1.2) and `--restart-service`/`--unit`/`--vm`/`--vms` (commit S.3). Added both to the CLI flags table, added `## Inventory migration` and `## Service restart` sections, and added a runbook entry for service restart. All 8 v1-action-opt-in commits plus this fix are now pushed and tagged v1-rc1.

**1885 tests passing, 0 skipped, 0 regressions.**

## Files Changed (RUN.md catch-up)
- `RUN.md` (MODIFIED — --migrate-inventory section, --restart-service section, CLI flags table, runbook entry)

---

## Previous Phase
**v1-action-opt-in commit S.4 — SETUP.md service-restart section, CLAUDE.md/README update, example inventory, learning doc (2026-05-17).**

Final documentation commit for the service-restart feature. `SETUP.md` gained an `## Optional: Service restart` section (skip callout, wrapper install, inventory snippet, check-targets verification, trigger commands). `CLAUDE.md` Risk Tiers table updated (service_restart HIGH, operator-triggered note, 6 actions in v1 scope). `README.md` capability matrix and CLI example block updated. `example/inventory.yaml` now includes `service_restart` blocks with `restartable_units: []` in all three environments. `docs/learning/40-service-restart-module.md` documents the design rationale, two-layer allowlist, sub-graph node responsibilities, and testing strategy. All 8 commits of the v1-action-opt-in plan are now complete.

**1885 tests passing, 0 skipped, 0 regressions.**

## Files Changed (commit S.4)
- `SETUP.md` (MODIFIED — `## Optional: Service restart` section added)
- `CLAUDE.md` (MODIFIED — Risk Tiers table, v1 scope updated to 6 actions, operator-triggered note)
- `README.md` (MODIFIED — service_restart row in capability matrix + CLI example)
- `example/inventory.yaml` (MODIFIED — service_restart block in header comment + all 3 envs)
- `docs/learning/40-service-restart-module.md` (NEW — design walkthrough)

---

## Previous Phase
**v1-action-opt-in commit S.3 — --restart-service CLI, restartable_units validation, allowlist drift check (2026-05-17).**

`--restart-service <env> --unit <name> --vm <vm-id>|--vms <csv>` CLI added. `ActionConfig.restartable_units` field added; `service_restart.enabled: true` with empty `restartable_units` raises `ConfigError` at config-load time. `--check-targets` now SSH-reads `/etc/errander/restart-allowlist` per VM and reports drift vs inventory `restartable_units`. `tests/agent/test_approval.py` verifies HIGH tier always routes through Slack approval (strict/moderate) and the HITL guardrail covers it in relaxed mode.

**1885 tests passing, 0 skipped, 0 regressions.**

## Files Changed (commit S.3)
- `errander/config/schema.py` (MODIFIED — `restartable_units` field + ConfigError validation)
- `errander/main.py` (MODIFIED — `--restart-service`/`--unit`/`--vm`/`--vms` flags, `run_restart_service()`, allowlist drift in `run_check_targets`)
- `tests/config/test_schema_actions.py` (MODIFIED — 6 new `TestServiceRestartValidation` tests)
- `tests/test_main.py` (MODIFIED — 11 new tests: args, dry-run happy path, rejections, allowlist drift)
- `tests/agent/test_approval.py` (NEW — 7 approval guarantee tests)

---

## Previous Phase
**v1-action-opt-in commit S.2 — install-systemctl-restart-wrapper.sh + drift test (2026-05-17).**

`scripts/install-systemctl-restart-wrapper.sh` added: idempotent root install script for the restart wrapper + allowlist from positional args + sudoers entry with visudo validation. `tests/scripts/test_install_systemctl_restart_wrapper.py` added: 23 drift tests verifying wrapper exit codes, section markers, and output parseable by `parse_restart_output()`.

**1859 tests passing, 0 skipped, 0 regressions.**

## Files Changed (commit S.2)
- `scripts/install-systemctl-restart-wrapper.sh` (NEW)
- `tests/scripts/test_install_systemctl_restart_wrapper.py` (NEW, 23 tests)

---

## Previous Phase
**v1-action-opt-in commit S.1 — service_restart sub-graph + manifest + state model + audit events (2026-05-17).**

`service_restart` is the 6th built-in action (HIGH risk tier, always requires Slack approval, operator-triggered only). `errander/agent/subgraphs/service_restart.py` adds the full sub-graph (validate → snapshot → execute → verify), `parse_restart_output()` parser, and MANIFEST. `errander/models/service_restart.py` adds `RestartContext` and `ServiceRestartState`. `BUILTIN_ACTIONS` now has 6 entries. 7 new event types in `events.py`. `ActionType.SERVICE_RESTART` added.

**1836 tests passing, 0 skipped, 0 regressions.**

## Files Changed (commit S.1)
- `errander/models/service_restart.py` (NEW)
- `errander/agent/subgraphs/service_restart.py` (NEW)
- `errander/agent/subgraphs/__init__.py` (MODIFIED — added service_restart)
- `errander/models/events.py` (MODIFIED — 7 new SERVICE_RESTART_* event types)
- `errander/models/actions.py` (MODIFIED — SERVICE_RESTART ActionType + risk tier)
- `tests/agent/subgraphs/test_service_restart.py` (NEW, 18 tests)
- `tests/agent/subgraphs/test_service_restart_manifest.py` (NEW, 15 tests)
- `tests/agent/subgraphs/test_service_restart_parser.py` (NEW, 13 tests)
- `tests/agent/subgraphs/test_registry.py` (MODIFIED — count 5→6)

### P0-1 Completed (2026-05-16)
- **Commit 1**: `enrich_plan_node` in `graph.py` — SSHes each VM at plan time, populates `preview` dict per planned action with exact packages/versions (patching) and disk usage (disk_cleanup); wired between `collect_plans` and `generate_plan_artifact` so preview is in the hash. `_parse_upgradable_with_versions` added to `patching.py`. Load test call count updated. 15 new tests.
- **Commit 2**: `_format_plan_for_approval()` updated — shows exact packages with `current -> target` per patching action, disk usage for disk_cleanup; "approving categories" disclaimer removed; replaced with hash-commitment line. SPEC.md pre-P0-1 note replaced. 13 new tests.
- **1480 tests passing, 111 skipped.**
- ruff: All checks passed. mypy: 76 source files, no issues.
- `autonomous_live_apply_enabled = False` unchanged — enabling autonomous mode is a separate decision.

### Phase F Completed (2026-05-16)
- **F1**: `StoredSignalContext` dataclass; `_load_stored_signals()` in `graph.py`; `plan_vm_node` now reads disk trends, drift kinds, failure count, last patch date, login count from stores and passes to `prioritize_actions()`. 9 new tests.
- **F2**: Early sudo/wrapper readiness check in `validate_targets_node` immediately after OS detection; `TARGET_READINESS_BLOCKED` EventType added; blocks VMs early rather than mid-batch. 8 new tests.
- **F3**: `_check_escalation()` in `probe.py` evaluates critical signals (disk ≥90%, 2+ failed services, drift+logins); `DigestReport.escalation_needed`/`escalation_reasons`; `render_digest_report()` escalation header; `main.py` posts separate Slack alert. 14 new tests.
- **F4**: `post_cleanup_disk_gate_node` in `vm_graph.py` wired between `dispatch_action` and `check_more_actions`; re-checks / disk after disk_cleanup/log_rotation before patching; blocks (skip) at ≥95%, warns at 90–94%; `DISK_GATE_BLOCKED` EventType. 12 new tests.
- **1582 tests passing, 111 skipped.**
- ruff: All checks passed. mypy: 77 source files, no issues.

### Phase E Completed (2026-05-16)
- **E2**: `ElkClient` wired into `probe_vm`, `--ask`, `--probe-now`; `elk_errors` on `ProbeVMResult`.
- **E3**: `probe_vm` now SSHes `journalctl -p err` and `systemctl --failed`; `_parse_journal_errors` / `_parse_failed_services` helpers; `journal_errors` + `failed_services` on `ProbeVMResult`. 11 new tests.
- **E4**: `sources_used` on `FleetContext`; `data_sources` on `AssistantResponse`; `--ask` prints "Sources consulted:" with tips for missing sources. 8 new tests.
- **1570 tests passing, 111 skipped.**

### Phase C Completed (2026-05-16)
- **Commit 1**: `errander/integrations/prometheus.py` — `PrometheusClient` (3 node_exporter metrics, 5s timeout, best-effort); `prometheus_base_url` in Settings; `prometheus_metrics` field on `VMSignalSummary` + `ProbeVMResult`; `all_prometheus_metrics` property on `DigestReport`. 10 new tests.
- **Commit 2**: Wired into `probe_vm()`, `run_env_probe()`, `_build_context()`, `_format_prompt()`, `render_digest_report()`, and all 3 main.py call sites. 12 new tests.
- **1452 tests passing, 111 skipped.**
- ruff: All checks passed. mypy: 76 source files, no issues.

### Phase D Completed (2026-05-15)
- **Commit 1**: `errander/agent/operator_assistant.py` — `OperatorAssistant` class with `_build_context()`, `_format_prompt()`, `_fallback_response()`; `errander/models/analysis.py` — `AssistantResponse`, `VMSignalSummary`, `FleetContext`. 16 new tests.
- **Commit 2**: `--ask "question"` CLI flag + `run_ask_query()` in `main.py`; LLM wired when `llm_base_url` set, fallback when absent. 10 new tests.
- **1430 tests passing, 111 skipped.**
- ruff: All checks passed. mypy: 75 source files, no issues.
- Layer A invariant verified: zero `SandboxExecutor`/`FileLocker`/`ApprovalManager` imports in `operator_assistant.py`.

### Phase B Completed (2026-05-15)
- **Commit 1**: `errander/agent/probe.py` — standalone probe runner calling existing SRE nodes directly (no new LangGraph); `DigestReport` + `ProbeVMResult` models; `render_digest_report()` deterministic Slack renderer; 3 new event types (`DAILY_PROBE_*`). 16 new tests.
- **Commit 2**: `signals` field in `ScheduleSchema`; `post_digest()` on `SlackClient`; `run_env_probe_main()` and `--probe-now <env>` CLI; probe cron job wired into scheduler loop. 9 new tests.
- **Fix**: `probe_vm()` now calls `discover_node` first (SSH pre-check + `vm_info` population), matching vm_graph node ordering exactly. Signal nodes never run for unreachable VMs.
- **1404 tests passing, 111 skipped.**
- ruff: All checks passed. mypy: 73 source files, no issues.

### Phase A.5 Completed (2026-05-15)
- `uv run ruff check errander/` → **All checks passed** (382 → 0 errors)
- `uv run mypy errander/` → **Success: no issues found in 72 source files** (112 → 0 errors)
- 1378 tests passing, 111 skipped, 0 regressions
- 28 `# type: ignore` comments total (down from ~30 pre-existing before this session)
- line-length bumped 100 → 120; `errander/web/` exempt from E501 (inline HTML/CSS templates)
- Real bug fixed: `run_bootstrap_known_hosts` used `load_inventory` (returns `list[VMTarget]`) instead of `validate_inventory` (returns `InventoryConfig`)
- APScheduler import-untyped suppressed via `[[tool.mypy.overrides]]` in pyproject.toml

### Phase A Completed (2026-05-15)
- **Commit 1**: `/usr/bin/env` removed from apt privileged commands; `apt --simulate` drops sudo; `SUDO_PREFLIGHT_FAILED` event type added; `sudo_preflight_node` migrated. 9 new tests.
- **Commit 2**: `docker_command_mode: wrapper | direct_sudo | disabled` per environment; `docker_prune.py` refactored; `parse_assess_output()` helper; plumbed through vm_graph/graph/main; preflight mode-aware; SETUP.md Docker section updated. 16 new tests.
- **Commit 3**: `--check-targets <env>` CLI reads every target VM and reports binary/sudo/wrapper readiness; supported distro matrix in SETUP.md and README. 11 new tests.
- **1378 tests passing, 111 skipped.**

### Two-Layer AI Architecture Locked In (2026-05-15)
- **`docs/AI-ARCHITECTURE.md`**: Canonical safety model document. Layer A (Operator Assistant — LLM + MCP + CLI + Skills) recommends; Layer B (Safe Execution — deterministic Python) executes. Strict audited boundary.
- **README.md**: Headline changed to "Deterministic maintenance automation with an AI-assisted operator layer for Linux fleets." Added Non-Goals section.
- **CLAUDE.md**: Added "AI Safety Invariant" section with anchor phrases.
- **SPEC.md**: Added "AI Safety Model — Two Layers" summary with link to canonical doc.
- **SRE sign-off**: validated in `ai_sre_audit_v2.md` "Two-Layer AI Architecture Validation" section. SRE's anchor phrase: *"MCP belongs in the operator brain, not in the execution hands."*

## Next
- P0-2: Deferred replay of exact artifact (window-opener replays approved plan, no re-planning)
- Note: `autonomous_live_apply_enabled = False` stays False after P0-2. Flipping it is a separate conscious decision.
- Phase D: Operator Assistant Layer (MCP / CLI / Skills for investigation and recommendation)

## Completed

### SRE HITL Guardrails — Fourth-Pass Fixes (2026-05-14)
- **Fail-closed**: `approval_gate_node` returns `approved=False` when `require_live_approval=True` but `approval_manager is None` — no silent auto-approve
- **`autonomous_live_apply_enabled` enforced**: gate is real — when False, any `require_live_approval=False` call is overridden to True
- **`require_live_approval` hardcoded**: not configurable via settings.yaml/env until P0-1/P0-2 done
- 1310 tests passing, 111 skipped

### SRE HITL Guardrails (2026-05-14)
- **`require_live_approval: bool = True`** in Settings — ALL live batches require human Slack approval regardless of policy tier; overrides relaxed/moderate auto-approve
- **`autonomous_live_apply_enabled: bool = False`** in Settings — product-level gate documenting HITL-only posture until P0-1/P0-2 done
- **Default policy → `strict`** — schema.py and graph.py fallback both changed from `moderate`
- **Honest Slack approval message** — disclaimer that operator approves action categories, not pinned commands; deferred batches flagged as re-approval required
- **Deferred execution re-approval** — `_window_opener` triggers fresh re-plan + fresh human approval at window time; no longer silently replays old approval
- **SPEC.md** — removed false exactness claims from `PlannedAction`; added honest pre-P0-1 limitation note
- 1308 tests passing, 111 skipped

### AI SRE Audit v2 — Second-Pass Residuals (2026-05-14)
- **Residual P0-3**: log_rotation tracks logrotate failure independently; per-file fallback clears it only when all files rotated AND large_files was non-empty
- **P2-3 full**: `check_connectivity` now has `strict_host_keys=True` default — refuses without `known_hosts_path`, consistent with `SSHConnectionManager`
- **mypy**: `action_params` extraction uses `isinstance(raw, dict)` (type-safe); `_get_connection_params` uses `str()` cast (removes wrong ignore tags); mypy errors 142 → 112
- 1307 tests passing, 111 skipped

### AI SRE Audit v2 Remediation (2026-05-14)
- **P0-3**: `execute_node` in docker_prune, disk_cleanup, log_rotation now propagate command failures to status; `SUCCESS` only when all commands succeed
- **P0-3**: `AptManager`/`DnfManager` `upgrade_all` capture apt/dnf exit code; unhold/versionlock-delete failures suppressed with `|| true`
- **P0-4**: `rollback_node` returns `ROLLED_BACK` on success, `ROLLBACK_FAILED` on failure — audit trail can now distinguish all 3 outcomes
- **P1-3**: `validate_no_pkg_lock` fail-closed in live mode on SSH probe failure; dry-run keeps permissive behavior
- **P1-5**: `drift_baseline_node` skips `compare_and_save` in dry-run — no operational state mutation
- **P2-3**: `check_connectivity` follows same known_hosts policy as `SSHConnectionManager`
- **P2-1**: `BACKUP_VERIFY` reclassified to LOW (read-only); moved to front of `DEFAULT_PRIORITY`
- **P1-1**: disk_cleanup, log_rotation, docker_prune runners read `whitelist_paths`/`tmp_age_days`/`log_paths`/`aggressive` from approved action params
- **P1-4**: `approval_timeout_seconds` + `approval_poll_interval_seconds` wired from settings into `await_dual_approval`
- 1305 tests passing, 111 skipped

### PR-2: SRE Signal Aggregation + BatchReport Rendering (2026-05-13)
- **`disk_snapshot_node` serialization** in `vm_graph.py`: now includes `window_start` and `window_end` as ISO strings (needed to reconstruct `DiskGrowth` objects for reports)
- **`_merge_sre_list` reducer** in `graph.py`: append-only reducer for SRE signal lists
- **`BatchGraphState` SRE fields**: `sre_disk_growth`, `sre_drift_changes`, `sre_failed_logins` (all Annotated with `_merge_sre_list`)
- **`run_vm_node`** updated to extract and return all 3 SRE signals from final vm_graph state alongside `vm_results`
- **`render_batch_report(report: BatchReport) -> str`** in `reporting.py`: deterministic Slack-formatted renderer with 7 sections (action results, preflight blocks, service regressions, reboot required, drift changes grouped by kind, disk growth, failed logins); sections omitted when empty
- **`generate_report_node`** refactored to build `BatchReport` from aggregated state (deserializes SRE dicts → typed objects) and call `render_batch_report()`, replacing LLM-powered `generate_report()` call
- **47 tests** in `test_reporting.py`; 1,283 total passing
- Learning doc: `docs/learning/31-sre-signal-aggregation.md`

### PR-1.5: Configuration Drift Detection + Failed SSH Logins (2026-05-13)
- **`errander/safety/drift_checks/authorized_keys.py`**: `authorized_keys_command()` — single-round-trip shell loop with `USER:` section delimiters; `parse_authorized_keys()` — parses user→keys sections; `capture_authorized_keys()` — SSH probe, scope_key=username (per-user independent baselines)
- **`errander/safety/drift_checks/sudoers.py`**: concatenates `/etc/sudoers` + `sudoers.d/*` in one SSH call; strips comments/blanks, sorts
- **`errander/safety/drift_checks/listening_ports.py`**: `ss -tlnp` with `netstat` fallback; strips header, sorts data lines
- **`errander/safety/drift_checks/scheduled_jobs.py`**: user crontab + `/etc/crontab` + `/etc/cron.d/*`; strips comments/blanks, sorts
- **`errander/safety/drift_checks/__init__.py`**: re-exports all four `capture_*` functions
- **`errander/execution/failed_logins.py`**: `failed_logins_command(window_hours)` — journald + auth.log fallback; `parse_failed_logins()` — regex extracts username+IP, Counter.most_common(5); `detect_failed_logins()` — SSH probe
- **`drift_baseline_node`** in `vm_graph.py`: runs enabled drift checks via deferred import; `compare_and_save` per capture; emits `DRIFT_KIND_CHANGED` or `DRIFT_KIND_BASELINE_SAVED`; diffs truncated to `diff_max_lines`
- **`failed_logins_node`** in `vm_graph.py`: probes for failed SSH logins; emits `FAILED_SSH_LOGINS_OBSERVED` when total > 0
- **Generalized SRE chain wiring** in `build_vm_graph`: `sre_snapshot_nodes` list drives discover → [disk_snapshot] → [drift_baseline] → [failed_logins] → drift_check; all nodes optional
- **97 new tests** across 6 files; 1,245 total passing
- Learning doc: `docs/learning/30-drift-detection.md`

### PR-1.4: Disk Growth Trend Detection (2026-05-13)
- **`errander/execution/disk_trend.py`**: `disk_bytes_command()` — `df -B1 2>/dev/null || true`; `parse_df_bytes()` — skips pseudo-filesystems (tmpfs/devtmpfs/udev/…), non-integer values, zero-total; `compute_growth_alert(datapoints, threshold_pct)` — compares oldest→newest used%, returns `DiskGrowth` when delta ≥ threshold; `detect_growth_alerts()` — queries distinct mountpoints then window per mountpoint; `record_and_detect_disk_growth()` — SSH probe with `dry_run=False`, records batch, returns alerts
- **`disk_snapshot_node`** in `vm_graph.py`: runs between discover and drift_check; calls `record_and_detect_disk_growth`; stores alerts in `disk_growth_alerts`; conditionally wired — when `disk_history_store=None` (default) graph is unchanged
- **24 new tests** in `tests/execution/test_disk_trend.py`; 1,148 total passing
- Learning doc: `docs/learning/29-disk-growth-trend.md`

### PR-1.3: Service Health Regression Detection (2026-05-13)
- **`errander/execution/service_check.py`**: `ServiceStatus` frozen dataclass; `service_status_command(services)` — shell loop using `systemctl is-active` with absent-binary fallback (`unknown`); `parse_service_statuses()` fills missing services as unknown; `find_regressions(pre, post)` returns services that were active before but not after; `check_services()` SSH probe best-effort (SSH failure → empty dict, no false regressions)
- **`service_health_pre_node`** in `patching.py`: runs after snapshot, before execute; probes `critical_services`; stores `service_pre_snapshot: dict[str, str]`; no-op when critical_services empty
- **`service_health_post_node`** in `patching.py`: runs after last SRE node; compares pre/post snapshots; emits `SERVICE_HEALTH_REGRESSION` with regressed service names in metadata; no-op when no pre-snapshot
- **`build_patching_subgraph`** gains `sre_service_check` param; local routing closure chains `service_pre → execute` and `[reboot_check →] service_post → END`; all 3 SRE flag combinations compile correctly
- **47 new tests** across 2 files (test_service_check.py, TestServiceHealthPreNode + TestServiceHealthPostNode + TestBuildSubgraphWithServiceCheck in test_patching.py); 1,124 total passing
- Learning doc: `docs/learning/28-service-health-checks.md`

### PR-1.2: Reboot-Required Detection (2026-05-13)
- **`errander/execution/reboot_check.py`**: `RebootStatus` frozen dataclass; `reboot_required_command(os_family)` — Debian/Ubuntu uses `/var/run/reboot-required` flag file + pkg list; RHEL uses `needs-restarting -r` with absent-binary fallback (`EXIT=unknown` → no reboot); `parse_reboot_status()` pure parser for both OS variants; `detect_reboot_required()` SSH probe with best-effort (failure → no reboot)
- **`reboot_check_node`** in `patching.py`: runs after verify (live success only); persists to `VMStateStore.set_needs_reboot()` when provided; emits `REBOOT_REQUIRED_DETECTED` audit event; returns `reboot_status_detected: bool`
- **`build_patching_subgraph`** gains `vm_state_store` + `sre_reboot_check` params; local `_route_verify` closure routes success → `reboot_check` when enabled; dry-run batches exit at `DRY_RUN_OK` (never reaches reboot check)
- **`format_reboot_required_section(vms)`** in `reporting.py`: Slack-ready section header + bullet list; truncates pkg lists > 5 with "+N more"; returns `""` for empty input
- **46 new tests** across 3 files (test_reboot_check.py, test_patching.py TestRebootCheckNode + TestBuildSubgraphWithRebootCheck, test_reporting.py TestFormatRebootRequiredSection); 1,077 total passing
- Learning doc: `docs/learning/27-reboot-detection.md`

### PR-1.1: Package Lock Detection (2026-05-13)
- **`PackageManager.detect_lock()`**: new abstract method + implementations in `AptManager` (fuser-based, 3 dpkg/apt lock files) and `DnfManager` (pid-file-based, dnf.pid + yum.pid)
- **`LockHolder` dataclass** + **`parse_lock_output()`** + **`validate_no_pkg_lock()`** in `validators.py`: runs detect_lock via SSH, parses `pid=N cmd=X` output; SSH failure treated as clear (best-effort)
- **`preflight_lock_node`** in `patching.py`: async, runs before validate; BLOCKED → END (never reaches assess/upgrade); emits `PREFLIGHT_LOCK_DETECTED` or `PREFLIGHT_LOCK_CLEAR` when `audit_store` provided
- **`build_patching_subgraph`** gains `audit_store`, `batch_id`, `sre_preflight_lock_check` params; existing callers unaffected (defaults: store=None, check=True)
- **35 new tests** across 3 files (commands, validators, patching); 1031 total passing

### PR-G: SRE Groundwork (2026-05-13)
- **`ActionStatus.BLOCKED`**: new enum value for pre-flight gate deliberate non-execution
- **8 new EventType values**: `PREFLIGHT_LOCK_DETECTED`, `PREFLIGHT_LOCK_CLEAR`, `REBOOT_REQUIRED_DETECTED`, `SERVICE_HEALTH_REGRESSION`, `DISK_USAGE_CAPTURED`, `DRIFT_KIND_BASELINE_SAVED`, `DRIFT_KIND_CHANGED`, `FAILED_SSH_LOGINS_OBSERVED`
- **`VMTarget.critical_services`**: `tuple[str, ...]` field with host-overrides-env inheritance
- **`errander/safety/migrations.py`**: numbered idempotent migration runner; 4 migrations (audit_events, vm_state, vm_baselines, vm_disk_history); splits SQL by ";" for PostgreSQL portability
- **`errander/safety/vm_state.py`**: `VMStateStore` — UPSERT-based per-VM mutable state (needs_reboot flag, uptime)
- **`errander/safety/baselines.py`**: `BaselineStore` + `DriftCheck` Protocol — per-kind drift baselines with unified diff and configurable retention; `ORDER BY captured_at DESC, id DESC` for deterministic latest-row
- **`errander/safety/disk_history.py`**: `VMDiskHistoryStore` — disk usage history with 90-day prune, `get_window()` for trend detection
- **`errander/models/reports.py`**: `BatchReport` + 6 supporting frozen dataclasses (PreflightBlock, VMRebootStatus, ServiceRegression, DiskGrowth, DriftChange, FailedLoginSummary)
- **SRE config block**: `SRESignalSettings` dataclass hierarchy (DiskGrowthSettings, DriftSettings, FailedSSHLoginsSettings) wired into `Settings`; schema validated via `SRESignalsSchema` Pydantic classes
- **`AuditStore` migrated**: `initialize()` now calls `run_migrations()` instead of inline DDL
- **`errander/safety/drift_checks/__init__.py`**: empty package placeholder for Phase 2
- **84 new tests** (migrations, vm_state, baselines, disk_history, reports, critical_services inheritance); 996 total passing
- All new files pass mypy strict + ruff

### Phase 1.1: Project Foundation
- Full project scaffold — Option C architecture (Parent Orchestrator + Fan-Out + Sub-Graphs)
- Data models, state dataclasses, strategy pattern stubs, policy system
- All module stubs created, test structure mirroring src

### Phase 1.2: Core Infrastructure
- Settings loader (env vars + YAML), schema validation, inventory loader with inheritance
- Audit logging (async SQLite), SSH execution (connection pooling + retry)
- OS detection, sandbox/dry-run wrapper, file-based VM locking

### Phase 1.3: Disk Cleanup (sub-graph complete)
- **Sub-graph**: LangGraph StateGraph with 4 nodes: validate → assess → execute → verify
- **Whitelist enforcement**: Hardcoded `ALLOWED_CLEANUP_PATHS` — `/tmp`, `apt-cache`, `yum-cache`, `journal`, `orphaned-deps`. Non-whitelisted paths are BLOCKED immediately.
- **Dry-run mode**: Uses simulate commands (e.g., `apt-get autoremove --simulate`) or synthetic `[DRY-RUN]` results
- **Live mode**: Real cleanup commands — `find /tmp -delete`, `apt-get clean`, `journalctl --vacuum-time`, `autoremove`
- **OS-aware**: AptManager for Ubuntu/Debian, DnfManager for RHEL — command generation fully implemented
- **Verification**: Post-cleanup `df -h` comparison against pre-cleanup baseline
- **Tests**: 31 tests covering whitelist, validation, routing, assess, execute, verify, sub-graph integration
- Pending: real VM dry-run test (needs infrastructure)

### Phase 1.4: Per-VM Graph
- **vm_graph.py**: Full LangGraph lifecycle — lock → discover → plan → dispatch → check_more → audit → unlock
- **Lock node**: FileLocker.acquire/release with graceful error handling — always releases
- **Discovery node**: detect_os() via SSH — populates VMInfo (OS, disk, docker, packages, uptime)
- **Plan node**: prioritize_actions() with optional LLM, hardcoded fallback
- **Dispatch node**: Action loop with index cursor — currently dispatches disk_cleanup sub-graph, skips others (Phase 2)
- **Audit node**: Writes ActionResult events to AuditStore (SQLite)
- **Tests**: 28 tests covering all nodes, routing, error paths, full integration

### Phase 1.5: Batch Orchestrator
- **graph.py**: Full LangGraph batch graph — init → window → validate_targets → fan_out → run_vm → collect → report
- **Send() fan-out**: Each healthy target dispatched independently via LangGraph `Send()` from conditional edge function
- **Target validation**: SSH connectivity check (echo ok) — partitions into healthy/failed
- **Result aggregation**: Append-only reducer (`Annotated[list, _merge_vm_results]`) for concurrent writes
- **Report generation**: Template-based with optional LLM; `validate_window_node` is a stub (wired to real window check before Phase 1.8)
- **Tests**: 21 tests covering all nodes, routing, fan-out, full integration

### Phase 1.4/1.5 Support: Decisions Module
- **decisions.py**: All three decision functions accept optional `llm_client` — LLM tried first, hardcoded fallback on `None`/failure
  - `prioritize_actions()`: filter by VM state (docker, pending packages) + sort by risk tier
  - `analyze_failure()`: heuristic-based retry/rollback/escalate recommendation
  - `generate_report()`: template-based Slack-ready report, `/no_think` mode with LLM
- **Tests**: 23 tests covering filtering, prioritisation, failure analysis, report generation

### Phase 1.6: Integrations
- **LLM client** (`errander/integrations/llm.py`): Full `LLMClient` — `complete()` with thinking/no_think modes, structured JSON via Pydantic, retry on transient errors, `health_check()`. Wired into `decisions.py` — all three decision functions now accept optional `llm_client` parameter and fall back to hardcoded logic when `None` or LLM unreachable.
- **Slack client** (`errander/integrations/slack.py`): Full `SlackClient` — `post_message()` returns `ts`, `get_reactions()` polls by `ts`, `post_alert()` convenience wrapper. Rate limiting handled with one automatic retry respecting `Retry-After`. All I/O via outbound HTTPS, no inbound webhooks.
- **Approval gate** (`errander/safety/approval.py`): `request_approval()` formats and posts dry-run plan to Slack. `poll_approval()` polls every N seconds — ❌ takes priority over ✅, timeout auto-rejects, transient Slack errors skip the poll without aborting.
- **Prometheus metrics** (`errander/observability/metrics.py`, `tracking.py`): `REGISTRY` with 7 metrics (actions_total, action_duration_seconds, batch_duration_seconds, ssh_errors_total, llm_requests_total, approval_wait_seconds, vm_lock_held_seconds). `start_metrics_server()` launches aiohttp app serving `/metrics` and `/health`. `tracking.py` provides `record_action_result()`, `record_ssh_error()`, `record_llm_outcome()`.
- **Tests**: 74 tests — 23 LLM, 10 Slack client, 21 approval gate, 20 metrics

### Pre-Phase 1.8: Wiring + Entry Point
- **`validate_window_node` wired** (`errander/agent/graph.py`): No longer a stub. Calls `check_window_from_config()` — blocks batch if outside window (sets `error` → short-circuits to `generate_report`). `force=True` bypasses with warning. `build_batch_graph()` now accepts optional `window: MaintenanceWindow | None`.
- **`main.py` implemented**: Full entry point — CLI args, config loading, component wiring (SSH, executor, locker, Slack, LLM), `--run-now` mode, scheduler loop with per-env cron jobs, graceful shutdown on SIGTERM/SIGINT.
- **`EnvironmentSchema`** extended with `maintenance_timezone: str = "UTC"`.
- **`_build_maintenance_window()`** helper parses `"HH:MM-HH:MM"` window strings from inventory.
- **Tests**: 21 graph tests (now 25), 17 main.py tests

### Phase 1.7: Config & Scheduling
- **Maintenance windows** (`errander/scheduling/windows.py`): `is_within_window()` handles normal and overnight windows, timezone-aware via `zoneinfo`. `MaintenanceWindow` dataclass with validation. `check_window_from_config()` convenience wrapper.
- **Scheduler** (`errander/scheduling/scheduler.py`): `MaintenanceScheduler` wraps `AsyncIOScheduler` — `add_maintenance_job()` registers cron-triggered async callbacks, `list_jobs()` summarises registered jobs, `start()`/`stop()` manage lifecycle. Misfire grace: 600s, coalesce enabled.
- **Example configs** (`example/inventory.yaml`, `example/settings.yaml`): Reference configuration files covering production/staging/dev environments with annotated comments.
- **Tests**: 36 tests — 25 windows, 11 scheduler

### vLLM Deployment
- **`deploy/vllm/docker-compose.yml`** — production Docker Compose for vLLM: NVIDIA GPU passthrough, the exact serve command from CLAUDE.md, `restart: unless-stopped`, model volume mount, healthcheck (180s start period for model load), 7-day log retention
- **`deploy/vllm/.env.example`** — all tunable vars: `MODEL_ID`, `HF_TOKEN`, `MAX_MODEL_LEN`, `GPU_MEM_UTIL`, `VLLM_PORT`, `MODEL_CACHE_DIR`
- **`LLMClient.check_endpoint()`** — detailed health check: reachability, model list, test completion with round-trip latency
- **`--check-llm` CLI flag** in `main.py` — `uv run python -m errander --check-llm` prints status, model IDs, and latency without starting the agent

### Dual-Channel Approval (Slack + Web UI)
- **`ApprovalManager`** (`errander/safety/approval.py`): In-memory store for pending approvals. `PendingApproval` dataclass with `asyncio.Event` for signalling. `register()`, `decide()` (idempotent), `wait_for_decision()` (timeout auto-rejects), `get_pending()`, `get_history()`.
- **`await_dual_approval()`**: Races Slack reaction polling against UI button click using `asyncio.wait(FIRST_COMPLETED)`. If Slack post fails, falls back gracefully to UI-only mode. Cancels the slower channel when either decides.
- **`GET /ui/approvals`**: Lists pending approvals with report excerpt and Approve/Reject buttons. Shows recent decision history table. Auto-refreshes every 15s. Red badge count in nav when pending > 0.
- **`POST /ui/approvals/{id}/approve|reject`**: Form submit handler — calls `manager.decide()`, redirects back to list. Returns 503 if manager not connected. Idempotent for unknown batch IDs.
- **Dashboard** updated: new "Pending approvals" card (red highlight when > 0, links to `/ui/approvals`). "Approvals" link added to nav bar across all pages.
- **Fixed `main.py`**: `--env` and `--unknown-env` validation now happens BEFORE the metrics server starts (port binding). Previously, 2 tests failed with port 10048 binding error.
- **Tests**: 50 approval tests (27 unit + 23 UI route tests), all 479 tests passing.

### Playwright UI Tests (25 tests)
- `pytest-playwright` added to dev dependencies, Chromium browser installed
- Server fixture: aiohttp server starts in a background thread with its own event loop + seeded `:memory:` SQLite — one server for all 25 tests
- **Dashboard** (6): page loads, Running status, event count heading, both batches visible, batch link navigates, all nav links present
- **Batch list** (3): page loads, both batches listed, link navigates to detail
- **Batch detail** (8): page loads, event count, completed/failed event types visible, detail text, VM link, back link, nonexistent batch
- **VM history** (6): page loads, event count, action detail, back-to-batch link, VM ID with slash in URL, nonexistent VM
- **Endpoints** (2): `/health` returns "ok", `/metrics` serves Prometheus format

### Web UI (built into aiohttp server)
- Extended `start_metrics_server()` with optional `audit_store` parameter
- 4 new routes on the same port 9090 (no new process, no new port):
  - `GET /ui` — Dashboard: running status, total event count, recent batches table, auto-refresh 30s
  - `GET /ui/batches` — Full batch history table (last 100), each row links to detail
  - `GET /ui/batches/{batch_id}` — All events for one batch with colour-coded event types
  - `GET /ui/vms/{vm_id}` — Full VM history across all batches (vm_id supports slashes e.g. `dev/web-01`)
- Styled with Pico.css (CDN) — zero custom CSS, pure semantic HTML
- Event types colour-coded: green (completed), red (failed), blue (started)
- All pages link to each other: batch → VM, VM → batch, nav bar everywhere
- `web.AppKey` typed key for `audit_store` on aiohttp app (no string key warnings)
- Tests: 415 passing (no new tests — user testing manually)

### SQLite Audit Integration (native, no MCP)
- **`AuditStore.get_events()`** extended with `action_type` filter — all four filters (batch_id, vm_id, event_type, action_type) can be combined freely
- **`AuditStore.get_recent_batches(limit)`** — returns batch summaries: batch_id, started_at, event_count, vm_ids (distinct)
- **`--audit` CLI mode** in `main.py`: `uv run python -m errander --audit [--batch-id X] [--vm-id Y] [--action-type Z] [--event-type T] [--last N] [--batches]`
- **Integration tests** (`tests/safety/test_audit_integration.py`, 21 tests):
  - `action_type` filter correctness (6 tests)
  - `get_recent_batches()` correctness (7 tests)
  - VM graph audit trail written correctly via full graph run with mocked SSH (3 tests)
  - Audit CLI query functions via `run_audit_query()` (5 tests)
- **Tests**: 415 total passing

### Design Review (10 fixes)
- Hardened kernel exclusion (frozenset + fnmatch, reject attempts to weaken)
- Disk cleanup whitelist enforcement in validate node (not just assess)
- Approval gate enforces risk tiers per policy (not blanket auto-approve)
- Rollback architecture: version snapshot before patching, batch rollback on failure
- Backup verify uses NEEDS_MANUAL status (not just SUCCESS/FAILED)
- Docker prune validates docker availability before proceeding
- Log rotation rejects paths outside `/var/log`
- All sub-graphs use ActionStatus enum consistently
- Dispatch handles unknown action types gracefully (ValueError catch)
- Audit integration tests patched for all 5 action types

### Phase 2: All Action Sub-Graphs (complete)
- **Log rotation** (`errander/agent/subgraphs/log_rotation.py`): Path validation → find oversized files → logrotate or manual gzip+truncate → verify. Idempotent via `nothing_to_do`. 28 tests.
- **Docker prune** (`errander/agent/subgraphs/docker_prune.py`): Docker availability check → count dangling images + stopped containers → `docker system prune -af` → verify df. Idempotent. 18 tests.
- **Patching** (`errander/agent/subgraphs/patching.py`): Kernel exclusion (mandatory frozenset + fnmatch) → list upgradable → version snapshot → upgrade → verify versions. Idempotent. 24 tests.
- **Backup verify** (`errander/agent/subgraphs/backup_verify.py`): Read-only — no execute node. Check exists/recent/non-zero for each backup path. Flags MISSING/STALE/EMPTY. 14 tests.
- **VM graph wiring** (`errander/agent/vm_graph.py`): All 5 sub-graphs compiled once and dispatched via `_run_*` helpers. Unknown action types handled gracefully.
- **README.md**: Comprehensive project README with architecture, how-it-works, safety gates, quick start, configuration, observability, vLLM deployment.

### Phase 3: Hardening (complete)

- **Rolling updates** (`errander/agent/graph.py`): Wave-based fleet dispatch. `_partition_into_waves()` splits healthy targets by `rolling_update_percentage`. New graph topology: `validate_targets → prepare_waves → dispatch_wave → run_vm → check_wave_health → (loop|collect)`. Defaults to 100% (single wave) — backward-compatible.
- **Canary logic** (`errander/agent/graph.py`): When `canary_enabled=True`, `prepare_waves_node` forces wave 0 = 1 VM. `check_wave_health_node` uses the stricter `canary_health_check_command` for wave 0; any failure aborts the entire rollout (`canary_passed=False`).
- **Drift detection** (`errander/safety/drift.py`, `errander/agent/vm_graph.py`): New `drift_check` node inserted between `discover` and `plan_actions`. Compares discovered VM state against SQLite-stored baseline (OS version, disk usage >20%, docker availability, reboot detection, package count >5). Saves baseline after each successful run. Disabled by default.
- **New metrics**: `errander_wave_health_checks_total` counter (labeled by wave index and outcome).
- **Settings wired**: 7 new fields in `AgentSettingsSchema` and `Settings` dataclass, env var overrides, and `config/settings.yaml` + `example/settings.yaml` updated.
- **Phase 3 edge-case hardening** (5 steps, 25 new tests, 677 total):
  - **Sub-graph exception safety** (`errander/agent/vm_graph.py`): All 5 `_run_*` helpers wrap `ainvoke()` in try/except — `(ConnectionError, OSError, TimeoutError)` + bare `Exception # noqa: BLE001`. Exceptions return a FAILED result dict; the `release_lock_node` always executes. `audit_results_node` wraps `save_baseline()` so a drift DB error never aborts the batch.
  - **Batch orchestrator exception safety** (`errander/agent/graph.py`): `run_vm_node`'s `ainvoke()` wrapped in bare `Exception` guard returning a FAILED vm_results entry.
  - **Audit resilience** (`errander/safety/audit.py`): `log_event()` retries once on `aiosqlite.OperationalError` (with 100ms backoff), then swallows persistent `OperationalError` and `aiosqlite.Error` so audit failures never abort a live batch.
  - **Atomic file locking** (`errander/safety/locking.py`): `acquire()` uses `os.O_CREAT | os.O_EXCL` for race-free creation; stale-lock overwrites use `os.replace()` (atomic on same filesystem). `_write_lock_atomic()` helper writes to `.tmp` then renames atomically.
  - **Settings bounds validation** (`errander/config/schema.py`): `@field_validator` on `rolling_update_percentage` [1–100], `wave_failure_threshold`/`fleet_failure_threshold` [0.0–1.0], and all timeout fields [1–86400].
  - **SSH hardening** (`errander/execution/ssh.py`): Timeout handler clears stale connection from pool; `None` exit_status maps to 255 (SSH convention).
  - **Sub-graph empty-output guards**: `snapshot_node` (patching) fails on empty package snapshot; `assess_node` (disk_cleanup, docker_prune) fails on empty `df`/`wc -l` stdout; `assess_node` (log_rotation) fails on non-zero `find` exit code.

### Load Testing + Playwright Approvals (complete)
- **`tests/agent/test_load.py`** (20 tests): `TestLargeFleetPartitioning` (7 pure-unit wave math tests at 100–200 VMs), `TestFleetBatchGraph` (7 integration tests — 10-VM fleet, rolling waves, wave abort at boundary, canary abort, crash recovery), `TestConcurrentLockOperations` (6 tests — 50-coroutine race, 20-VM lifecycle, stale lock recovery, force release, serial waves).
- **`tests/ui/test_approvals_playwright.py`** (22 Playwright tests): Module-scoped aiohttp server with `ApprovalManager` pre-seeded with 5 pending approvals. `TestApprovalsPage` (7), `TestApprovalsNavigation` (4), `TestDashboardWithPendingApprovals` (4), `TestApproveAction` (2), `TestRejectAction` (2), `TestApprovalsBadgeAcrossPages` (3).
- **Total tests: 719** — all passing, lint clean.

### Phase 4: LLM Flexibility + Secrets Encryption + UI Config (complete)

- **Phase A — LLM provider flexibility**: Removed hardcoded `Qwen/Qwen3-8B-AWQ` and `/no_think` prefix. `LLMClient` now accepts `model: str` and `temperature: float`. Works with any OpenAI-compatible API (vLLM, Ollama, OpenAI, Anthropic via proxy, Groq). `decisions.py` updated — no more `thinking=True/False`. Provider docs in `docs/LLM-PROVIDERS.md`.

- **Phase A.5 — Secrets encryption foundation**:
  - `SecretsManager` with Fernet AES-128-CBC + HMAC-SHA256, `enc:v1:<token>` format
  - `--generate-secrets-key` and `--encrypt VALUE` CLI commands
  - YAML config decryption on load (`_decrypt_yaml_strings`)
  - `SecretsRedactingFilter` log filter scrubs API keys, Slack tokens, `enc:v1:` blobs from all log output
  - 24 `test_secrets.py`, 9 `test_redaction.py`, 6 `test_secrets_loading.py` tests

- **Phase B — UI settings + inventory management**:
  - `OverridesStore` (SQLite) — two tables: `settings_overrides` and `inventory_overrides`
  - Settings precedence: env > DB (UI) > YAML > default. `load_settings()` accepts pre-fetched `db_overrides`.
  - `GET/POST /ui/settings` — runtime LLM/approval setting changes. Source indicators (env=locked, db=blue, yaml=green). "Test Connection" button validates LLM endpoint.
  - `GET/POST /ui/inventory` — disable YAML VMs or add ad-hoc VMs. Changes take effect on next batch run.
  - HTTP Basic Auth middleware on all `/ui/*` routes (`secrets.compare_digest`, timing-safe)
  - Inventory merge in `run_env_batch()`: YAML → filter disabled → append db_additions
  - All audit-change events logged as `SETTINGS_CHANGED` / `INVENTORY_CHANGED`
  - New tests: 18 `test_overrides.py` (T1), 21 `test_settings_precedence.py` (T2), 9 `test_inventory_merge.py` (T3)
  - Learning doc: `docs/learning/22-ui-settings-and-inventory.md`
  - SETUP.md updated: Step 5b — Secure the Web UI

- **Phase 4 Playwright tests (T4-T6 — 45 tests)**:
  - `tests/ui/test_settings_playwright.py` (15 tests): page load, save+persist, reset, env-var lock / source labels
  - `tests/ui/test_inventory_playwright.py` (17 tests): page load, VM display, toggle, add ad-hoc VM, delete
  - `tests/ui/test_ui_auth_playwright.py` (13 tests): 401 without creds, 200 with creds, wrong user/pass, WWW-Authenticate header, /metrics+/health open
  - **Bug fixed**: Nested `<form>` inside the main settings `<form>` caused Chromium to implicitly close the outer form — Save button ended up orphaned. Fixed via HTML5 `form="reset-{key}"` attribute pattern (out-of-band form + form-attr button).

### Deferred Execution — Window-Gated Approval (complete)

The approval flow is now fully decoupled from execution. A dry-run scan can happen at 10 AM, the operator approves at 1 PM, and live execution only fires when the maintenance window opens (e.g., 11 PM).

- **`errander/models/events.py`**: Added `EXECUTION_DEFERRED` and `DEFERRED_EXECUTION_STARTED` to `EventType`
- **`errander/safety/deferred.py`** (NEW): `DeferredExecutionStore` — SQLite table `deferred_executions`; `save()`, `get_pending()`, `mark_executing()`, `mark_done()`, `expire_old()` (7-day auto-expiry)
- **`errander/scheduling/windows.py`**: Added `next_window_open()` (next future window start, skips current open window) and `window_start_cron()` (converts window config to APScheduler cron string)
- **`errander/agent/graph.py`**: `BatchGraphState` extended with `env_name` and `deferred` fields; `approval_gate_node` defers approved dry-runs made outside window; `build_batch_graph()` accepts `deferred_store`
- **`errander/main.py`**: `DeferredExecutionStore` initialised alongside `AuditStore`; `_window_opener()` function executes pending deferred batches at window start; window-opener cron jobs registered per environment; `env_name` threaded into initial batch state
- **Tests**: 34 new tests — `tests/safety/test_deferred.py` (15), `tests/scheduling/test_windows.py` (+9), `tests/agent/test_graph.py` (+6), `tests/test_main.py` (+3 `_window_opener` tests)
- **Total: 878 tests passing**

## In Progress
- Nothing actively in flight.

## Recent Fix: PR-2 Gap Closure (2026-05-14)

Three correctness/docs gaps identified in PR-1.5/PR-2 post-review:

- **Gap 1 (correctness)** — `parse_listening_ports` now strips `pid=\d+` and `fd=\d+` from the `users:((...))` column via `_EPHEMERAL_RE`. PIDs change on every service restart and were causing false drift alerts. Process names are retained so new services are still detected. 4 new tests added (`test_pid_stripped_from_users_column`, `test_fd_stripped_from_users_column`, `test_process_name_retained`, `test_pid_change_does_not_change_canonical_form`).
- **Gap 2 (docs debt)** — `example/settings.yaml` now contains the full `sre_signals:` block with annotated comments for all 10 tuneable fields (`preflight_lock_check`, `reboot_required_check`, `service_health_check`, `disk_growth_trend.*`, `drift.*`, `failed_ssh_logins.*`). Operators now have a reference config.
- **Gap 3 (missing feature)** — `disable_failed_login_check: bool = False` per-VM inventory tag wired through: `TargetSchema` → `yaml_targets` dict → `VMGraphState` → `failed_logins_node` early-exit. Set `disable_failed_login_check: true` in inventory.yaml to skip the failed login probe for honeypots/bastions. Documented in `example/inventory.yaml` header comment.
- **1287 tests passing** (no regressions, 4 new listening_ports tests).

## Recent Fix: SRE Production Wiring (2026-05-14)

SRE validation audit found all signal stores were implemented as library code but never reached the production path. Fixed:

- **`VMDiskHistoryStore`, `BaselineStore`, `VMStateStore`** initialized in `async_main` and threaded through `run_env_batch` → `build_batch_graph` → `make_wave_dispatcher` → `build_vm_graph`. Previously these were library code only; now active in every production run.
- **`vm_state_store` + `audit_store`** passed to `build_patching_subgraph` so `reboot_check_node` persists reboot flags and `service_health_post_node` emits `SERVICE_HEALTH_REGRESSION` audit events with correct batch_id.
- **`batch_id` to `PatchingGraphState`**: patching nodes now read `batch_id` from state (passed from `VMGraphState`) so audit events carry the right batch_id across all runs, not just the first.
- **`critical_services`** threaded from `TargetSchema` → `yaml_targets` dict → `VMGraphState.critical_services` → `PatchingGraphState.critical_services` via both `Send()` paths. Service health regression checks now actually use the configured service list.
- **`authentication failure` grep removed** from `failed_logins_command`: grep was fetching lines the regex couldn't parse; removed to match what we actually count.
- **10 new wiring tests** in `tests/agent/test_sre_wiring.py`: proves stores reach `make_wave_dispatcher`, `build_vm_graph`, patching subgraph, and `run_env_batch`.
- **1303 tests passing** (10 new).

## Recent Fix: Plan Gap Closure Round 2 (2026-05-14)

Three remaining items from `ai_sre_implementation_plan.md` audit:

- **Systemd timers in `scheduled_jobs.py`** — Added `systemctl list-timers --all --no-legend --no-pager | awk '{print $NF}'` as a 4th source in the SSH command. Timer unit names are captured; the volatile "next trigger" timestamp is excluded (awk last-column only) to prevent false drift every time a timer fires. 6 new tests.
- **`docs/learning/README.md`** — Added entries 25–31 (all SRE learning docs were missing from the index).
- **`README.md`** — Test count updated 929 → 1293 in all three occurrences.
- **1293 tests passing**.

## Next Up
- Run staging soak (`tests/staging/soak_checklist.md`) against real VMs to validate end-to-end before any production deployment
- Wire `generate_report` and `analyze_failure` decisions through `ai_decision_store` (currently only `prioritize_actions` is audited)



## Decisions Made
- **LangGraph Send() pattern**: `Send()` objects must come from conditional edge routing functions, NOT from nodes. Nodes return dicts. Routing functions return strings or `list[Send]`. Discovered via `InvalidUpdateError` during Phase 1.5.
- **Pre-compiled VM graph**: The per-VM graph is compiled once in `make_fan_out_router()` and reused for all fan-out invocations via closure. Avoids N graph compilations.
- **Routing-only nodes**: `check_more_actions` is a pass-through node (returns `{}`) that exists only to give the conditional edge a named source. LangGraph requires conditional edges to be attached to nodes.
- **State serialisation at boundaries**: Sub-graph states use TypedDict; the VM graph stores results as `list[dict]`. ActionResult objects are serialised when written, deserialised when read (e.g., report generator). Avoids Pydantic/dataclass serialisation across graph boundaries.
- **Hardcoded fallbacks first**: All LLM-powered functions (`prioritize_actions`, `generate_report`, `analyze_failure`) are fully implemented with hardcoded logic before LLM integration. The agent is fully functional without LLM.
- **Module-scoped test fixtures for expensive clients**: `AsyncOpenAI` initialises an httpx transport (~1.4s). Using `pytest.fixture(scope="module")` reduces LLM test suite from 57s to 6.5s.
- **Custom Prometheus registry**: Using `CollectorRegistry()` instead of the library default for test isolation and explicit ownership — `generate_latest(REGISTRY)` only outputs Errander-AI metrics.
- **Scheduler does not enforce windows**: The APScheduler cron triggers runs at the configured time; the graph's `validate_window_node` is the authoritative safety gate. Separates scheduling concerns from safety concerns.
- **Outbound-only Slack**: No webhooks, no inbound endpoints, no nginx. Agent polls `reactions.get` every 30s. Zero infra overhead for approval flow.
- **Web UI on same port as /metrics**: The `/ui` routes run on the same aiohttp server as `/metrics` and `/health` (port 9090). No new process, no new port, direct in-process access to `AuditStore`. Separate server warranted only if UI needs auth or WebSockets.
- **`web.AppKey` for typed app data**: Used `web.AppKey[AuditStore | None]` to store the audit store on the aiohttp app. Silences `NotAppKeyWarning` and gives the type checker a typed handle — avoids string key collisions.
- **Pico.css classless via CDN**: Zero custom CSS — Pico.css styles standard semantic HTML elements without class names. One `<link>` tag is the entire styling solution for Phase 1.
- **Native SQLite queries, not MCP**: SQLite query capability built directly into `AuditStore` and `main.py --audit` mode. No external MCP server needed — the agent owns its own audit data and can query it natively.
- **`GROUP_CONCAT(DISTINCT vm_id)`**: Used for `get_recent_batches()` — SQLite supports this natively, deduplicates VM IDs per batch in a single query without a subquery.
- **Docker Compose for production vLLM**: Single-VM GPU deployment via Docker Compose is production-grade — reproducible environment, `restart: unless-stopped`, model weights cached on host volume. Bare metal adds no meaningful benefit for a single dedicated T4 VM.
- **`asyncio.wait(FIRST_COMPLETED)` for dual-channel racing**: `await asyncio.wait({slack_task, ui_task}, return_when=FIRST_COMPLETED)` is the right primitive — cleaner than `asyncio.gather` with cancellation tokens or manual flags. The losing task is explicitly cancelled with `await t` to drain any pending cleanup.
- **`asyncio.Event()` as the signalling primitive**: `PendingApproval._event` is set by `decide()` and waited on by `_wait_ui()`. No queues, no locks — the event is the direct channel between the HTTP handler coroutine and the approval waiter. Works because all coroutines share the same event loop.
- **Idempotent `decide()`**: Uses `self._pending.pop(batch_id, None)` — returns None silently if already decided. This makes dual-channel racing safe: the slower channel can call `decide()` after the faster one without raising.
- **Canary as wave 0**: Canary is not a separate mechanism — it's just wave 0 with exactly 1 VM and a stricter health check command. `prepare_waves_node` inserts the canary target as `waves[0]` before the percentage-based remaining waves. Zero new nodes or state machines.
- **Drift stored as audit events**: Baselines are stored as `DRIFT_BASELINE_SAVED` events in the existing SQLite audit trail (JSON blob in metadata). No new table, no schema migration. Queried via `get_events(vm_id=..., event_type=..., limit=1)` — most recent entry is the current baseline.
- **`dispatch_wave` as no-op node**: In the wave-based graph, `dispatch_wave` is a pass-through node (`lambda state: {}`) whose only role is to be the named source for the conditional edge that emits `Send()` objects. The routing function does the real work. Same pattern as `check_more_actions` in the VM graph.
- **`make_fan_out_router` kept for backward compat**: New `make_wave_dispatcher` handles production use. `make_fan_out_router` still exists and is still imported in tests — removing it would break existing test assertions without any benefit.
- **Port bind before validation caused test failures**: `start_metrics_server()` was called before `--env` validation checks. When two `async_main` calls ran in the same process (test suite), the second bind on port 9090 failed. Fix: validate `--env` / unknown env BEFORE creating the audit store or starting the server.
- **`load_settings()` stays synchronous (Phase 4)**: Accepts pre-fetched `db_overrides: dict[str, str]` instead of `OverridesStore` directly — keeps the sync call chain intact and makes testing simple.
- **`enc:v1:` prefix for encrypted values (Phase 4)**: Prefix-tagged format makes it easy to detect encrypted vs plaintext at any layer (env var, YAML, DB) without needing a separate `is_secret` flag.
- **Basic Auth on `/ui/*` only (Phase 4)**: `/metrics` and `/health` remain open (Prometheus scrapers don't support auth). Auth scoped to human-facing routes only.
- **Inventory merge in `run_env_batch()` (Phase 4)**: Merge happens at batch invocation time — operators can change inventory via UI and the next scheduled run picks it up without restart.

## Blockers
None.

## Files Changed (2026-05-14 — SRE production wiring fix)
### Modified
- `errander/agent/subgraphs/patching.py` — Added `batch_id: str` to `PatchingGraphState`; `reboot_check_node` and `service_health_post_node` now read `batch_id` from state (not from compile-time closure) so audit events carry the correct id across all batches.
- `errander/agent/vm_graph.py` — Added `critical_services: list[str]` and `vm_state_store` param to `build_vm_graph`; `build_patching_subgraph` now receives `audit_store` + `vm_state_store`; `_run_patching` passes `batch_id` and `critical_services` into `PatchingGraphState`.
- `errander/agent/graph.py` — `make_wave_dispatcher` and `build_batch_graph` accept 6 new SRE params (disk/baseline/vm_state stores + settings) and forward them to `build_vm_graph`; both `Send()` paths include `critical_services`.
- `errander/main.py` — `VMDiskHistoryStore`, `BaselineStore`, `VMStateStore` initialized in `async_main` and closed in `finally`; passed through `run_env_batch`, `_window_opener`, and both scheduler closures; `yaml_targets` now includes `critical_services` from inventory; `db_additions` gets `critical_services: []`.
- `errander/execution/failed_logins.py` — Removed `authentication failure` from grep (regex can't parse PAM format; honest removal beats silent under-count).
- `tests/agent/test_inventory_merge.py` — Added `settings.sre_signals = SRESignalSettings()` to mock setup.

### Created
- `tests/agent/test_sre_wiring.py` — 10 wiring tests proving full dependency chain from `make_wave_dispatcher` → `build_vm_graph` → patching subgraph, `critical_services` in `Send()` payloads, and `run_env_batch` passing stores to `build_batch_graph`.
- `docs/learning/32-sre-production-wiring.md` — Learning doc: dependency injection chain, silent no-op pattern, batch_id-in-state fix, critical_services flow, how to wire new dependencies correctly.

## Files Changed (2026-05-14 — SRE auditor second pass: URL-quoting + stale Playwright test)
### Modified
- `errander/observability/metrics.py` — Added `_uq = urllib.parse.quote(safe="")` helper; applied to all URL path segments in batch/VM/approval links and form actions (defense in depth alongside `_esc`).
- `tests/ui/test_inventory_playwright.py` — Added `_YAML_FLEET` list of `VMTarget`s; `_start_server` now accepts `base_inventory`; `inventory_base_url` fixture passes `_YAML_FLEET` so yaml_override rows render correctly; `inventory_empty_url` passes `[]`; stale empty-state assertion updated from "No inventory overrides yet" → "No VMs in inventory".

## Files Changed (2026-05-14 — Inventory UI shows full YAML fleet)
### Modified
- `errander/observability/metrics.py` — Added `_BASE_INVENTORY_KEY` app key; `start_metrics_server` gains `base_inventory` param; `_ui_inventory_get` rewritten to build merged view: YAML VMs as base (with disabled state from DB override), ad-hoc DB VMs appended. Shows YAML vs ad-hoc badge per row.
- `errander/main.py` — Calls `load_inventory()` alongside `validate_inventory()` to get flat `list[VMTarget]`; passes it as `base_inventory` to `start_metrics_server`.

## Files Changed (2026-05-14 — SRE UI revalidation: 3 remaining issues)
### Modified
- `errander/observability/metrics.py` — Escaped `title` in `<title>` and `.tb-title` in `_page()`; escaped `batch_id`/`vm_id` in dashboard rows, batches list, approval cards/forms/history links.
- `errander/main.py` — Fixed startup ordering: `OverridesStore` initialized and queried for `db_overrides` before `_build_components()` so DB-persisted LLM settings take effect on restart. Reuses same store instance later.

## Files Changed (2026-05-14 — SRE UI audit remediation)
### Modified
- `errander/observability/metrics.py` — 7 findings fixed: (1) Added `@web.middleware` to `_csrf_middleware` — was missing, causing 500 on POST; (2) Fixed `_inject_csrf` to return `(modified_html, nonce)` not `(token, nonce)` — modified html was discarded; (3) Wired `_inject_csrf` + `_set_csrf_cookie` into `_page()` via optional `request=` param, called from settings/inventory/approvals GET handlers; (4) Applied `html.escape()` (`_esc`) to all untrusted DB/URL fields in batch detail, VM detail, inventory rows, flash messages, and settings input values; (5) Added "takes effect after restart" warning to settings page; (6) Converted `test-llm` from GET to POST so API keys never appear in URLs/logs; (7) Narrowed `_VALID_OS_FAMILIES` to `{"ubuntu","debian","rhel"}` matching `OSFamily` enum.

## Files Changed (2026-05-13 — UI nav active-state bug fix)
### Modified
- `errander/web/server.py` — Removed duplicate "Active Batch" nav item (both it and "Batch History" pointed to `/batches`, causing both to highlight simultaneously). Deleted dead `sidebar()` and `_sidebar_nav()` functions that were never called by `layout()`. All 8 routes verified returning 200 with exactly one active nav item per page.

## Files Changed (2026-05-13 — Operations Hub UI: Glossary, Inventory, Settings, Admin)
### Modified
- `errander/web/server.py` — Added `page_glossary()` (animated LangGraph DAG + 18-term glossary + node-click modal), `page_inventory()` (KPI tiles, filter bar, full VM table), `page_settings()` (4 read-only config cards), `page_admin()` (agent controls, system health, lock manager, override toggles, danger zone). Wired `handle_inventory()`, `handle_settings()`, `handle_admin()` route handlers. Updated `NAV_ITEMS` with Admin section. Registered all routes in `create_app()`.

## Files Changed (2026-05-10 — fix MasterKeyMissingError in --check-llm)
### Modified
- `scripts/configure.sh` — LLM verify call now passes `ERRANDER_SECRETS_KEY` inline alongside the other env vars
- `errander/main.py` — moved `--generate-secrets-key`, `--encrypt`, `--check-inventory` before `load_settings()`; wrapped `load_settings()` in try/except for `MasterKeyMissingError` with a clear actionable error message

## Files Changed (2026-05-10 — configure.sh set -e grep fixes)
### Modified
- `scripts/configure.sh` — added `|| true` to all bare `grep` calls inside `$()` subshells; `set -euo pipefail` was silently killing the script when `grep` found no match (exit 1 treated as fatal)

## Files Changed (2026-05-10 — --check-inventory CLI flag)
### Modified
- `errander/main.py` — added `--check-inventory` flag + `run_inventory_check()`: validates inventory.yaml and prints env/target summary; wired into `async_main` early-exit path
- `scripts/configure.sh` — Step 6 verify command replaced with `uv run python -m errander --check-inventory` (was a 200-char one-liner that wrapped in terminals and broke on copy-paste)

## Files Changed (2026-05-10 — SECRETS.md key rotation docs)
### Modified
- `docs/SECRETS.md` — added `ERRANDER_UI_PASSWORD` to `.env` example; split key rotation into two sections (old key available vs. key lost); added per-variable notes explaining runtime behaviour

## Files Changed (2026-05-10 — SETUP.md continued)

### Modified
- `SETUP.md` — Step 2: rewrote SSH key section with diagram and Master VM / Target VM labels on every substep; Step 3: added backup → visudo validate → rollback safety sequence, labeled all substeps (Target VM); Step 4: full rewrite — decision table, Azure Foundry as first featured option, verify step per option, Master VM labels throughout; merged old Step 5 (Slack) into Step 6 as a subsection — Step 5 is now the single "Configure the agent" step with Slack as an optional sub-section at the bottom; steps 6-10 renumbered to 5-9; added Steps 4-6 quick path section (configure.sh one-liner); Windows Step 1 rewritten to use bootstrap.ps1 one-liner (was manual steps)
- `scripts/bootstrap.ps1` — new Windows bootstrap script: installs git via winget, uv via official PowerShell installer, Python 3.12 via uv, clones repo, runs uv sync, verifies import. No admin required.
- `scripts/configure.sh` — new interactive setup script: prompts for LLM, VMs, SSH key, Slack; writes .env + inventory.yaml; verifies LLM connection
- `CLAUDE.md` — expanded doc sync rule to two tiers: always-update (STATUS, command-log, todo, lessons) and update-when-relevant (SETUP, README, RUN, learning docs, etc.)
- `README.md` — fixed hardcoded Qwen3/vLLM references → generic; test count 587 → 878; V2 roadmap removed already-shipped Phase 3 items; Quick Start fixed clone URL + directory; added configure.sh reference
- `tasks/phase-1.8-validation-checklist.md` — updated stale commit hash to aa32f48

## Files Changed (2026-05-09 — E2E Validation Prep + Docs)

### Created
- `scripts/bootstrap.sh` — distro-agnostic bootstrap script (Ubuntu/Debian/RHEL/CentOS/Oracle/Fedora): detects pkg manager, installs git + curl + uv + Python 3.12, clones repo, runs uv sync, verifies import
- `.gitattributes` — enforce LF line endings for .sh, .py, .yaml, .md files

### Modified
- `SETUP.md` — major overhaul: added Prerequisites section (software, network ports table, Azure NSG note for port 9090, SSH tunnel alternative); updated architecture diagram to reflect Azure VNet topology; fixed git clone URL placeholders; marked Step 5 (Slack) as optional with web UI fallback; updated .env templates (added ERRANDER_LLM_MODEL, commented out Slack, added UI auth); fixed env var table (Slack Required: Yes → No); replaced Linux Step 1 manual commands with bootstrap script one-liner; fixed Python 3.12 apt install for Ubuntu 22.04; fixed private repo bootstrap (clone first, then run script)
- `CLAUDE.md` — added commit message format rule (one line, type: description, under 72 chars)

## Files Changed (2026-04-27 — Deferred Execution)

### Modified
- `errander/models/events.py` — added `EXECUTION_DEFERRED`, `DEFERRED_EXECUTION_STARTED` to `EventType`
- `errander/scheduling/windows.py` — added `next_window_open()`, `window_start_cron()`, `_CRON_DAY_ABBR` map
- `errander/agent/graph.py` — `BatchGraphState` extended; `approval_gate_node` with deferred logic; `build_batch_graph()` new `deferred_store` param; imports updated
- `errander/main.py` — `DeferredExecutionStore` import + init; `deferred_store` param in `run_env_batch()`; `_window_opener()` function; window-opener cron job registration; `env_name` in initial state; `deferred_store.close()` in finally
- `tests/scheduling/test_windows.py` — 9 new tests for `next_window_open` and `window_start_cron`
- `tests/agent/test_graph.py` — 6 new `TestApprovalGateDeferred` tests
- `tests/test_main.py` — 3 new `TestWindowOpener` tests; `SSHConnectionManager` import added
- `docs/SETUP.md` — updated test count to 878 (from SETUP.md step 6)
- `config/inventory.yaml` — approval_policy strict for all envs
- `example/inventory.yaml` — approval_policy strict for all envs
- `errander/models/actions.py` — Docker prune risk tier raised from LOW to MEDIUM
- `CLAUDE.md` — Risk Tiers table updated (Docker prune → Medium)

### Created
- `errander/safety/deferred.py` — `DeferredExecutionStore` + `DeferredExecution` dataclass
- `tests/safety/test_deferred.py` — 15 tests for `DeferredExecutionStore`
- `docs/learning/24-deferred-execution.md` — learning doc

## Files Changed (2026-05-10 — configure.sh UX fix)
### Modified
- `scripts/configure.sh` — moved "Do you want to add target VMs?" prompt before section header on fresh install; section header only shown after user confirms; re-run path (existing inventory.yaml) unchanged
- `scripts/configure.sh` — suppress `[3/5] SSH key pair` step header when key already exists; show single ok line instead
- `scripts/configure.sh` — removed SSH key generation entirely; script now only verifies key exists and points to SETUP.md Step 2 if missing; banner updated to reflect verify-only behaviour
- `scripts/configure.sh` — split combined "Keep existing VMs and just add more?" into two separate prompts: "Keep these VMs? (Y/n)" and "Add more VMs? (y/N)"
- `scripts/configure.sh` — removed stale "Complete SETUP.md Steps 2-3" reminder from final summary; replaced with direct "Next — run a dry-run:" line
- `scripts/configure.sh` — final summary now shows Step 6 (verify inventory + pytest) before Step 7 (dry-run), matching SETUP.md order
- `scripts/configure.sh` — Step 7 dry-run command now includes `--force --force-reason "initial dry-run validation"` to bypass maintenance window on first run
- `SETUP.md` — Step 7 commands updated with `--force --force-reason`; added note explaining `--force` bypasses the window for first-run validation
- `errander/agent/graph.py` — approval gate now auto-approves dry-run batches immediately; approval only required for live runs with HIGH/CRITICAL risk tier

## Files Changed (2026-05-10 — configure.sh + SETUP.md 9-bug audit)
### Modified
- `scripts/configure.sh` — A: fixed fresh install Enter default (added `_add_vms="${_add_vms:-y}"`)
- `scripts/configure.sh` — B: fixed "keep + add more" silently dropping new VMs (append TARGETS_YAML when KEEP_INVENTORY=true)
- `scripts/configure.sh` — C: fixed re-run resetting UI password (read existing creds from .env before writing)
- `scripts/configure.sh` — C: added warning when UI password is still 'changeme'
- `scripts/configure.sh` — D: improved SSH key missing message — explicit "setup is incomplete, re-run after creating key"
- `SETUP.md` — E: Step 7 and Step 8 `--env dev` → `--env <your-env-name>` with substitution note
- `SETUP.md` — F: Azure Foundry URL fixed from `openai.azure.com` → `cognitiveservices.azure.com`
- `SETUP.md` — G: systemd service rewritten to use `$(whoami)` and `$(pwd)` — no more hardcoded `errander` user
- `SETUP.md` — H: quick path description updated — "SSH key" → "verify your SSH key path"
- `SETUP.md` — I: added password change warning in Web UI section and `.env` template comment

## Files Changed (2026-05-10 — secrets hardening + UI credential prompt)
### Modified
- `scripts/configure.sh` — `chmod 600 .env` applied on every write (was missing entirely)
- `scripts/configure.sh` — optional Fernet encryption: generates key to `~/.errander.key` (chmod 600, separate from .env), encrypts LLM_API_KEY / UI_PASSWORD / SLACK_BOT_TOKEN as `enc:v1:` blobs; re-run safe (already-encrypted values passed through unchanged)
- `scripts/configure.sh` — web UI username + password prompted explicitly on fresh install (with confirmation loop); re-run shows existing values as defaults; `changeme` can never silently reach production
- `scripts/configure.sh` — encryption key auto-wired: exported into current session, appended to `~/.bashrc`/`~/.zshrc` (idempotent), and injected into systemd service EnvironmentFile if service already installed — no manual steps required
- `scripts/bootstrap.sh` — completion message corrected: step numbers updated, configure.sh quick path surfaced

## Files Changed (2026-05-10 — fix --check-llm decrypts enc:v1: API key)
### Modified
- `errander/main.py` — `run_llm_check()` now runs LLM env vars through `SecretsManager.decrypt_if_needed()` so encrypted API keys (enc:v1:...) are decrypted before use; previously the raw ciphertext was sent to the LLM provider causing 401

## Files Changed (2026-05-10 — fix --check-llm needs env vars in Step 6)
### Modified
- `scripts/configure.sh` — Step 6 output: removed `--check-llm` (configure.sh already ran it); replaced with note "(LLM already verified above)"
- `SETUP.md` — Step 6: `--check-llm` moved to optional re-verify block with explicit `source ~/.errander.key` + `export .env` instructions before it

## Files Changed (2026-05-10 — separate end-user and developer setup steps)
### Modified
- `scripts/configure.sh` — Step 6 output trimmed to end-user steps only: `--check-inventory` and `--check-llm`
- `scripts/bootstrap.sh` — reverted to bare `uv sync` (no `--extra dev`, no playwright — dev tools not needed for deployment)
- `SETUP.md` — Step 6 is now end-user only (inventory check + LLM check); pytest/playwright/ruff/mypy moved to new "For developers" section at the bottom

## Files Changed (2026-05-11 — Phase 4 E2E verification)

### Created
- `tests/chaos/__init__.py`
- `tests/chaos/test_fault_injection.py` — 19 fault-injection tests: SSH drop (2), patching rollback routing (3), dpkg lock (1), audit strict/best-effort (4), LLM timeout/malformed/no-LLM (3), approval manager (3), fleet abort (1), Windows tempdir safety (2)
- `tests/staging/__init__.py`
- `tests/staging/soak_checklist.md` — 8-step manual staging soak checklist

### Modified
- `tests/agent/test_graph.py` — `/tmp/test-locks` → `tmp_path / "locks"` (Windows portability, finding #4.3)

## Files Changed (2026-05-11 — Phase 3 honest AI integration)

### Created
- `errander/safety/ai_audit.py` — `AIDecisionStore` + `AIDecision` dataclass; `ai_decisions` SQLite table with indexes; per-call audit: model, base_url, prompt_template_id, prompt_hash, response_raw, outcome, latency_ms, token counts
- `tests/ai_evals/__init__.py`
- `tests/ai_evals/test_golden_plans.py` — 32 eval tests: golden plans (7), injection corpus (10), schema-violation corpus (6), per-decision audit (5), additional integration (4)

### Modified (source)
- `errander/agent/decisions.py` — `_INJECTION_RE` injection guard; `prioritize_actions` accepts `policy`, `batch_id`, `vm_id`, `ai_store`; per-call audit logging; policy-aware filtering log; injection rejection; fallback audit event
- `errander/agent/vm_graph.py` — `VMGraphState.ai_db_path` field; `plan_actions_node` accepts `llm_client` + `ai_decision_store`; `build_vm_graph` accepts both; `_plan_actions` closure wires them
- `errander/agent/graph.py` — `BatchGraphState.ai_db_path` field; `make_wave_dispatcher` + `build_batch_graph` accept `llm_client` + `ai_decision_store`; `ai_db_path` threaded through Send payload
- `errander/main.py` — `run_env_batch` accepts `llm_client`; `AIDecisionStore` initialized per batch with same SQLite file; `ai_db_path` in initial batch state; all 3 run_env_batch call sites pass `llm_client`; `_window_opener` accepts `llm_client`

### Modified (tests)
- `tests/agent/test_inventory_merge.py` — `settings.audit_db_url = ":memory:"` on mock Settings

## Files Changed (2026-05-11 — Phase 2 policy enforcement + fleet safety)

### Modified (source)
- `errander/models/events.py` — added `FLEET_ABORT` and `OS_MISMATCH` to `EventType`
- `errander/safety/validators.py` — `validate_action` now uses `get_policy()`/`requires_approval()`; CRITICAL reason includes policy name; removed "unused" docstring note
- `errander/agent/vm_graph.py` — `VMGraphState.env_policy` field added; passed to `validate_action` in `dispatch_action_node`
- `errander/agent/graph.py` — `check_fleet_health_node` between validate_targets and plan fan-out; `route_after_fleet_check`; `validate_targets_node` replaces `echo ok` with `cat /etc/os-release` + `parse_os_release()` + `verify_os_match()`; OS_MISMATCH audit events; `env_policy` threaded into Send payloads; `plan_vms` no-op node as fan-out entry; `check_fleet_health` node wired in graph

### Modified (tests)
- `tests/safety/test_audit.py` — `test_all_event_types_stored` uses dynamic limit
- `tests/agent/test_graph.py` — `validate_targets` tests updated to mock os-release response
- `tests/agent/test_load.py` — `_ssh_ok()` default stdout is valid os-release; SSH call counts updated for validate (1 os-release) + plan_vm (5 detect_os) pattern

### Created (tests)
- `tests/agent/test_phase2_policy.py` — 21 tests: 5 policy validation, 8 fleet abort, 8 OS verification

## Files Changed (2026-05-11 — Phase 1 security hardening)

### Created
- `errander/execution/command_builder.py` — `safe_path`, `safe_pkg`, `safe_ver`, `pkg_version_spec`, `build_cmd`; `CommandBuildError`
- `tests/execution/test_command_builder.py` — 22 tests; injection corpus covering `;`, `$()`, backtick, `|`, `>`, null byte, spaces
- `tests/execution/test_ssh_host_keys.py` — 6 tests for known_hosts modes (strict, TOFU, missing config)
- `tests/agent/subgraphs/test_docker_prune_scope.py` — 4 tests for dangling-only vs aggressive prune
- `tests/observability/test_ui_security.py` — 7 tests for bind address enforcement, CSRF middleware, CSRF injection helper

### Modified (source)
- `errander/execution/ssh.py` — `SSHConnectionManager.__init__` accepts `known_hosts_path`/`strict_host_keys`; `_connect` enforces three modes (verified/TOFU/refuse); TOFU logs WARNING per connection
- `errander/execution/commands.py` — `AptManager.upgrade_all` uses dpkg-query + Python filter + exact hold names (no glob apt-mark); `DnfManager.upgrade_all` uses rpm + dnf versionlock; both `list_installed_versions` / `install_version` use `safe_pkg`/`safe_ver`
- `errander/agent/subgraphs/backup_verify.py` — `assess_node` uses `safe_path()`; unsafe paths skipped with error logged
- `errander/agent/subgraphs/log_rotation.py` — manual rotation f-strings replaced with `safe_path()`; unsafe paths skipped
- `errander/agent/subgraphs/docker_prune.py` — `DockerPruneGraphState.docker_prune_aggressive` field; `execute_node` defaults to dangling-only commands; aggressive=True uses `system prune -af`
- `errander/safety/rollback.py` — `shlex.quote` replaced with `pkg_version_spec()` from command_builder
- `errander/config/settings.py` — `ssh_known_hosts_path`, `ssh_strict_host_keys`, `ui_bind_address` fields + env var loading
- `errander/observability/metrics.py` — `bind_address` param; mandatory auth guard on non-loopback; `_CSRF_SECRET_KEY` AppKey; `_csrf_middleware`, `_csrf_verify`, `_inject_csrf`, `_re_inject_csrf` helpers; CSRF middleware wired into app
- `errander/main.py` — `SSHConnectionManager` constructed with `known_hosts_path`/`strict_host_keys` from settings; `--bootstrap-known-hosts <env>` CLI; `run_bootstrap_known_hosts()` function; `start_metrics_server` called with `bind_address`

## Files Changed (2026-05-11 — Phase 0 SRE audit remediation)

### Modified (source)
- `errander/agent/graph.py` — new plan/apply flow: `plan_vm` fan-out, `collect_plans`, `generate_plan_artifact`, `approval_gate` before execution; ImmutableBatchPlan with SHA-256 hash; deferred logic inverted (live runs outside window defer, dry-run always immediate); `_route_plan_vms` fan-out; `vm_plans` reducer
- `errander/agent/subgraphs/patching.py` — `execute_node` reads `dry_run` from state; `rollback_node` with real dpkg rollback; `route_after_execute` routes FAILED → rollback; graph wired with rollback node
- `errander/agent/subgraphs/disk_cleanup.py` — `execute_node` reads `dry_run` from state, passes per-call override to `executor.execute()`
- `errander/agent/subgraphs/docker_prune.py` — same `dry_run` state read fix
- `errander/agent/subgraphs/log_rotation.py` — same `dry_run` state read fix
- `errander/execution/sandbox.py` — `execute()` accepts `dry_run: bool | None = None` per-call override; `effective_dry_run` logic
- `errander/main.py` — `--unsafe-legacy-live` guard blocks live mode until Phase 0 complete
- `errander/models/plans.py` — `ImmutablePlan` dataclass with SHA-256 `plan_hash` and `short_hash()`
- `errander/safety/audit.py` — `AuditWriteError`, `strict_mode: bool = True`, `log_event(dry_run=False)` fail-closed in strict mode
- `errander/safety/rollback.py` — full Option A patching rollback: dpkg snapshot → apt-get --allow-downgrades → verify versions
- `errander/config/settings.py` — `audit_mode: str = "strict"` field

### Modified (tests)
- `tests/agent/subgraphs/test_disk_cleanup.py` — `capture_execute` mock updated with `dry_run` param
- `tests/agent/subgraphs/test_patching.py` — `test_route_after_execute_finishes_on_failure` → `test_route_after_execute_routes_failure_to_rollback`
- `tests/agent/test_graph.py` — 4 deferred tests updated to reflect new behavior (dry-run never deferred; live outside window IS deferred)
- `tests/agent/test_load.py` — wave abort SSH mock count updated (12 validate + 12 plan_vm + 3 health = 27)
- `tests/safety/test_audit.py` — swallow tests use `dry_run=True` (best-effort mode)
- `tests/safety/test_rollback.py` — patching rollback tests updated to reflect implemented behavior

## Files Changed (2026-05-10 — fix SETUP.md Step 6: remove env export before pytest, add sync/playwright)
### Modified
- `SETUP.md` — Step 6 rewritten: removed `export $(grep -v '^#' .env | xargs)` (poisons pytest), replaced long one-liner with `--check-inventory`, added `uv sync --extra dev` + `playwright install chromium` steps, added warning note; Step 7 Linux/Windows blocks aligned — both now show the load-env step explicitly before `--run-now`

## Files Changed (2026-05-10 — fix test failures on VM: stale dates, env leakage, Playwright)
### Modified
- `tests/safety/test_deferred.py` — WINDOW_START changed from hardcoded 2026-04-26 to `now+30d`; expiry_at was already in the past on the VM, causing get_pending() to return nothing
- `tests/test_main.py` — same fix for two TestWindowOpener tests using `datetime(2026, 4, 27, ...)`
- `tests/conftest.py` — added autouse fixture `clean_errander_env` that clears all ERRANDER_* env vars before each test; prevents real .env values exported to shell from polluting settings/secrets tests
- `scripts/bootstrap.sh` — added `uv run playwright install chromium` after uv sync so browser binary is available for UI tests
- `scripts/configure.sh` — added `playwright install chromium` line to Step 6 verify instructions
### Created
- `tests/ui/conftest.py` — `pytest_collection_modifyitems` hook that skips all UI tests with a clear message when Chromium binary is absent, instead of ERRORing

## Files Changed (2026-05-10 — add --extra dev to uv sync in bootstrap and docs)
### Modified
- `scripts/bootstrap.sh` — `uv sync` → `uv sync --extra dev` so pytest/ruff/mypy are installed during bootstrap
- `scripts/configure.sh` — Step 6 output now includes `uv sync --extra dev` as the first verify command
- `SETUP.md` — both manual-clone code blocks updated to `uv sync --extra dev`

## Files Changed (2026-05-10 — move --check-llm before load_settings)
### Modified
- `errander/main.py` — `run_llm_check()` now reads LLM env vars directly (no Settings param); moved before `load_settings()` in `async_main` so a decryption error in `ERRANDER_UI_PASSWORD` never blocks LLM connectivity verification
- `docs/learning/13-vllm-setup.md` — updated code snippet to reflect new early-exit placement

## Files Changed (2026-05-10 — fix DecryptionError on configure.sh re-run)
### Modified
- `scripts/configure.sh` — reuse existing `~/.errander.key` on re-run instead of generating a new key; new key generated only when the file is absent; prevents `enc:v1:` blobs in `.env` becoming unreadable after re-run
- `errander/integrations/secrets.py` — improved `DecryptionError` message to explain the key-mismatch cause and tell the user to re-run configure.sh and re-enter the affected secret
- `tasks/lessons.md` — added lesson: configure.sh must reuse existing key, not regenerate on every run

## Files Changed (2026-05-10 — patching: run apt-get update before listing upgrades)
### Modified
- `errander/execution/commands.py` — added `refresh_package_lists()` abstract method to `PackageManager`; `AptManager` returns `apt-get update -qq`, `DnfManager` returns `dnf makecache --quiet 2>/dev/null || true`
- `errander/agent/subgraphs/patching.py` — `assess_node` now calls `refresh_package_lists()` before `list_upgradable()`; refresh failure is non-fatal (logs warning, continues with stale index)
- `tests/agent/subgraphs/test_patching.py` — all `assess_node` and integration tests updated to mock refresh call (now 2 executor calls in assess: refresh + list); 34/34 passing

## Files Changed (This Session)
### Modified
- `errander/agent/decisions.py` — LLM wired in: all decision functions accept optional llm_client, fall back to hardcoded
- `errander/integrations/llm.py` — Full LLMClient implementation
- `tests/integrations/test_llm.py` — 23 tests
- `errander/agent/vm_graph.py` — Full per-VM graph implementation
- `errander/agent/graph.py` — Full batch orchestrator implementation
- `tests/agent/test_decisions.py` — 23 tests (updated for new llm_client signature)
- `tests/agent/test_vm_graph.py` — 28 tests
- `tests/agent/test_graph.py` — 21 tests
- `errander/main.py` — full entry point implementation
- `errander/agent/graph.py` — validate_window_node wired + build_batch_graph accepts window
- `errander/config/schema.py` — EnvironmentSchema.maintenance_timezone field added
- `example/inventory.yaml` — maintenance_timezone field added to all environments
- `tests/agent/test_graph.py` — 4 new window node tests (25 total)
- `tests/test_main.py` — 17 tests for CLI parsing and helper functions
- `errander/scheduling/windows.py` — is_within_window, MaintenanceWindow dataclass
- `errander/scheduling/scheduler.py` — MaintenanceScheduler wrapping AsyncIOScheduler
- `tests/scheduling/test_windows.py` — 25 tests
- `tests/scheduling/test_scheduler.py` — 11 tests
- `example/inventory.yaml` — annotated reference inventory
- `example/settings.yaml` — annotated reference settings
- `errander/integrations/slack.py` — Full SlackClient implementation
- `errander/safety/approval.py` — request_approval + poll_approval
- `errander/observability/metrics.py` — Prometheus registry + HTTP server
- `errander/observability/tracking.py` — record_action_result, record_ssh_error, record_llm_outcome
- `tests/integrations/test_slack.py` — 10 tests
- `tests/safety/test_approval.py` — 21 tests
- `tests/observability/test_metrics.py` — 20 tests
- `docs/SETUP.md` — full setup guide: prerequisites, vLLM, SSH, Slack, config, first run, systemd service, monitoring, troubleshooting
- `docs/learning/13-vllm-setup.md` — learning doc: GPU passthrough, host volumes, healthcheck start_period, check_endpoint design
- `deploy/vllm/docker-compose.yml` — production vLLM container with GPU passthrough
- `deploy/vllm/.env.example` — configurable deployment vars
- `errander/integrations/llm.py` — check_endpoint() method with model list + latency
- `errander/main.py` — --check-llm flag + run_llm_check()
- `errander/observability/metrics.py` — UI routes + handlers, typed AppKey, AuditStore import
- `docs/learning/12-web-ui.md` — learning doc: AppKey, slash URL matching, same-server architecture, Pico.css
- `errander/main.py` — pass audit_store to start_metrics_server()
- `errander/safety/audit.py` — action_type filter in get_events(), get_recent_batches() method added
- `errander/main.py` — --audit CLI mode, run_audit_query(), EventType import
- `tests/safety/test_audit_integration.py` — 21 integration tests (created)
- `docs/learning/11-sqlite-audit.md` — learning doc: GROUP_CONCAT aggregation, CLI short-circuit pattern, integration test strategy
- `tasks/todo.md` — Phases 1.4/1.5/1.6/1.7 items checked off; Phase 4 tasks added and checked off
- `docs/command-log.md` — Phase 1.6 + 1.7 + 4 commands added
- `tasks/lessons.md` — aiohttp async CM, rate-limit retry, APScheduler __slots__, DST offset, web.Response, Phase 4 lessons added
- `tasks/phase4-llm-flexibility-and-ui-config.md` — status updated to Complete

### Phase 4 — Modified
- `errander/integrations/llm.py` — removed hardcoded model/thinking-mode, added model+temperature params
- `errander/integrations/secrets.py` — rewritten: SecretsManager with Fernet enc:v1: format
- `errander/config/schema.py` — added _decrypt_yaml_strings, LLMSettingsSchema.model+temperature, validators
- `errander/config/settings.py` — added llm_model, llm_temperature, ui_user, ui_password, sources, db_overrides param
- `errander/agent/decisions.py` — removed thinking= kwarg from all complete() calls
- `errander/main.py` — added --generate-secrets-key, --encrypt flags; OverridesStore init; inventory merge; overrides_store wired to scheduler loop
- `errander/observability/metrics.py` — Basic Auth middleware, /ui/settings and /ui/inventory routes + handlers
- `errander/models/events.py` — added SETTINGS_CHANGED and INVENTORY_CHANGED to EventType
- `config/settings.yaml` — added llm.model and llm.temperature fields
- `example/settings.yaml` — added llm.model and llm.temperature fields
- `tests/integrations/test_llm.py` — rewritten: removed thinking tests, added verbatim/temp/model tests
- `docs/SETUP.md` — added Step 5b: Secure the Web UI
- `docs/learning/README.md` — added entries 20, 21, 22

### Phase 4 — Created
- `errander/safety/overrides.py` — OverridesStore: settings_overrides + inventory_overrides SQLite tables
- `errander/observability/redaction.py` — SecretsRedactingFilter log filter
- `tests/integrations/test_secrets.py` — 24 SecretsManager tests
- `tests/observability/test_redaction.py` — 9 redaction filter tests
- `tests/config/test_secrets_loading.py` — 6 YAML/env decryption integration tests
- `tests/safety/test_overrides.py` — 18 OverridesStore tests (T1)
- `tests/config/test_settings_precedence.py` — 21 settings precedence tests (T2)
- `tests/agent/test_inventory_merge.py` — 9 inventory merge tests (T3)
- `docs/learning/22-ui-settings-and-inventory.md` — learning doc: precedence chain, DB schema, merge algo, Basic Auth
- `docs/LLM-PROVIDERS.md` — provider config reference (vLLM, Ollama, OpenAI, Anthropic, Groq)
- `docs/SECRETS.md` — encryption setup guide, threat model, key rotation

## Decisions Made (Phase 4)
- **`load_settings()` stays synchronous**: Accepts pre-fetched `db_overrides: dict[str, str]` instead of `OverridesStore` directly — keeps the sync call chain intact and makes testing simple.
- **`enc:v1:` prefix for encrypted values**: Prefix-tagged format makes it easy to detect encrypted vs plaintext at any layer (env var, YAML, DB) without needing a separate `is_secret` flag.
- **Basic Auth on `/ui/*` only**: The `/metrics` and `/health` endpoints remain open (Prometheus scrapers don't support auth by default). Auth is scoped to the human-facing routes.
- **`secrets.compare_digest()` for password check**: Constant-time comparison prevents timing oracle attacks — critical for a network-exposed auth check.
- **Inventory merge in `run_env_batch()`**: The merge happens at batch invocation time, not at startup — operators can change inventory via the UI and the very next scheduled run picks it up without restart.
- **`_name` temporary field pattern**: YAML target dicts get `_name` injected for filter lookup, then `del`-ed before the list reaches the graph. Avoids passing unknown fields into graph state.

## Test Count
918 passed, 111 skipped (Playwright UI tests, excluded without Chromium browser).

### Phase 0: SRE Audit Remediation (complete)

Implemented all Phase 0 fixes from `ai_sre_remediation_plan.md`:

- **Finding #2 (dry_run single source of truth)**: `SandboxExecutor.execute()` now accepts per-call `dry_run` override. All sub-graphs read `state["dry_run"]` instead of `executor.dry_run`.
- **Finding #3 (plan/apply before execution)**: New planning phase fan-out (`plan_vm` → `collect_plans` → `generate_plan_artifact`) between `validate_targets` and execution. Approval gate acts on the plan hash BEFORE any execution. `ImmutablePlan` with SHA-256 `plan_hash`.
- **Finding #3 (hash re-verification)**: `verify_plan_hash_node` re-computes SHA-256 from current state at execution time. Any drift between approval and execution aborts the batch and routes to `generate_report`. Wired between `approval_gate` and `prepare_waves`.
- **Finding #5 (patching rollback — Option A)**: `rollback_node` in patching sub-graph implements real dpkg snapshot + `apt-get install --allow-downgrades` + post-rollback verification. Activated on `FAILED` execution status.
- **Finding #6 (policy-based approval thresholds)**: `env_policy` threaded from `EnvironmentSchema.approval_policy` → `initial_state` → `BatchGraphState`. `approval_gate_node` now enforces: strict = MEDIUM/HIGH/CRITICAL require approval; moderate = HIGH/CRITICAL; relaxed = CRITICAL only.
- **Finding #13 (audit fail-closed)**: `AuditWriteError` raised after retry exhaustion in strict mode for live actions. Dry-run always best-effort.
- **Phase 0 gate**: `--unsafe-legacy-live` guard blocks live mode until Phase 0 is marked complete.

All 787 unit/integration tests pass (111 skipped = Playwright UI tests, excluded without Chromium). Includes 20 new `test_plan_apply_flow.py` tests.

## Files Changed (2026-05-12 — Re-audit: 7 production blockers)

### Modified (source)
- `errander/agent/graph.py` — `plan_vm_node` passes `llm_client`, `ai_decision_store`, `env_policy`, `batch_id`, `vm_id` to `prioritize_actions()`; `_plan_vm` closure captures both; `_route_plan_vms` injects `env_policy` into Send; `dispatch_current_wave` builds `vm_id_to_approved_actions` lookup and passes `planned_actions` per-VM
- `errander/agent/vm_graph.py` — `route_after_drift_check` returns `"dispatch_action"` when `planned_actions` pre-populated (skip re-planning); `drift_check` conditional edges extended to include `"dispatch_action"`
- `errander/agent/subgraphs/patching.py` — `assess_node`: `dry_run=False` on `refresh_package_lists()` and `list_upgradable()`; `snapshot_node`: `dry_run=False`; `verify_node`: `dry_run=False`, sets `status=FAILED` on SSH failure or no version changes; `route_after_verify` routes FAILED to rollback; `rollback_node` passes `os_family`; graph wired with `add_conditional_edges("verify", route_after_verify, ["rollback", END])`
- `errander/agent/subgraphs/disk_cleanup.py` — `assess_node` and `verify_node`: all SSH calls use `dry_run=False`
- `errander/agent/subgraphs/docker_prune.py` — `assess_node` and `verify_node`: all SSH calls use `dry_run=False`
- `errander/agent/subgraphs/log_rotation.py` — `assess_node`: `dry_run=False`
- `errander/agent/subgraphs/backup_verify.py` — `assess_node`: `dry_run=False`
- `errander/main.py` — `--unsafe-legacy-live` removed; live guard block removed; `AuditStore` constructed with `strict_mode=(settings.audit_mode == "strict")`; `run_audit_query` uses `strict_mode=False`
- `errander/safety/rollback.py` — `os_family` param added to `rollback_action` and all `_rollback_*` helpers; `_rollback_patching` dispatches to `_rollback_patching_apt` or `_rollback_patching_dnf`; `_rollback_patching_dnf` uses `dnf downgrade` + `rpm -q`

### Modified (tests)
- `tests/agent/test_vm_graph.py` — `test_full_dry_run_disk_cleanup`: 10→12 SSH responses (added yum-cache assess + yum-cache execute simulate)
- `tests/agent/test_graph.py` — `test_full_dry_run_single_vm`: same 10→12 SSH responses

## Files Changed (2026-05-12 — Fourth-round audit: action params in plan artifact)

### Modified (source)
- `errander/agent/graph.py` — `plan_vm_node`: added `"params": a.params` to serialized `planned_actions` so params are included in plan hash and wave dispatch; `_format_plan_for_approval`: surfaces non-empty action params (up to 3 key=value pairs) in Slack summary so operators see exactly what will run

### Modified (tests)
- `tests/agent/test_plan_apply_flow.py` — `TestActionParamsSurvivePlanning` class: 4 tests proving params affect plan hash, empty params hash stability, params surface in Slack approval summary, params survive to wave dispatch

## Files Changed (2026-05-12 — Third-round audit: 2 blockers + 2 high risks)

### Modified (source)
- `errander/agent/vm_graph.py` — Added `pre_approved_plan_set: bool` to `VMGraphState`; `route_after_drift_check` now distinguishes "approved empty plan" (→ audit_results, no re-plan) from "no plan yet" (→ plan_actions); uses sentinel instead of truthiness check on `planned_actions`
- `errander/agent/graph.py` — `dispatch_current_wave` converted from list comprehension to for-loop; injects `pre_approved_plan_set=True` for all VMs with approved plan; live mode + VM missing from approved plan → `error` set + `pre_approved_plan_set=True` (fail closed); dry-run + missing plan → `pre_approved_plan_set=False` (allow re-planning)
- `errander/agent/subgraphs/log_rotation.py` — `verify_node`: added `dry_run=False` to SSH call (was missing — verification could use synthetic dry-run output instead of real VM state)
- `errander/safety/rollback.py` — `_rollback_patching_dnf`: added version comparison after `rpm -q` (parse output line-by-line, compare against snapshot, return `False` on any mismatch — mirrors APT rollback verification)

### Modified (tests)
- `tests/agent/test_vm_graph.py` — 3 new `TestRoutingDriftCheck` tests: pre_approved_non_empty→dispatch_action, pre_approved_empty→audit_results, pre_approved_with_error→audit_results
- `tests/agent/subgraphs/test_log_rotation.py` — 1 new `TestVerifyNode` test: asserts `dry_run=False` is passed even when executor is in dry-run mode
- `tests/safety/test_rollback.py` — `TestDnfRollbackVersionVerification` class: 3 tests (version match, version mismatch, package missing from rpm output)

## Test Count
929 passed, 111 skipped (Playwright UI tests, excluded without Chromium).
