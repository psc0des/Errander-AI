## Doc sync — README stale sections + login + UI bind (2026-05-25, COMPLETE)

- [x] README.md: replace Docker Prune with docker_hygiene in Action Types table
- [x] README.md: fix Safety Gates table (docker prune → docker hygiene, correct tiers)
- [x] README.md: add login page section to Web UI
- [x] README.md: add ERRANDER_UI_BIND to configure section
- [x] STATUS.md, todo.md, lessons.md, command-log.md: sync all session phases

---

## Login page — feature bullet wording (2026-05-25, COMPLETE)

- [x] Replace patch-focused bullets with platform-scope bullets: "Autonomous fleet maintenance", "Human-in-the-loop approvals", "Safe by design", "Full audit trail"

---

## Session-cookie login page — Stitch redesign (2026-05-25, COMPLETE)

- [x] Split layout: left indigo shell (#1e1b4b) with LED + bullets; right white card on #f6f2ff
- [x] Gradient "Sign in →" button; Space Grotesk/JetBrains Mono/Inter fonts
- [x] Playwright tests rewritten: 302 redirect, `.lc-err` selector, correct creds grant access

---

## Session-cookie auth — replace Basic Auth popup (2026-05-25, COMPLETE)

- [x] `_session_auth_middleware` — protects /ui/* routes, redirects to /ui/login on miss
- [x] `_ui_login_get` / `_ui_login_post` — HTML form, HMAC-signed session cookie, 8-hour TTL
- [x] `_ui_logout` — clears session cookie
- [x] `/metrics` and `/health` remain open regardless of auth config
- [x] "Sign out" link added to sidebar

---

## ERRANDER_UI_BIND — network-accessible Web UI (2026-05-25, COMPLETE)

- [x] Add `ERRANDER_UI_BIND=0.0.0.0` to configure.sh `.env` write block
- [x] SETUP.md and README.md updated to document the var

---

## Systemd EnvironmentFile — optional key file loading (2026-05-25, COMPLETE)

- [x] SETUP.md unit template: add `KEY_PATH=$(echo ~/.errander.key)` + `EnvironmentFile=-${KEY_PATH}`
- [x] `-` prefix makes it optional — unit doesn't fail when no encrypted install

---

## SETUP.md — Step 8/9 multi-env and service-mode clarifications (2026-05-25, COMPLETE)

- [x] Step 8: per-env dry-run examples (multiple envs); clarify `--env` is per environment
- [x] Step 9: intro paragraph explaining service mode runs all envs on schedule; web UI access section

---

## docker_available: wire enabled_actions into run_vm Send payloads (2026-05-25, COMPLETE)

- [x] Read both VMGraphState constructions for run_vm in graph.py
- [x] Pass `enabled_actions` in `route_after_validate` (dry-run Send) and `dispatch_current_wave` (live Send)
- [x] All 2507 tests pass, mypy clean

---

## docker_available execution-phase fallback (2026-05-25, COMPLETE)

- [x] Add `enabled_actions: list[str]` to `VMGraphState` TypedDict
- [x] In `discover_node`, after `detect_os()`, probe `sudo -n errander-docker-assess-v2 --check` when `docker_available=False` and `docker_hygiene` in `enabled_actions`
- [x] Override `docker_available=True` on wrapper check success
- [x] All 2507 tests pass, mypy clean

---

## Dry-run report UX overhaul (2026-05-25, COMPLETE)

- [x] `_parse_du_size` / `_parse_journal_size` / `_parse_tmp_size` in disk_cleanup.py; apply at assess time
- [x] `BatchReport.dry_run: bool = False`; pass from `generate_report_node`
- [x] `render_batch_report`: dry-run title, env label, "○ queued" vs "⊘ skipped", hide 0-login noise, next-steps section
- [x] All 2507 tests pass, ruff + mypy clean

---

## docker_hygiene wrapper-mode docker_available fallback (2026-05-25, COMPLETE)

- [x] Diagnose: `docker info` without sudo fails for errander user → `docker_available=False` → planner skips docker_hygiene
- [x] Fix: in `plan_vm_node`, probe `sudo -n errander-docker-assess-v2 --check` as fallback when `docker_available=False` and docker_hygiene is in `enabled_actions`
- [x] All 2507 tests pass, ruff + mypy clean

---

## service_restart excluded from automated batch planning (2026-05-24, COMPLETE)

- [x] Add `operator_triggered: bool = False` to `ActionManifest` in `models/manifest.py`
- [x] Set `operator_triggered=True` in `service_restart.MANIFEST`
- [x] Filter operator-triggered actions out of `_enabled_actions` in `main.py` batch init
- [x] All 2507 tests pass, ruff + mypy clean

---

## --check-targets ALLOWLIST OK confirmation (2026-05-24, COMPLETE)

- [x] Print `ALLOWLIST OK vm: unit(s) (N unit(s) verified)` when allowlist matches inventory
- [x] Update `test_no_drift_when_allowlist_matches` to assert `ALLOWLIST OK` appears

---

## configure.sh SSH host key fix (2026-05-24, COMPLETE)

- [x] Add `ERRANDER_SSH_STRICT_HOST_KEYS=false` to `.env` write in configure.sh (TOFU mode by default)
- [x] Add SSH bootstrap prompt after LLM verify — runs `--bootstrap-known-hosts <env>`, flips to strict on success
- [x] Add `--check-targets <env>` to Done banner Step 6
- [x] SETUP.md: update configure.sh description, add SSH section to `.env` template, add SSH vars to env var table
- [x] STATUS.md, todo.md, lessons.md, command-log.md doc sync

---

## Workspace hygiene — gitignore cleanup (2026-05-24, COMPLETE)

- [x] Add `.gitignore` patterns: `*.sqlite-journal/wal/shm`, `.playwright-mcp/`, `errander-*.png`, `tmp_*.yaml`, `approvals_text.txt`
- [x] Delete 10 untracked artifact files from working tree
- [x] Push to origin — working tree clean

---

## Repo-wide quality gate cleanup (2026-05-24, COMPLETE)

**Status:** All items complete. 2507 tests passing. `ruff check .` → 0 errors. `mypy errander/` → 0 errors.

- [x] `uv run ruff check --fix .` — auto-fixed 237 violations
- [x] Manual ruff fixes: SIM117, SIM102, B905, B007, N806, N814, E741, E402, E701, E702, F841, E501, TC001/TC003
- [x] mypy: `ServiceRestartState` import, `SandboxExecutor` missing arg, checkpointer `Any` annotation, unused-ignore cleanup, aiosqlite Row cast
- [x] Full test suite: 2507 passed, 0 failures
- [x] Doc sync

---

## SRE P2 Fixes (2026-05-24, COMPLETE)

**Status:** All items complete. 2507 tests passing, ruff clean on changed files.

- [x] P2.1: evidence validation always runs — remove `if valid_sources:` guard; when sources_used is empty, all evidence is hallucinated and stripped
- [x] P2.2: `context_snapshot` now includes `redaction_count`, `vms_dropped`, `fields_truncated`, `entries_truncated`
- [x] 2 new tests: `test_investigate_strips_evidence_when_sources_empty` + `test_investigate_context_snapshot_includes_budget_and_redaction_stats`

---

## SRE Trust Gap Fixes — P1 (2026-05-24, COMPLETE)

**Status:** All items complete. 2505 tests passing, ruff clean on changed files.

- [x] Finding 1: centralize LLM redaction — `_REDACTOR` in `decisions.py`; belt-and-suspenders in `LLMClient.complete()`
- [x] Finding 2: `OperatorAssistant.investigate()` accepts `ai_decision_store`; logs `AIDecision(decision_type="operator_assistant")`
- [x] Finding 3: validate `finding.evidence` IDs against `context.sources_used`; strip unknowns
- [x] Finding 4 (debt): pre-existing ruff/mypy errors documented below — not introduced by AI Trust Layer work
- [x] 9 new targeted tests: `TestRedactionInDecisionPaths` (4) + 5 audit/evidence tests
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, README.md, learning/49-sre-trust-gap-fixes.md

### Pre-existing ruff/mypy debt (tracked, not from AI Trust Layer)
- `mypy errander/main.py` has 4 pre-existing errors: `attr-defined` for `ServiceRestartState`, `call-arg` for `SandboxExecutor`, `unused-ignore`, `arg-type` for `AsyncSqliteSaver` checkpointer
- `ruff check .` pre-existing errors (not from AI Trust Layer changes) — tracked for cleanup in a future chore commit

---

## AI Trust Layer — Phase 6a: Provider Prefix/Input Caching (2026-05-24, COMPLETE)

**Status:** All items complete. 2496 tests passing, ruff + mypy clean on changed files.

- [x] `errander/integrations/llm.py` — `_prefix_cache` auto-detection from `base_url`; `_build_messages()` helper; `complete()` uses `_build_messages()`
- [x] `tests/integrations/test_llm.py` — `TestPrefixCaching` (11 tests)
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, README.md, learning/48-ai-prefix-caching.md

---

## AI Trust Layer — Phase 5: Source Citation for AI Answers (2026-05-24, COMPLETE)

**Status:** All items complete. 2485 tests passing, ruff + mypy clean on changed files.

- [x] `errander/models/analysis.py` — new `Finding(text, evidence, is_cited)` model; `AssistantResponse.findings: list[Finding]`; `@field_validator` backward-compat coercion
- [x] `errander/agent/operator_assistant.py` — `Finding` imports; updated prompt schema with `evidence` + valid source IDs; `_fallback_response()` constructs typed `Finding` with citations
- [x] `tests/agent/test_operator_assistant.py` — fix 5 string-accessor assertions; add 8 citation tests
- [x] `tests/agent/test_operator_assistant_facts.py` — fix 4 string-accessor assertions; add 2 citation tests
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, README.md, learning/47-ai-source-citation.md

---

## AI Trust Layer — Phase 4: Operational Memory Confidence (2026-05-24, COMPLETE)

**Status:** All items complete. 2475 tests passing, ruff + mypy clean on changed files.

- [x] `errander/safety/vm_facts.py` — `_sample_confidence()`, `_rejection_confidence()` helpers; `@computed_field confidence` on `ActionOutcomeFact`, `VMRebootPatternFact`, `ActionRejectionFact`
- [x] `errander/agent/operator_assistant.py` — include `confidence:` label in `_format_prompt()` fact lines
- [x] `tests/safety/test_vm_facts.py` — `TestConfidenceLabels` (10 tests covering all tiers + boundaries)
- [x] `tests/agent/test_operator_assistant_facts.py` — 3 new prompt confidence label tests
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, README.md, learning/46

---

## AI Trust Layer — Phase 2: Prompt Versioning & Replay Evals (2026-05-24, COMPLETE)

**Status:** All items complete. 2462 tests passing, ruff + mypy clean on changed files.

- [x] `errander/safety/migrations.py` — migration 9: `ai_eval_runs` + `ai_eval_results` tables
- [x] `errander/evals/__init__.py` (NEW) — package init
- [x] `errander/evals/replay.py` (NEW) — `check_assertions`, `EvalStore`, `EvalResult`, `EvalRun`, `run_replay`
- [x] `errander/main.py` — `--ai-eval-replay`, `--eval-model` flags + `run_ai_eval_replay()`
- [x] `tests/ai_evals/test_replay.py` (NEW) — 28 tests
- [x] `tests/safety/test_migrations.py` — migration count 9→10, expected tables updated
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, README.md, learning/45
- [x] Green tree: 2462 tests passing

---

## AI Trust Layer — Phase 3: Context Budget & Redaction Policy (2026-05-24, COMPLETE)

**Status:** All items complete. 2434 tests passing, ruff + mypy clean on changed files.

- [x] `errander/safety/context_redactor.py` (NEW) — `ContextRedactor`: OpenAI key, AWS key, password, bearer token, PEM block; IP opt-in
- [x] `errander/safety/context_budget.py` (NEW) — `ContextBudgeter`: max_vms, max_log_entries_per_vm, max_chars_per_field
- [x] `errander/agent/operator_assistant.py` — wire both into `investigate()` with log warnings
- [x] `tests/safety/test_context_redactor.py` (NEW) — 24 tests
- [x] `tests/safety/test_context_budget.py` (NEW) — 13 tests
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, README.md, learning/44

---

## AI Trust Layer — Phase 1: Decision Explainability + Adversarial Tests (2026-05-23, COMPLETE)

**Status:** All items complete. 2397 tests passing, ruff clean on changed files.

- [x] `ai_audit.py` — expose `id` PK in `_SELECT_SQL`, add `decision_id` field + `get_decision_by_id()`
- [x] `main.py` — `--ai-decisions`, `--ai-decision-show`, `--decision-type` CLI flags + `run_ai_decisions_query()` + long-lived web UI store
- [x] `metrics.py` — `_AI_DECISION_STORE_KEY`, `_ui_ai_decisions()`, `_ui_ai_decision_detail()`, nav link, routes
- [x] `tests/safety/test_ai_decisions_store.py` (NEW) — 5 tests
- [x] `tests/ai_evals/test_adversarial.py` (NEW) — 25 adversarial tests
- [x] Doc sync: STATUS.md, todo.md, lessons.md, learning/43-ai-decision-explainability.md, README.md test count
- [x] Green tree: 2397 tests passing

---

## Post-Residual Fixes — deferred hygiene wiring + doc P2 (2026-05-23, COMPLETE)

**Status:** All 3 findings resolved. 846 agent/main tests passing.

- [x] P1: `_window_opener` missing `hygiene_manager` param — added to signature + both `run_env_batch` calls + call site in scheduler
- [x] P1 test: `test_window_opener_passes_hygiene_manager` in `test_deferred_replay.py` (17 tests passing)
- [x] P2: SETUP.md sample + CLI `--unit` help updated to `.service` suffix
- [x] Doc sync: STATUS.md, todo.md, lessons.md

---

## SRE Residual Fixes — 5 issues from Opus 4.7 validation (2026-05-23, COMPLETE)

**Status:** All 5 residual findings resolved. 2366 tests passing, ruff clean.

- [x] Finding 1 (P1): Docker assess wrapper — add `--no-trunc` to `docker images` calls so full SHA256 IDs are emitted; `grep -Fx` in remove wrapper now matches correctly
- [x] Finding 1 tests: `TestWrapperIdFormat` (3 tests) in `test_docker_hygiene.py`
- [x] Finding 2 (P1): `run_restart_service()` — add maintenance window check (hard block; `--restart-force --restart-force-reason` to override) + per-VM `FileLocker` acquire/release (TTL=300s)
- [x] Finding 2 CLI args: `--restart-force`, `--restart-force-reason` added to `_parse_args()`
- [x] Finding 2 tests: `TestRestartServiceWindowAndLock` (4 tests) in `test_service_restart_cli.py`
- [x] Finding 3 (P1/P2): All bare unit names in docs/examples updated to `.service` suffix (18 locations across 5 files)
- [x] Finding 4 (P2): orphaned-deps exact preview — `_parse_autoremove_candidates()` helper, `orphaned_candidates` state field, drift gate in execute_node, package list in Slack approval
- [x] Finding 4 tests: `TestOrphanedDepsExactPreview` (5 tests) in `test_disk_cleanup.py`
- [x] Finding 5 (Decision): CLAUDE.md updated — categorical acceptable for LOW-risk whitelist-bounded actions; orphaned-deps is the exception
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, learning/49-sre-residual-fixes.md, README.md
- [x] Green tree: 2366 tests passing

## AI SRE Gap Fix — 7 safety/quality fixes (2026-05-23, COMPLETE)

**Status:** All 7 valid findings addressed. 2354 tests passing.

- [x] P0-1: Wire `service_restart` live mode in `main.py` + `vm_graph.py` (Slack approval gate + subgraph execution)
- [x] P1-1: Already fixed in v1.1 (docker_prune removed, docker_hygiene is sole Docker action)
- [x] P1-2: Remove global `logrotate --force` from log_rotation execute_node; per-file only
- [x] P1-3: Remove `orphaned-deps` from DEFAULT_CLEANUP_PATHS; require explicit opt-in
- [x] P1-4: Per-action coverage table [EXACT]/[CATEGORICAL]/[ADVISORY] in approval message
- [x] P2-1: Full plan inspectable: signed web URL + `--plan-show` CLI + `plan_snapshots` DB table
- [x] P2-2: Fix `backup_verify.py` docstring risk tier ("High" → "Low")
- [x] P2-3: `safe_systemd_unit_name()` validator in command_builder + service_restart nodes + config schema
- [x] New tests: test_service_restart_cli.py, test_plan_inspection_p21.py, adversarial unit name tests, orphaned-deps opt-in tests, backup_verify manifest test, coverage table tests
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md, learning/48-ai-sre-gap-fix.md
- [x] Green tree: 2354 tests passing

## Docker hygiene — v1.1 implementation (2026-05-21, SESSION 1 COMPLETE)

**Status:** Session 1 (assessment foundation) shipped 2026-05-21. Session 2 (execution + dual approval surface) pending.

### Session 1 completed (2026-05-21)
- [x] `errander/models/actions.py` — `ActionType.DOCKER_HYGIENE` + risk tier MEDIUM
- [x] `errander/models/docker_hygiene.py` (NEW) — `DockerResourceClass`, `FindingClassification`, `DockerHygieneFinding`, `DockerHygieneAssessment` models
- [x] `errander/agent/subgraphs/docker_hygiene.py` (NEW) — MANIFEST + validate + assess nodes + parser + classification rules + graph builder (no execute yet)
- [x] `errander/agent/subgraphs/__init__.py` — register `docker_hygiene` in BUILTIN_ACTIONS (docker_prune still present)
- [x] `errander/execution/target_validation.py` — skip docker_hygiene in generic loop (alongside docker_prune); add explicit probe block gated by `enabled_actions`
- [x] `scripts/install-docker-wrappers-v2.sh` (NEW) — `errander-docker-assess-v2` wrapper (5-class output) + `errander-docker-remove-v2` stub + sudoers
- [x] `tests/agent/subgraphs/test_docker_hygiene.py` (NEW) — 40 tests: parser (every class), classification (every cell), validate node, assess node happy paths, idempotency, graph builder smoke
- [x] `tests/agent/subgraphs/test_registry.py` — bump count 6→7, add docker_hygiene-specific assertions
- [x] `tests/agent/subgraphs/test_service_restart_manifest.py` — bump count 6→7
- [x] Green tree: **2215 pytest, ruff clean on changed files, no new mypy errors**

### Session 2a completed (2026-05-22)
- [x] `errander/models/docker_hygiene.py` — added `ApprovalSurface`, `RemovalStatus` enums + `DockerHygieneApproval`, `DockerHygieneRemovalResult` dataclasses + `compute_assessment_hash` helper
- [x] `errander/models/events.py` — added 3 new EventType entries for per-object audit
- [x] `errander/agent/subgraphs/docker_hygiene.py` — `execute_node`, `parse_remove_v2_output`, drift gate, updated routing + graph builder
- [x] `errander/agent/vm_graph.py` — dispatch wiring (`docker_hygiene_compiled` threaded through), `_run_docker_hygiene` runner, `_write_docker_hygiene_per_object_audit` helper
- [x] `scripts/install-docker-wrappers-v2.sh` — real `errander-docker-remove-v2` (replaces Session 1 stub)
- [x] Tests: 22 new (parse_remove_v2_output, execute_node, compute_assessment_hash, dispatch routing, per-object audit hook). 2 routing tests updated.
- [x] Green tree: **2237 pytest, ruff clean on changed files, no new mypy errors**
- [x] Doc sync: README test count, STATUS, todo, lessons, command-log

### Defense-in-depth for LLM continuity (2026-05-22, COMPLETE)
- [x] CLAUDE.md → new "Implementation Contracts" subsection naming the two contracts (layered drift gates, per-object parser invariants)
- [x] `# INVARIANT:` markers at 5 load-bearing sites (compute_assessment_hash, execute_node drift gate, parse_remove_v2_output × 2 branches, wrapper preamble) — grep-discoverable, each cites CLAUDE.md
- [x] New memory entry `pattern_object_level_approval.md` + MEMORY.md index — auto-loaded, signposts docker_hygiene as canonical pattern
- [x] CLAUDE.md doc-sync rule extended: "Pre-flight check before destructive-action work" requires re-reading Implementation Contracts + grepping `INVARIANT` + mirroring reference implementation
- [x] New lesson capturing the meta-pattern (vibe-coding with LLMs requires defense-in-depth for invariants)

### Session 2b-i shipped (2026-05-22)
- [x] `errander/integrations/signed_url.py` (NEW) — `make_signed_token`/`verify_signed_token` (HMAC-SHA256, time-limited, constant-time compare), `SigningSecretMissingError` (loud failure)
- [x] `errander/safety/hygiene_approval.py` (NEW) — `format_hygiene_approval_message`, `parse_hygiene_reply`, `HygieneApprovalManager`
- [x] 52 new tests: 17 signed-URL (roundtrip / tamper / expiry / secret resolution) + 35 hygiene approval (formatter / parser / manager)
- [x] Green tree: **2289 pytest, ruff clean on new files, no new mypy errors**
- [x] Doc sync: README test count, STATUS, todo, lessons, command-log

### Session 2b-ii shipped (2026-05-22)
- [x] `errander/web/server.py` — `page_hygiene_approve` renderer + `handle_hygiene_approve_get` / `handle_hygiene_approve_post` route handlers + supporting helpers. Routes registered.
- [x] `errander/integrations/slack.py` — `SlackClient.conversations_replies` method
- [x] `errander/safety/hygiene_approval.py` — `poll_hygiene_replies_once` polling helper
- [x] 20 new tests: `tests/safety/test_hygiene_web_approve.py` (11) + `tests/safety/test_hygiene_reply_polling.py` (9)
- [x] Green tree: **2309 pytest, ruff clean on new code, no new mypy errors**
- [x] Doc sync: README test count, STATUS, todo, lessons, command-log

### Session 2b-iii (COMPLETE — 2026-05-22)
- [x] `errander/config/settings.py` — `web_base_url` field + `ERRANDER_WEB_BASE_URL` env var
- [x] `errander/observability/metrics.py` — `_HYGIENE_MANAGER_KEY` typed AppKey, GET/POST handlers for `/ui/docker-hygiene/approve`, `hygiene_manager` param in `start_metrics_server()`
- [x] `errander/agent/vm_graph.py` — full `_run_docker_hygiene` orchestration: fast-path, assess-only, Slack+URL post, manager register, background poll, `wait_for_decision`, execute; params threaded through `dispatch_action_node` + `build_vm_graph`
- [x] `errander/agent/graph.py` — hygiene/slack/web params threaded through `make_wave_dispatcher` + `build_batch_graph`
- [x] `errander/main.py` — `HygieneApprovalManager` instantiated, passed to metrics server + both `run_env_batch()` calls; stale docker_prune refs fixed
- [x] `tests/agent/test_hygiene_orchestration.py` (NEW, 8 tests) — approve path, rejection, timeout, no-candidates, dry-run, no-manager, pre-injected fast-path, signed URL in Slack
- [x] Green tree: **2317 pytest, ruff clean on changed files, no new mypy errors**
- [x] Doc sync: README test count, STATUS, todo, lessons, command-log

### Session 3 — Removal of docker_prune + final docs
**Status:** COMPLETE (2026-05-22).
- [x] Delete `docker_prune.py`, `test_docker_prune.py`, `test_docker_prune_modes.py`, `test_docker_prune_scope.py`, docker_prune branch from `vm_graph.py`, legacy wrapper install script + its tests
- [x] `schema.py` loader fails loud on legacy `docker_prune` inventory key with migration command; docker_hygiene contradiction check added
- [x] `migrate.py` extension for `docker_prune` → `docker_hygiene` rename + drop unsupported `direct_sudo` mode
- [x] SETUP.md rewrite: "Optional: Docker hygiene"; transition note + direct_sudo shortcut removed
- [x] `docs/learning/45-docker-hygiene-session3-cutover.md` (NEW)
- [x] README test count updated (2317 → 2252); CLAUDE.md v1.1 transition note updated (6 actions confirmed)
- [x] 11 test files updated to reflect docker_prune removal
- [x] Green tree: 2252 passing tests
**Trigger:** SRE feedback (Docker prune scope review, 2026-05-21) — current `docker_prune` is too blunt for serious SRE use. Verdict: split into a richer assessment surface with object-level approval.
**Driving invariant:** New exact-object approval rule in CLAUDE.md (Layer B → AI Safety Invariant → Exact-Object Approval section, added 2026-05-21). Applies to all destructive actions from day one of v1.1.

**Locked decisions (2026-05-21):**
- **Implementation horizon:** Now. Next 1–2 sessions.
- **Approval UI:** Both surfaces. Slack structured reply for ≤10-object sets; thin web approval page (FastHTML, signed URL) for larger sets. Both write the same approval artifact to `ai_decisions`. Slack message always includes the web URL so either path works.
- **Legacy `docker_prune`:** **Removed altogether.** No grandfathering — keeping a bulk action-level mode contradicts the new Exact-Object Approval invariant. Config loader fails loud on the legacy key with a migration command. Existing audit rows referencing `docker_prune` stay readable but no new rows can use that action_type.

### Background — what's wrong with the current `docker_prune`

- Treats Docker as one cleanup action. Operator approves "docker_prune" with no view of what will be removed.
- Hides three distinct resource classes (images, containers, build cache) behind one button. A fourth (volumes) is not addressed at all.
- Approval is action-level ("approve the action") not object-level ("approve these 4 image IDs"). Violates the new Exact-Object Approval invariant.
- `prune_aggressive` mode (`docker image prune -a`) removes *all* unused images, not just dangling — same approval gesture, very different blast radius. No way for the operator to see which is which from the Slack message alone.
- No surfacing of stopped-container exit codes. An exited-137 container (OOM) is a *signal to investigate*, not a cleanup candidate. Current flow conflates them.

### v1.1 design — `docker_hygiene` sub-graph (replaces `docker_prune` entirely)

Replace `docker_prune` with a new sub-graph `docker_hygiene` with three lifecycle phases. The existing `docker_prune` sub-graph, manifest, wrapper scripts, ActionType enum value, and inventory key are all removed in v1.1. Legacy inventories fail loud on load with a migration command (see Migration section below).

#### Phase 1 — Rich assessment (read-only, runs every batch when enabled)

Wrapper `errander-docker-assess-v2` emits structured findings across 5 resource classes:

```
docker_hygiene_begin
class=image_dangling
  id=sha256:abc... size_bytes=1288490188 age_days=23 last_tag=api:v2.1.3-rc classification=cleanup_candidate
  id=sha256:def... size_bytes=838860800 age_days=14 last_tag=<none>            classification=cleanup_candidate
class=image_unused
  id=sha256:ghi... size_bytes=2147483648 age_days=89 last_tag=worker:v1.4.0    classification=cleanup_candidate
class=container_stopped
  id=abc123 name=api_v1_backup       exit_code=0   stopped_age_hours=288 classification=cleanup_candidate
  id=def456 name=worker-3            exit_code=137 stopped_age_hours=2   classification=investigate
class=volume_unreferenced
  name=pgdata_old     size_bytes=12884901888 last_mount_days=47 classification=report_only
  name=redis_cache_v1 size_bytes=4294967296  last_mount_days=31 classification=report_only
class=build_cache
  reclaimable_bytes=8589934592 classification=report_only
docker_hygiene_end
```

**Classification rules (deterministic, Python — no LLM):**

| Resource | `cleanup_candidate` if … | `investigate` if … | `do_not_touch` / `report_only` if … |
|---|---|---|---|
| Dangling image | always | — | — |
| Unused (non-dangling) image | age_days > 30 AND not currently referenced | age_days < 7 | — |
| Stopped container | exit_code == 0 AND stopped_age_hours > 168 | exit_code in (137, 139, 143) OR restarting | exit_code == 0 AND stopped_age_hours < 24 |
| Volume | — | — | always `report_only` in v1.1 |
| Build cache | — | — | always `report_only` in v1.1 |

#### Phase 2 — Object-level approval (dual surface)

Slack message lists exact objects with size/age/classification grouped by resource class. Operator can approve either:

**Surface A — Slack structured reply (best for ≤10 objects, mobile, in-thread):**
Operator replies to the approval message with a structured pattern:
```
@errander approve images 1,3 containers 1 reject volumes all
@errander approve all cleanup_candidate
@errander reject all
```
Bot parses the reply, resolves indices against the assessment snapshot, writes the approval artifact.

**Surface B — Web approval page (best for >10 objects, desk, detail view):**
Slack message includes a signed URL (HMAC-signed, time-limited) to a FastHTML page rendered by the existing Operations Hub. Page shows each object with size/age/classification/last-tag, checkboxes per item, "approve selected" / "reject all" buttons. Submission writes the same approval artifact.

**Shared:** Approval artifact stored in `ai_decisions` table includes the **exact object IDs** approved, the surface used (`slack_reply` | `web_page`), the operator identity (Slack user ID or web session), and a hash of the assessment snapshot at approval time. The poller doesn't care which surface generated the approval — it only checks for the artifact.

#### Phase 3 — Validated execution

Wrapper `errander-docker-remove-v2` accepts an allowlist of `(class, id)` pairs. For each pair:

1. Re-query current state.
2. Verify the object is still in the same classification it had at approval time. If drifted (e.g., image was re-tagged, container was restarted), **skip that object and log a drift event** — do not silently remove.
3. Remove via the appropriate Docker subcommand (`docker rmi <id>`, `docker rm <id>`).
4. Emit one audit row per object: `{vm, class, id, size_bytes, removed_at, approval_id}`.

Per-object audit. No "batch removed N objects" shortcuts.

### Phased rollout (matches SRE recommendation)

- [x] **v1.1** — Implement `docker_hygiene` with: rich assessment (all 5 classes), object-level approval, **execution scope limited to dangling images and exited-0 stopped containers > 7 days old**. Volumes / unused-images / build cache are report-only. *(shipped 2026-05-22, Sessions 1–3)*
- [x] **v1.2** — Extend execution scope to unused (non-dangling) images with `age_days > 30` threshold. Same approval mechanism. *(shipped 2026-05-22)* Fixes: formatter now shows per-finding ✓ (not per-class), parser blocks approval of report_only findings in executable classes, `approve all` defaults to cleanup_candidate only.
- [x] **decisions.py semantic debt** — Replace stale `DOCKER_PRUNE` references with `DOCKER_HYGIENE` in `DEFAULT_PRIORITY` + `_is_action_applicable`; 6 stale test assertions updated across 5 test files. *(2026-05-22)*
- [x] **v1.5** — Volume + build_cache deletion. `volume_deletion_enabled`/`build_cache_deletion_enabled` (both default-off). `volume_last_mount_days_threshold` (default 90). Classify-time gate. Volumes explicit-only approval (cannot use `approve all`). Soft backup_verify context in Slack message. 37 new tests. *(2026-05-22)*
- [ ] **Never v1** — Container start/restart. Confirmed out of scope. Surfacing crashed/unhealthy containers as `investigate` findings is the closest we get; the operator decides what to do, Errander does not touch container lifecycle.

### Migration from `docker_prune` (hard cutover, no grandfathering)

`docker_prune` is removed entirely in v1.1. Existing deployments must run the migration before upgrading.

1. **Loud failure on legacy key.** Config loader detects `docker_prune` in `inventory.yaml` and raises `ConfigError` with a one-line migration command. No silent ignore, no auto-rewrite.
2. **Migration helper.** Extend the existing `--migrate-inventory` flag (which already handles the legacy `docker_command_mode` field) to:
   - Rename `docker_prune` → `docker_hygiene` in every environment's `actions:` block.
   - Drop `command_mode: direct_sudo` entries with a clear message — `direct_sudo` cannot satisfy the per-object validation requirement of the new wrapper. Operators using direct_sudo must install the wrapper before re-enabling.
   - Preserve `enabled: true/false` and `command_mode: wrapper/disabled`.
   - Write `inventory.yaml.migrated` for operator review (same pattern as current migration).
   - Print a list of VMs that need new wrappers installed (`errander-docker-assess-v2`, `errander-docker-remove-v2`).
3. **Audit log.** Historical `docker_prune` action_type rows stay readable. The `ActionType` enum keeps the value but new rows cannot use it (validator in `models/actions.py`).
4. **Wrapper deprecation.** Install scripts no longer deploy `errander-docker-prune-safe` / `errander-docker-prune-aggressive`. Operators should remove them manually post-migration (the migration output lists the paths).

### Drift handling UX

When the remove-wrapper finds approved objects have drifted between approval and execution:

- **Per-object failure rows in audit.** One row per drifted object with `status=drift_skipped` and `drift_reason` (e.g., `image_re_tagged`, `container_restarted`, `volume_now_referenced`).
- **Summary Slack reply.** Posted to the same thread as the approval. Format: "Removed N of M approved objects. K skipped due to drift: [list]. See audit log for detail."
- **No silent proceed.** A drifted object is never removed under the original approval. If the operator still wants it removed, they re-run the assessment and re-approve.

### Manifest

`docker_hygiene` ActionManifest:
- `command_modes = ("disabled", "wrapper")` — no `direct_sudo`. Per-object validation requires the wrapper.
- `required_binaries = ("/usr/bin/docker",)`
- `required_wrappers = ("/usr/local/sbin/errander-docker-assess-v2", "/usr/local/sbin/errander-docker-remove-v2")`
- `risk_tier = "MEDIUM"` (assessment is LOW, removal is MEDIUM — the manifest reflects the worst case)
- `default_enabled = False`

### Session-by-session implementation plan

**Session 1 (estimate: 1 long session) — Add `docker_hygiene` alongside `docker_prune`, no execution, no removal:**
- New ActionType `DOCKER_HYGIENE` + risk tier entry (`errander/models/actions.py`)
- New finding models (`errander/models/docker_hygiene.py`): `DockerResourceClass`, `FindingClassification` enums + `DockerHygieneFinding` + `DockerHygieneAssessment` frozen dataclasses
- `docker_hygiene.py` sub-graph (new file): MANIFEST + validate node + assess node + parser + classification rules + sub-graph builder. No execute node yet. validate → assess → END.
- MANIFEST registration in `errander/agent/subgraphs/__init__.py` (BUILTIN_ACTIONS gets a new entry; docker_prune entry untouched)
- New wrapper installer `scripts/install-docker-wrappers-v2.sh` with `errander-docker-assess-v2` emitting the 5-class output
- Tests in `tests/agent/subgraphs/test_docker_hygiene.py`: parser (all 5 classes), classification rules (every cell in the table), validate node, assess node happy path, idempotency (nothing-to-do)
- **Intentionally deferred to Session 2:** vm_graph.py dispatch wiring. The sub-graph is buildable and testable in isolation but won't be reached by a live batch yet.
- **Intentionally deferred to Session 3:** docker_prune removal, schema.py legacy rejection, migrate.py extension. Keeping these untouched in Session 1 keeps the tree green at every commit.

**Session 2 — Approval surfaces + execution:**
- Approval artifact schema (`ai_decisions` extension: per-object list + snapshot hash + surface field)
- Slack message format with object list + signed web URL
- Slack reply parser (structured commands: `approve images 1,3 containers 1`)
- Web approval page (FastHTML, signed URL verification, checkbox UI, submit handler)
- Remove wrapper (`scripts/errander-docker-remove-v2`) with per-object re-validation
- `docker_hygiene.py` execute node + drift handling
- Per-object audit rows
- Tests: dual approval surfaces, drift handling, audit rows, signed URL verification

**Session 3 (if needed) — Docs + cleanup:**
- SETUP.md rewrite (replace docker_prune section)
- `docs/learning/XX-docker-hygiene.md`
- Remove old `docker_prune.py`, wrappers, tests
- CLAUDE.md updates (action list, scope note, action count stays at 6 since we replaced not added)
- README test count update
- Full pytest run, ruff, mypy

### Files that will change

- `errander/agent/subgraphs/docker_hygiene.py` — **NEW** sub-graph
- `errander/agent/subgraphs/docker_prune.py` — **DELETED**
- `errander/models/manifest.py` — register `docker_hygiene` manifest, remove `docker_prune`
- `errander/models/actions.py` — new `ActionType.DOCKER_HYGIENE`, per-object result model, `DOCKER_PRUNE` marked legacy/read-only
- `errander/config/schema.py` — loader fails on legacy `docker_prune` key
- `errander/config/migrate.py` — migration path
- `scripts/errander-docker-assess-v2` — **NEW** wrapper
- `scripts/errander-docker-remove-v2` — **NEW** wrapper
- `scripts/errander-docker-assess` — **DELETED** post-migration
- `scripts/errander-docker-prune-safe` — **DELETED** post-migration
- `scripts/errander-docker-prune-aggressive` — **DELETED** post-migration
- `errander/safety/audit.py` — per-object audit entry support
- `errander/integrations/slack.py` — reply parser for structured approval commands
- `errander/web/server.py` — approval page route + signed URL verifier
- `errander/safety/approval.py` — dual-surface artifact resolution
- `tests/agent/subgraphs/test_docker_hygiene.py` — **NEW** full suite
- `tests/agent/subgraphs/test_docker_prune.py` — **DELETED**
- `tests/integrations/test_slack_reply_parser.py` — **NEW**
- `tests/web/test_approval_page.py` — **NEW**
- `SETUP.md` — replace docker_prune section
- `RUN.md` — inventory migration note
- `docs/learning/XX-docker-hygiene.md` — **NEW** design + walkthrough
- `CLAUDE.md` — replace "Docker prune" with "Docker hygiene" in action list, update risk-tier table

---

## Provider layer — Operations Hub backed by real stores (2026-05-21, COMPLETED)

- [x] `errander/web/providers.py` (NEW) — DataProvider Protocol, FixtureProvider (demo/CI default), LiveProvider (real stores, `ERRANDER_UI_DATA_MODE=live`), singleton + `reset_provider_for_testing()`.
- [x] `errander/web/server.py` — all 14 data constants replaced with `get_provider().get_*()` calls; APPROVAL_COUNT inline; `_mode_banner_html()` reads provider state; `_on_startup` initialises LiveProvider + schedules refresh.
- [x] Three crash fixes for live mode with empty data: `next_runs` list guard, `max()` default=0 for empty nodes, `if not probe:` placeholder branch.
- [x] `tests/ui/test_web_providers.py` (NEW) — 43 tests: AST contract, FixtureProvider (19 tests), LiveProvider (10 tests), page renders in both modes (8 tests), env var selection (3 tests), mode banner (3 tests).
- [x] 43/43 provider tests passing; 168/168 UI tests passing; 2163 total.

### SRE QA: evidence gating — fixture data leak in live mode (2026-05-21, COMPLETED)

SRE QA verdict: live mode still served fixture operational data (`VM_EVIDENCE` lock holders, `BATCH_EVIDENCE` KPIs, `APPROVAL_EVIDENCE`, `AUDIT_EVIDENCE`, static April 2026 chart, static settings/admin/health values).

- [x] Added `_NULL_AUDIT_EV` sentinel + `_ev_vm()`, `_ev_batch()`, `_ev_approval()`, `_ev_audit()` gate helpers — return `{}` / null sentinel in live mode, fixture lookup in fixture mode.
- [x] `_operator_queue()` — VM_EVIDENCE lock section gated behind `if get_provider().data_mode() == "FIXTURE":`.
- [x] `page_approvals()` — `APPROVAL_EVIDENCE.get(a["id"], {})` → `_ev_approval(a["id"])`.
- [x] `page_audit()` — `audit_evidence_for(i)` → `_ev_audit(i)`.
- [x] `page_vm()` — all 3 `VM_EVIDENCE.get(hostname, {})` → `_ev_vm(hostname)`.
- [x] Fleet inventory table — `VM_EVIDENCE.get(vm["hostname"], {})` → `_ev_vm(vm["hostname"])`; default window `"—"` in live mode.
- [x] `page_batches()` — live mode computes KPIs from `get_provider().get_batches()`; chart rendered only in fixture mode; `BATCH_EVIDENCE` → `_ev_batch()`.
- [x] `page_settings()` — `_live_settings_sections()` reads env vars in live mode; fixture sections only in fixture mode.
- [x] `page_admin()` — agent controls read from `get_provider().get_agent_status()` + `get_provider().get_active_batch()`; `_live_health_checks()` reads env vars in live mode.
- [x] Agent page SSH pool count: `"11 hosts"` → `f"{len(get_provider().get_vms())} host(s)"`.
- [x] 168/168 UI tests passing after all 10 edits.

### SRE QA round 2: remaining fixture leaks (2026-05-21, COMPLETED)

- [x] `page_fleet()` — gated `"last batch 02:00 UTC"`, `"Slack approval expires < 30 min"`, `"Completed 2026-04-23 02:14 UTC"`, `"data as of 2026-04-23 02:14 UTC"` behind `_is_fixture`.
- [x] `handle_fleet()` topnav — `"Last batch: 2026-04-23 02:00 UTC"` chip only rendered in fixture mode.
- [x] `page_approvals()` — `"RESOLVED TODAY — 14 actions approved or rejected"` banner gated.
- [x] `page_vm()` — `"Next: 2026-04-24 02:00 UTC"`, `34`/`8`/`3` KPIs, hardcoded `prod-0423-0200` deep-link batch ID, `/keys/{hostname}.pem` SSH FP, `"Tue/Thu 02:00–04:00 UTC"` window all gated/replaced with `"—"` in live mode.
- [x] Settings env var reference table: `"Qwen3-8B-AWQ"` example replaced with generic text.
- [x] Metrics API: unknown hostname returns 404 (not 200 with empty arrays).
- [x] 9 new regression tests in `tests/ui/test_web_providers.py` — 8 parametrized page renders + VM not-found check — assert no fixture string appears in live mode.
- [x] 177/177 UI tests passing.

### SRE QA round 3: P2 inventory/admin static facts (2026-05-21, COMPLETED)

- [x] Inventory OS subtitle: `"Ubuntu · RHEL · Debian"` → computed from actual VMs.
- [x] Inventory "Reachable" timestamp: `"last verified 02:14 UTC"` → `get_provider().data_freshness()` in live mode.
- [x] `_inventory_env_breakdown()` restartable units: `_ENV_RESTARTABLE_UNITS` gated — live mode shows "None configured" per actual env.
- [x] `page_settings()` restartable units table: same gate — live mode iterates actual envs with empty units.
- [x] Admin "Last checked" timestamp: `"2026-05-13 03:00:12 UTC"` → "Not yet checked" in live mode.
- [x] `_FIXTURE_ONLY_STRINGS` extended with `"2026-05-13"`, `"last verified 02:14"`, `"Ubuntu · RHEL"`, `"nginx"`, `"gunicorn"`.
- [x] 177/177 UI tests passing.

---

## P0 regression fix — f-string JS brace escape (2026-05-21, COMPLETED)

- [x] `errander/web/server.py` — `_batchFilter` JS braces escaped (`{{`/`}}`). Module now compiles and imports cleanly.
- [x] `tests/ui/test_web_server_smoke.py` (NEW) — 14 smoke tests: `ast.parse` compile check, import check, every `page_*` renders without exception, brace-escaping regression guards for `_batchFilter` and `_invFilter`.
- [x] 2120 tests passing.

---

## Deferred — revisit when ready

These are intentionally parked. Each has a clear trigger condition for when to pick it back up.

### Project C — Runbook & Postmortem Memory
**Trigger:** user authors at least 3–5 `./runbooks/*.md` files.
**What's needed:** markdown files in `./runbooks/` with YAML frontmatter (title, tags, applies_to, severity) and body text. Even rough operational notes count — "what to check before restarting nginx", "common patching failure patterns on RHEL".
**What gets built:** `RunbookStore` loader, keyword+tag retrieval in `OperatorAssistant`, `errander runbooks list/show/reload` CLI, example runbooks in `example/runbooks/`. Design is fully specced in `tasks/post-review-implementation-plan.md §6`.

### Project D2–D4 — AI Eval / Replay Harness
**Trigger:** `ai_decisions` table has real rows (i.e., the agent has run live batches against a real LLM endpoint).
**What's needed:** run `SELECT COUNT(*) FROM ai_decisions` — if > 0 and `prompt_full` is populated, D2 is useful. The D1 capture infrastructure is already wired; rows accumulate automatically when the agent runs.
**What gets built:** `errander ai-replay` CLI, assertion functions (policy compliance, risk-tier stability, fallback parity, citation presence, never-automate check), daily scheduled replay job, regression report. Design in `tasks/post-review-implementation-plan.md §7`.

### Project E — HITL Interrupt/Resume
**Trigger:** any of — operators report crash-lost approvals; approval wait p95 routinely > 30 min (check with `errander --measure-durability`); need to run agent in a stateless/restart-tolerant env.
**What's needed:** Project A (checkpointing) is already in place. Also needs a durable `ApprovalManager` (currently in-memory). Design sketch in `tasks/post-review-implementation-plan.md §8`.

---

## Project B3 — `errander vm-facts` CLI (2026-05-20, COMPLETED)

- [x] `errander/commands/vm_facts.py` — `cmd_vm_facts` with three output sections: outcomes, reboot pattern, rejection facts. `_fmt_rate()` with ✓/~/✗ visual indicator. Cross-fleet mode when vm_id omitted.
- [x] `errander/main.py` — `--vm-facts <vm_id>` and `--vm-facts-action <type>` args + dispatch block.
- [x] `tests/commands/test_vm_facts.py` — 16 tests covering: no-args error, single-VM outcomes, action filter, cross-fleet, no-data messages, reboot pattern, section visibility, rejection facts, old-event exclusion, ✓/✗ indicators.
- [x] `RUN.md` — `## VM operational facts` section with example output + CLI flags table.
- [x] 2106 tests passing, ruff clean, mypy clean.

---

## QA/SRE UI bug fixes (2026-05-20, COMPLETED)

- [x] **Fix 1 — `load_inventory()` startup crash**: `_on_startup` called with no args. Fixed: `ERRANDER_INVENTORY_PATH` env var + `list(load_inventory(_inv_path))`. WAL pragmas added for SQLite disk I/O hardening.
- [x] **Fix 2 — SQLite disk I/O error**: `aiosqlite.connect(timeout=30)` + `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=10000`.
- [x] **Fix 3 — Mobile 390px overflow**: `html, body { max-width: 100vw; overflow-x: hidden }` root rule + ~40 new `@media (max-width:768px)` rules.
- [x] **Fix 4 — Placeholder controls**: Inventory FILTER → `_invFilter()` JS; batch rows → `batch-row`+`data-status`; approval cards → `approval-card`+data attrs; Audit EXPORT CSV → `_exportAudit('csv')`; Agent RUN BATCH NOW, Inventory EXPORT, deferred queue View Plan → disabled with CLI/v2 tooltips.
- [x] 2090 tests passing, 0 regressions.

---

## Project A — LangGraph Workflow Durability A2–A6 (2026-05-20, COMPLETED)

- [x] **A2 — BatchStore + batches table**: `errander/models/batches.py` (BatchStatus StrEnum + BatchRecord), `errander/safety/batches.py` (BatchStore with INSERT OR IGNORE + WHERE status='running' guard), migration #5. `init_batch_node` + `generate_report_node` wired. 16 tests.
- [x] **A3 — State serialization tests**: `tests/agent/test_state_serialization.py` — all 6 GraphState TypedDicts round-tripped through JsonPlusSerializer (dumps_typed → loads_typed). 17 tests. Identified patch_output as >4 KB blob candidate.
- [x] **A4 — ArtifactStore + artifacts table**: `errander/safety/artifacts.py` (store/retrieve/retrieve_by_kind/purge_before), migration #6. `AuditStore.make_artifact_store()` factory. 13 tests.
- [x] **A5 — AsyncSqliteSaver + AgentLease**: `errander/safety/agent_lease.py` (acquire/heartbeat/release, TTL=90 s), migration #7. `AsyncSqliteSaver` wired per batch run in `main.py` with graceful fallback. `AgentLease` in `async_main()` with finally-release. 14 lease tests.
- [x] **A6 — `errander runs` CLI + SAFE_RESUME_NODES**: `errander/commands/runs.py` (list/inspect/resume sub-commands, checkpoint probing). `SAFE_RESUME_NODES` frozenset in `graph.py`. `OPERATOR_FORCE_RESUME` EventType. CLI wired into `main.py`. 0 new tests (covered by integration).
- [x] 2090 tests total, all passing. Ruff clean on all new/modified files.

---

## SRE UX punch list — P0 wave (2026-05-20, COMPLETED)

External SRE review (full text in conversation log) graded enterprise trust/audit 3/10, decision support 4/10, operator safety 3/10. P0 wave landed: foundation + 3 highest-risk pages. Visual verification via screenshots confirmed every required element renders.

### Foundation (touches all pages)
- [x] **`errander/web/evidence.py`** — new additive enrichment module (UI_MODE, APPROVAL_EVIDENCE, AUDIT_EVIDENCE, VM_EVIDENCE, BATCH_EVIDENCE) so we don't risk breaking existing data.py dicts. Shape mirrors the real audit DB / immutable execution artifact so this module is the integration seam when wired to real data.
- [x] **Mode banner** in `layout()` via `_mode_banner_html()` — every page renders `DEMO DATA | PROD | DRY RUN · static fixture · backend · build` above breadcrumb. Color escalates for LIVE / LIVE+PROD.
- [x] **CSS additions** — mode-banner, evidence-grid, layer-section (b/policy/a), countdown-big, deeplink-chip, confirm-modal, destructive-hdr, layer-partition. ~250 lines appended to CSS string.

### Per-page (P0 — safety-critical)
- [x] **Approvals** (`page_approvals`, server.py) — 13-cell evidence grid (plan_id, plan_hash, batch_id, action_id, requester, approver_role, artifact age/expiry, drift, rollback_ready, vm_lock, window, idempotency). Per-action table (cur → target package versions w/CVE column, or unit state for service restart). Three-section Layer A/B/Policy split with canonical labels. Large color-coded countdown timer (15min/5min thresholds). Deep-link chips: Slack thread, audit slice, batch, VM. Typed-confirm modal — must retype batch_id exactly + 20-char reason before Confirm enables. Banner clarifies "UI is not a self-approval surface".
- [x] **Admin** (`page_admin`, server.py) — "DESTRUCTIVE — AUDITED" red banner. Danger Zone reorganized: each destructive action has ROLE, AUDIT EVENT preview, description. Typed-confirm modal requires exact phrase ("FLUSH DEFERRED QUEUE", "CLEAR ALL LOCKS", "FORCE ROLLBACK") + 20-char reason. TRUNCATE AUDIT LOG is BLOCKED in UI (DBA-only path).
- [x] **Agent** (`page_agent`, server.py) — Layer A (LLM endpoint, model, latency p50/p95, fallback, tool/MCP) vs Layer B (scheduler, batches, SSH pool, audit DB, Slack poll) visually partitioned with vertical "⚠ SAFETY BOUNDARY · NO LLM PAST THIS LINE ⚠" divider. AI Safety Invariant text beneath. Responsive: collapses to horizontal divider on <900px.

### Verification
- [x] `python -c "import ast; ast.parse(...)"` — both server.py and evidence.py parse cleanly
- [x] Direct page-render test — all 10 page_* functions execute without exception
- [x] Bounced live server (PID 31748 → fresh process), curl + Chrome screenshots confirm rendered HTML contains every required marker
- [x] Live modal interaction tested: clicked FLUSH DEFERRED QUEUE → confirm modal opened, audit event preview visible, exact-phrase + reason fields present, Confirm disabled until both validated

### P1 wave (2026-05-20, COMPLETED)
- [x] **Fleet Dashboard** — `_operator_queue()` aggregates pending approvals (w/countdown), HIGH RISK actions, failed/partial batches, failed VMs, degraded VMs, locked VMs, next window, active batch. Priority-ordered CRITICAL/HIGH/MED/INFO. VM cards demoted to "Fleet Inventory" below KPIs.
- [x] **Audit Log** — every row surfaces event_id + action_id + approver. Click-to-expand shows: Event ID, Action ID, Plan Hash, Approver, Approval Source, Before/After (color-coded), Command Executed, Stdout, Stderr (red on failure), Rollback Status, deep-link chips to batch/VM/approvals. Filters by env/batch/VM/action/status (client-side, data-* attributes). Export CSV + JSON honor current filter.

### P1 follow-up (deferred — important, not safety-critical)
- [x] **VM Detail — Metricbeat-style trends (user request 2026-05-20)** — Resource Trends card with 24h/7d toggle. CPU + MEM sparklines with dashed 75%/90% threshold lines, min/avg/max stats, endpoint dot. Disk partition rows show mini 80×18 sparklines with ↑/↓ N% 24h delta. VM_EVIDENCE expanded with cpu_history/mem_history/disk_history for all 11 VMs. Last history point pinned to live vm["cpu"]/vm["mem"] for coherence. Verified: prod-api-01 MEM climb, prod-db-01 OOM pattern, 7d toggle working.
- [x] **Real metrics collection + live API (2026-05-20)** — `errander/observability/vm_metrics.py` (new): POSIX SSH probe (vmstat+/proc/meminfo+df, one round-trip), asyncssh 8s hard timeout, collect_all asyncio.gather, cleanup_old_metrics 8-day retention, query_metrics 4-window bucketed AVG. Migration #4: vm_metrics table. server.py: /api/vm/{hostname}/metrics JSON endpoint, _on_startup (DB open + migrations + APScheduler 60s collect + 1h cleanup), _on_cleanup, handle_vm queries DB for all 4 windows, _auth_middleware JSON 401 for /api/ paths. 2027 tests passing.
- [x] **Node Exporter flag + configure.sh (2026-05-20)** — MetricsCollector rewritten: flag-driven discover() reads `target.node_exporter` from inventory (true→scrape :9100, false→SSH probe, true+unreachable→warn+SSH fallback). Prometheus text parser + stateful CPU delta. Persistent SSH connections. `node_exporter: bool` on VMTarget; `node_exporter: bool|None` on TargetSchema (host override); `node_exporter: bool` on EnvironmentSchema (default). inventory.py inheritance. configure.py: interactive SSH check → NE check → install prompt → SSH install → YAML write. configure.sh: thin bash wrapper. 2027 tests, all passing.
- [x] **VM Detail — VM_EVIDENCE + deep links (2026-05-20)** — lock alert callout (red, lock holder + clear path), noop badge (NOOP · up to date), last_patched field, ssh_key_fp fingerprint, window from ev. Deep-link chip strip: Last Batch, Approval, Audit Slice, Patch History. callout-red CSS added.
- [x] **Batches — BATCH_EVIDENCE + click-to-expand (2026-05-20)** — rows toggle expand showing plan_hash, approver, approval_source, outcome (ok/failed/partial/rolled_back counts). JS _toggleBatchRow + fragment auto-expand (URL hash → scroll + expand). Deep links: Approval, Audit Slice.
- [x] **Glossary verify (2026-05-20)** — confirmed: Layer A/B, Risk Tier, Rollback, Disk Cleanup (/tmp whitelist detail), AI Safety Invariant all present. No changes needed.
- [x] **Inventory + Settings (2026-05-20)** — Inventory: SSH Key FP column from VM_EVIDENCE (fingerprint, not path), maintenance window per VM from VM_EVIDENCE, restartable units in env breakdown (nginx/gunicorn/redis-server per env). Settings: Restartable Units Allowlist section (env → ENABLED/DISABLED → unit chips), links to /inventory.
- [x] **Mobile responsive sweep (2026-05-20)** — @media (max-width:768px): sidebar hidden, shell full-width, table-card overflow-x:auto, filter-bar flex-wrap, section-hdr column, kpi-grid 2-col, settings/admin/inv-kpi grids responsive.

### Acceptance (P0 wave)
✅ A senior SRE looking at the Approvals page can now answer: *Plan hash? Batch ID? Action ID? Exact commands? cur→target versions? Drift? Artifact age/expiry? Rollback ready? Approver role? VM lock? Window? Idempotency? Deadline?* — all visible on one card. AI text labeled LAYER A · ADVISORY with explicit disclaimer that the approval authority is the operator's Slack reaction, not the LLM.

✅ Admin destructive actions cannot be triggered by accident — typed-phrase confirm + 20-char reason + audit-event preview gate every one. TRUNCATE AUDIT LOG is blocked in UI entirely.

✅ Agent page makes the Layer A/B safety boundary visible at a glance — operators and reviewers can see that no LLM lives in the execution path.

---

## P0-1 immutable execution artifact — final closure (2026-05-19, COMPLETED)

- [x] verify_node: query all approved package names (not just pending_updates) when approved_packages present
- [x] Tests: partial-update scenario passes, query-scope assertion test
- [x] 1991 tests passing, 0 failures

## P0-1 immutable execution artifact — second closure (2026-05-19, COMPLETED)

- [x] assess_node: approved-artifact path uses list_installed_versions, not list_upgradable
- [x] assess_node: already-at-target versions → nothing_to_do without touching repo
- [x] verify_node: exact version match against approved targets; fail on any mismatch or missing pkg
- [x] load_deferred_artifact_node: missing/invalid approved_at fails closed
- [x] Tests: assess approved path (3 new), verify exact match (3 new), deferred timestamp (2 updated)
- [x] 1989 tests passing, 0 failures

---

## P0-1 true immutable execution artifact (2026-05-19, COMPLETED)

- [x] Add `install_pinned()` + `simulate_install_pinned()` to `PackageManager`, `AptManager`, `DnfManager`
- [x] `execute_node` uses `install_pinned()` in live mode; fails closed without approved_packages or missing versions
- [x] `_run_patching()` extracts `approved_packages` from enriched plan preview and injects into `PatchingGraphState`
- [x] `load_deferred_artifact_node` checks artifact age via `preloaded_approved_at`; fails closed > 168h; warns > 24h
- [x] `run_env_batch` passes `preloaded_approved_at` from `record.approved_at`
- [x] Tests updated: chaos, patching execute_node, commands install_pinned, deferred replay age check
- [x] Docs: STATUS.md, 37-immutable-plan-artifact.md, todo.md, lessons.md, command-log.md, README.md
- [x] 1982 tests passing, 0 failures

---

## Glossary overhaul — current with v1 codebase (2026-05-19, COMPLETED)

- [x] Added Backup Verify action term (Low risk, read-only)
- [x] Added Service Restart action term (High risk, operator-triggered, allowlist required)
- [x] Added Layer A term (Operator Assistant, read-only, --ask + UI)
- [x] Added Layer B term (Safe Execution, deterministic Python, no LLM in live path)
- [x] Renamed "vLLM" → "LLM Endpoint" — leads with any OpenAI-compatible endpoint
- [x] Fixed Plan Enrichment workflow badge: P0-1 → PRE-APPROVAL
- [x] Fixed Plan Enrichment node sublabel: removed cryptic P0-1 label
- [x] Updated Action Exec. popup + sublabel to cover all 6 v1 sub-graphs
- [x] Added risk tier labels to all ACTIONS definitions

---

## Login screen + Godmode E2E sweep + 5 fixes (2026-05-19, COMPLETED)

- [x] Login page — dark indigo full-screen card, HMAC-signed 8h session cookie, no new deps
- [x] Auth middleware — protects all routes, /login and /logout public
- [x] Sign out link in sidebar footer
- [x] Agent page — removed duplicate Admin Controls/RUN BATCH NOW buttons (were in both topnav and section-hdr)
- [x] Fleet topnav — `▶ RUN BATCH NOW` symbol consistency (was missing ▶)
- [x] Settings page — added Environment Variables Reference table (was sparse)
- [x] VM detail page — added "Fleet Siblings" section linking to other VMs in same env (was sparse)
- [x] Inventory page — added Environment Breakdown summary cards (PROD/STAGING/DEV counts)
- [x] 111 UI tests passing, 0 regressions

---

## UI overhaul — information density + actionability + /agent page (2026-05-19, COMPLETED)

- [x] VM cards: CPU / MEM / DISK tri-bars, pending patches chip, uptime, IP, last action type
- [x] Fleet dashboard: "Needs Attention" callout for warning/failed/pending VMs with reasons and links
- [x] Approval cards: VM health panel (CPU, MEM, DISK, load), trigger line, reject consequences
- [x] Audit log: `detail` field shown inline under each action (was hidden behind broken "Details →")
- [x] Audit event detail strings enriched (packages, logs, disk before→after)
- [x] Batch history: error summary + failed VM links inline in errors column
- [x] VM detail: pending patches callout, CPU/memory in identity card, 4-tile KPI row
- [x] /agent page: agent status strip, LangGraph execution trace, per-VM stage matrix, LLM decisions, scheduler timeline, daily probe history, deferred queue
- [x] handle_agent route handler + /agent route registered in create_app()
- [x] 111 UI tests passing, 0 regressions

---

## Fix — audit log detail strings for patching / log_rotation / disk_cleanup (2026-05-18, COMPLETED)

- [x] `patching.py` — added `changed_packages: dict[str, str]` to `PatchingGraphState`; `verify_patch_node` now returns `changed_packages` (old→new per package that actually changed)
- [x] `vm_graph.py` patching detail — uses `changed_packages` to show `"installed: N package(s)"` or `"no package versions changed"` instead of pre-execution counts
- [x] `vm_graph.py` log_rotation detail — distinguishes `"logrotate"` key (system logrotate ran) from per-file keys; shows `"rotated: N file(s) via logrotate"` or `"rotated: N file(s) manually"`
- [x] `vm_graph.py` disk_cleanup detail — shows `"cleaned: apt-cache, journal, /tmp"` + `"/ : 45% → 38%"` disk usage change
- [x] Fixed 5 stale Playwright test assertions left from UI redesign (Approvals→Approval Queue, Dashboard→Fleet Dashboard, Batches→Batch History)
- [x] 1969 tests passing

---

## Bug fix — vm_plans duplicate due to append-only reducer + enrich_plan_node (2026-05-18, COMPLETED)

- [x] Diagnosed "2 VMs planned" for 1 physical VM — `enrich_plan_node` returned `{"vm_plans": enriched}` which the append-only LangGraph reducer doubled to `[raw, enriched]`
- [x] Added `enriched_vm_plans` field to `BatchGraphState` (no reducer = last-write-wins)
- [x] `enrich_plan_node` now writes to `enriched_vm_plans` instead of `vm_plans`
- [x] Added `_effective_vm_plans(state)` helper — returns `enriched_vm_plans` if set, else `vm_plans`
- [x] All post-enrich consumers updated: `generate_plan_artifact_node`, approval gate, `verify_plan_hash_node`, wave dispatch, `load_deferred_plan_node`
- [x] 33 graph tests pass, no regressions

---

## UI redesign — Sovereign Architect design system (2026-05-18, COMPLETED)

- [x] Replace dark theme CSS with Stitch "Sovereign Architect" light design system
- [x] Font swap: IBM Plex → Space Grotesk (headlines) + Inter (UI) + JetBrains Mono (system data)
- [x] Sidebar redesigned: deep indigo `#1e1b4b`, accent bar on active item, gradient badge
- [x] Cards: 4px left accent bar instead of top border; ambient shadow; no 1px lines
- [x] Buttons: gradient (primary→secondary 135°) for primary actions; pill badges
- [x] Tables: alternating surface tints instead of divider lines
- [x] Approval cards: gradient left bar, surface-lowest background, shadow
- [x] Test LLM button wired to `/ui/settings/test-llm` (was endpoint-only, no UI trigger)
- [x] All 8 POST buttons verified wired to correct routes with CSRF protection
- [x] Diagnosed `ERRANDER_UI_BIND=0.0.0.0` requirement for public IP access
- [x] Diagnosed stale lock path: `.errander-locks/` (relative to CWD), not `/var/lib/errander/locks/`

---

## OSS readiness review — SETUP.md/RUN.md polish + --check-targets fix (2026-05-18, COMPLETED)

- [x] `RUN.md` — added 9 missing CLI flags, corrected `--migrate-inventory` description (writes `.migrated`, not stdout diff)
- [x] `SETUP.md` — wrapper install flow (scp from controller, run as admin on target), ELK API key creation command, Step 6 inline verification sequence, sequencing fixes for Optional sections
- [x] `errander/main.py` — `--bootstrap-known-hosts` auto-appends `ERRANDER_SSH_KNOWN_HOSTS` to `.env`; `run_check_targets` + `run_probe_now` load settings and pass `known_hosts_path`/`strict_host_keys` to `SSHConnectionManager`
- [x] `errander/execution/target_validation.py` — import `PRIVILEGED_PATHS`; `_SUDO_REQUIRED_BINARIES` frozenset; sudo check skips non-privileged binaries (find, stat, /bin/systemctl, /bin/journalctl)
- [x] 1969 tests passing, no regressions

---

## Phase D1 — Full prompt + context capture in ai_decisions (2026-05-18, COMPLETED)

- [x] `errander/safety/ai_audit.py` — 3 new columns (`prompt_full`, `context_snapshot`, `model_params`) in `_CREATE_TABLE_SQL`, `AIDecision` dataclass, `_INSERT_SQL`, `_SELECT_SQL`, `_row_to_decision()`; `initialize()` adds columns idempotently via ALTER TABLE
- [x] `errander/agent/decisions.py` — `json`, `asdict`, `_as_float()` helper; success + fallback call sites pass new fields; no_llm path passes `context_snapshot`
- [x] `tests/safety/test_ai_audit.py` — 16 tests: schema migration on old table, idempotent ALTER, round-trip log/get for all 3 fields, filters, hash_prompt
- [x] 1969 tests passing, ruff clean, mypy clean

---

## Phase A1 + B1/B2 — Durability measurement + VMFactsStore (2026-05-18, COMPLETED)

### Phase A1 (measurement)
- [x] `errander/observability/metrics.py` — AGENT_STARTS_TOTAL, BATCHES_INTERRUPTED_TOTAL counters
- [x] `errander/observability/startup_scan.py` — scan_orphan_batches (7-day window, warns per orphan)
- [x] `errander/observability/durability.py` — DurabilityReport dataclass + compute + print
- [x] `errander/main.py` — --measure-durability / --window-days CLI + startup instrumentation
- [x] 8 tests for startup_scan, 15 for durability, all pass
- [x] `--measure-durability` output: 0 batches in window (clean DB), BATCHES_INTERRUPTED_TOTAL=0

### Phase B1 (VMFactsStore)
- [x] `errander/safety/vm_facts.py` — ActionOutcomeFact, VMRebootPatternFact, ActionRejectionFact + VMFactsStore
- [x] 21 tests covering success rate, sample cap, reboot pattern, rejection window

### Phase B2 (OperatorAssistant fact integration)
- [x] `errander/models/analysis.py` — 3 new FleetContext fields (TYPE_CHECKING guarded imports)
- [x] `errander/agent/operator_assistant.py` — vm_facts_store param, _build_context queries, _format_prompt section, _fallback_response flags
- [x] 13 tests for context building + prompt formatting + fallback
- [x] 1953 tests passing, ruff clean, mypy clean

---

## v1-action-opt-in plan (2026-05-17, in progress)

### Commit 1.1 — manifest model, registry, nested actions schema
- [x] `errander/models/manifest.py` — `ActionManifest` frozen dataclass
- [x] `MANIFEST` constants in all 5 subgraph modules
- [x] `errander/agent/subgraphs/__init__.py` — `BUILTIN_ACTIONS` registry
- [x] `errander/config/schema.py` — `ConfigError`, `ActionConfig`, legacy rejection, defaults + contradiction validators
- [x] `errander/main.py` — `run_check_targets` + `run_env_batch` use `env.actions.get("docker_prune")`
- [x] `errander/agent/graph.py` — reads `docker_command_mode` from batch state, not per-target dict
- [x] `example/inventory.yaml` — converted to nested `actions:` block
- [x] `tests/test_main.py` — updated 2 TestRunCheckTargets inline YAML to nested format
- [x] 3 new test files: `test_manifest.py`, `test_registry.py`, `test_schema_actions.py` (35 new tests)
- [x] 1742 tests passing, ruff clean (my files), mypy no new errors
- [ ] commit 1.1

### Commit 1.2 — migration helper (`--migrate-inventory`)
- [x] `errander/config/migrate.py` — `migrate_inventory()` + full synthesis + diff output
- [x] `--migrate-inventory` CLI flag in `main.py` + `_run_migrate_inventory()`
- [x] `tests/config/test_migrate.py` — 28 tests covering all migration cases
- [x] `tests/test_main.py` — 4 new tests for arg parsing + CLI exits
- [x] 1764 tests passing, ruff clean, mypy no new errors
- [ ] commit 1.2

### Commit 1.3 — registry-driven `--check-targets` + SETUP.md + CLAUDE.md scope note
- [x] `TARGET_PREFLIGHT_FAILED` added to `errander/models/events.py`
- [x] `BatchStatus` StrEnum added to `errander/models/reports.py`
- [x] `sudo_preflight_node` uses `BUILTIN_ACTIONS` for wrapper list; emits `TARGET_PREFLIGHT_FAILED` for missing wrappers
- [x] `target_validation.check_target()` uses manifest-derived wrapper list (not hardcoded)
- [x] `SETUP.md` Docker section → `## Optional: Docker cleanup` + skip callout
- [x] `CLAUDE.md` `## v1 Scope` subsection added
- [x] `README.md` capability matrix (5 actions, enabled/disabled/opt-in/risk tier)
- [x] 3 new tests in `test_sudo_preflight.py`, 3 in `test_vm_graph.py`, 2 in `test_main.py`
- [x] 1772 tests passing, ruff clean, mypy no new errors
- [ ] commit 1.3

### Commit 2.1 — Docker wrapper install script
- [x] `scripts/install-docker-wrappers.sh` — idempotent root install script (3 wrappers + sudoers)
- [x] SETUP.md Docker section collapse — ~90-line heredoc → 4-line scp+ssh+verify block
- [x] `tests/scripts/test_install_docker_wrappers.py` — 18 drift tests (wrapper parse, flags, prune commands)
- [x] 1790 tests passing, ruff clean, mypy clean
- [x] commit 2.1

### Commit S.1 — service_restart sub-graph + manifest + events
- [x] `errander/models/service_restart.py` — `RestartContext` dataclass + `ServiceRestartState` TypedDict
- [x] `errander/agent/subgraphs/service_restart.py` — full sub-graph (validate→snapshot→execute→verify) + MANIFEST + `parse_restart_output()`
- [x] `errander/agent/subgraphs/__init__.py` — added `service_restart` to BUILTIN_ACTIONS (now 6 entries)
- [x] `errander/models/events.py` — 7 new `SERVICE_RESTART_*` event types
- [x] `errander/models/actions.py` — `ActionType.SERVICE_RESTART` + `ACTION_RISK_TIERS[SERVICE_RESTART] = HIGH`
- [x] `tests/agent/subgraphs/test_service_restart.py` — 18 tests (validate/snapshot/execute/verify)
- [x] `tests/agent/subgraphs/test_service_restart_manifest.py` — 15 tests (manifest fields + registry)
- [x] `tests/agent/subgraphs/test_service_restart_parser.py` — 13 tests (full output, snapshot, malformed)
- [x] `tests/agent/subgraphs/test_registry.py` — updated count 5→6
- [x] 1836 tests passing, ruff clean, mypy clean
- [x] commit S.1

### Commit S.2 — systemctl-restart wrapper install script + drift test
- [x] `scripts/install-systemctl-restart-wrapper.sh` — idempotent root install (wrapper + allowlist + sudoers)
- [x] `tests/scripts/test_install_systemctl_restart_wrapper.py` — 23 drift tests
- [x] 1859 tests passing, ruff clean
- [x] commit S.2

### Commit S.3 — CLI `--restart-service` + schema validation + allowlist drift + approval test
- [x] `errander/config/schema.py` — `ActionConfig.restartable_units: list[str] = []` + ConfigError when service_restart enabled with empty list
- [x] `errander/main.py` — `--restart-service`, `--unit`, `--vm`, `--vms` flags + `run_restart_service()` + allowlist drift in `run_check_targets`
- [x] `tests/config/test_schema_actions.py` — 6 new `TestServiceRestartValidation` tests
- [x] `tests/test_main.py` — 11 new tests (arg parsing, dry-run happy path, rejection cases, allowlist drift)
- [x] `tests/agent/test_approval.py` — 7 new approval guarantee tests (HIGH tier, HITL invariant)
- [x] 1885 tests passing, ruff clean, mypy clean
- [x] commit S.3

### Commit S.4 — SETUP.md + CLAUDE.md + README + learning doc
- [x] `SETUP.md` — add `## Optional: Service restart` section
- [x] `CLAUDE.md` — update v1 scope (6 actions), risk-tier table (service_restart HIGH), operator-triggered note
- [x] `README.md` — capability matrix: service_restart ✅ opt-in + CLI example
- [x] `example/inventory.yaml` — add service_restart block (disabled default + commented units)
- [x] `docs/learning/40-service-restart-module.md` — design walkthrough
- [x] commit S.4

### RUN.md catch-up (missed in 1.2 and S.3)
- [x] `RUN.md` — `--migrate-inventory` section + `--restart-service` section + CLI flags + runbook entry
- [x] commit + push

### SRE audit fix Round 1 — enabled_actions enforcement (2026-05-17)
- [x] Bug 1 (High): `enabled_actions` built from `env_schema.actions` in `run_env_batch`, added to `BatchGraphState`, passed to `prioritize_actions` in `plan_vm_node`
- [x] Bug 2 (Medium): `check_target` now takes `enabled_actions` kwarg; per-action binary mapping replaces fixed `_binaries_for_os`; `run_check_targets` and `validate_targets_node` pass enabled list
- [x] `docker_mode` defaults to `"disabled"` when `docker_prune.enabled: false` in both call sites
- [x] `tests/agent/test_enabled_actions_planning.py` — 6 new tests (planning enforcement + plan_vm_node wire-up)
- [x] `tests/execution/test_target_validation.py` — 2 new tests (per-action binary filtering, wrapper skip)
- [x] 1893 tests, ruff clean, mypy clean
- [x] commit + push

### SRE audit fix Round 3 — service_restart wrapper in check_target (2026-05-17)
- [x] Generic wrapper probe loop added to `check_target()` (step 3): manifest-driven, skips docker_prune, skips disabled actions, probes `sudo -n {wrapper} --check`
- [x] Docker block renumbered step 4, unchanged
- [x] 3 new tests: wrapper probed when enabled, skipped when disabled, fail → blocked
- [x] 1901 tests, ruff clean, mypy clean
- [x] commit + push

### SRE audit fix Round 2 — route_plan_vms Send payload + manifest-derived binaries (2026-05-17)
- [x] Blocker: `route_plan_vms()` extracted to module level; `enabled_actions` passed in Send payload when present; key omitted (not `[]`) when absent — preserves DEFAULT_PRIORITY fallback
- [x] Medium: `_binaries_for_enabled_actions()` now derives from `BUILTIN_ACTIONS` manifests (not hand-written table)
- [x] `apt-mark` added to patching MANIFEST `required_binaries`
- [x] `TestRoutePlanVms` — 5 new tests verifying Send payload behavior
- [x] 1898 tests, ruff clean, mypy clean
- [x] commit + push

---

## Glossary UI in production metrics server (2026-05-17, completed)

- [x] Identified root cause: production server is in `metrics.py` with `/ui/` prefix routes; `server.py` `create_app()` is standalone demo only
- [x] Added `_ui_glossary` handler in `metrics.py` importing `page_glossary` + `GLOSS_CSS` from `server.py`
- [x] Added Glossary nav link to `_page()` sidebar in `metrics.py`
- [x] Added active-nav detection for "glossary & workflow" title
- [x] Extracted `GLOSS_CSS` constant from `server.py` (all `.gloss-*` + `.wf-*` rules)
- [x] Registered `/ui/glossary` route in `start_metrics_server()`
- [x] Verified in browser: 29-term grid renders with full card styling + color chips
- [x] Verified: animated workflow diagram renders; Plan Enrichment modal popup works

## Per-environment Prometheus/ELK URL overrides (2026-05-17, completed)

- [x] `EnvironmentSchema` in `schema.py`: `prometheus_url`, `elk_url`, `elk_api_key`, `elk_index_pattern` (all `str | None = None`)
- [x] `main.py`: `_resolve_prometheus_url(env, settings)` + `_resolve_elk_config(env, settings)` resolver functions
- [x] `run_env_probe_main()`: uses resolver — env URL wins over global
- [x] `run_ask_query()`: looks up env schema by `env_name`, uses resolver
- [x] Scheduler `_run_probe` closure: uses resolver for prom + now also wires ELK for first time
- [x] `example/inventory.yaml`: per-env override examples (commented)
- [x] `SETUP.md`: two-level resolution documented under Prometheus and ELK sections
- [x] `scripts/configure.sh`: prompts updated to say "global default — override per-env in inventory.yaml"
- [x] 14 new tests in `tests/config/test_env_url_overrides.py`
- [x] 1707 tests passing, 0 regressions; ruff clean; mypy clean

## Phase F — LangGraph Signal Integration (2026-05-16, completed)

- [x] F1: `StoredSignalContext` dataclass in `decisions.py`; `_load_stored_signals()` in `graph.py`; `plan_vm_node` reads disk/drift/patch/login history and passes to `prioritize_actions()`; 9 new tests (`test_plan_vm_stored_signals.py`)
- [x] F2: Early readiness check in `validate_targets_node` after OS detection; `TARGET_READINESS_BLOCKED` EventType; `check_target()` called at validate-time; 8 new tests (`test_validate_targets_readiness.py`)
- [x] F3: `_check_escalation()` in `probe.py`; `DigestReport.escalation_needed/escalation_reasons`; `render_digest_report()` escalation header; `main.py` posts Slack alert when escalation_needed; 14 new tests (`test_probe_escalation.py`)
- [x] F4: `post_cleanup_disk_gate_node` wired `dispatch_action → gate → check_more_actions`; ≥95% injects skipped result for patching; 90-94% warns only; `DISK_GATE_BLOCKED` EventType; 12 new tests (`test_disk_gate.py`)
- [x] 1582 tests passing, 111 skipped — 0 regressions
- [x] ruff: All checks passed. mypy: 77 source files, no issues.
- [x] `docs/learning/38-elk-journalctl-enrichment.md` created
- [x] `docs/learning/39-langgraph-signal-integration.md` created

## Phase E — ELK + journalctl Enrichment (2026-05-16, completed)

- [x] E2: `ElkClient` wired into `probe_vm`, `--ask`, `--probe-now`; `elk_errors` on `ProbeVMResult`
- [x] E3: `probe_vm` SSH-calls `journalctl -p err` + `systemctl --failed`; `_parse_journal_errors` / `_parse_failed_services`; `journal_errors` + `failed_services` on `ProbeVMResult`; `render_digest_report()` updated; 11 new tests (`test_probe_live_enrich.py`)
- [x] E4: `sources_used` on `FleetContext`; `data_sources` on `AssistantResponse`; `--ask` prints "Sources consulted:"; 8 new tests (`test_operator_assistant_sources.py`)
- [x] 1570 tests passing, 111 skipped — 0 regressions

## P0-1 — Immutable Signed Plan Artifact (2026-05-16, completed)

- [x] Commit 1: `enrich_plan_node` + `_enrich_vm_plan` + `_preview_patching` + `_preview_disk_cleanup` in `graph.py`
- [x] Commit 1: `_parse_upgradable_with_versions` in `patching.py`
- [x] Commit 1: Wired `collect_plans → enrich_plan → generate_plan_artifact` in `build_batch_graph()`
- [x] Commit 1: Load test `test_wave_abort_stops_fleet_at_boundary` call count updated (75 → 99)
- [x] Commit 1: 15 new tests (`test_enrich_plan.py`)
- [x] Commit 2: `_format_plan_for_approval()` updated — exact packages, disk preview, no disclaimer
- [x] Commit 2: `docs/SPEC.md` pre-P0-1 limitation note replaced
- [x] Commit 2: 13 new tests (`test_approval_message_p01.py`)
- [x] 1480 tests passing, 111 skipped — 0 regressions
- [x] ruff: All checks passed. mypy: 76 source files, no issues.
- [x] `autonomous_live_apply_enabled = False` unchanged
- [x] `docs/learning/37-immutable-plan-artifact.md` created

## Phase C — Prometheus HTTP Adapter (2026-05-16, completed)

- [x] Commit 1: `errander/integrations/prometheus.py` — `PrometheusClient`, 3 node_exporter metrics, best-effort
- [x] Commit 1: `Settings.prometheus_base_url` (env: `ERRANDER_PROMETHEUS_BASE_URL`, default `""`)
- [x] Commit 1: `VMSignalSummary.prometheus_metrics` + `ProbeVMResult.prometheus_metrics` + `DigestReport.all_prometheus_metrics`
- [x] Commit 1: 10 new tests (`tests/integrations/test_prometheus.py`)
- [x] Commit 2: Wire into `probe_vm()`, `run_env_probe()`, `OperatorAssistant._build_context()`, `_format_prompt()`, `render_digest_report()`
- [x] Commit 2: Wire into 3 main.py call sites (`run_env_probe_main`, `run_ask_query`, scheduler closure) with `try/finally close()`
- [x] Commit 2: `example/settings.yaml` documents `prometheus_base_url`
- [x] Commit 2: 12 new tests (`test_probe_prometheus.py`, `test_operator_assistant_prometheus.py`)
- [x] 1452 tests passing, 111 skipped — 0 regressions
- [x] ruff: All checks passed. mypy: 76 source files, no issues.
- [x] Optional invariant: `prometheus_base_url=""` → no client built, probe + ask unaffected
- [x] `docs/learning/36-prometheus-adapter.md` created

## Phase D — Operator Assistant Layer MVP (2026-05-15, completed)

- [x] Commit 1: `errander/models/analysis.py` — `AssistantResponse` (Pydantic), `VMSignalSummary`, `FleetContext` dataclasses
- [x] Commit 1: `errander/agent/operator_assistant.py` — `OperatorAssistant.investigate()`, `_build_context()`, `_format_prompt()`, `_fallback_response()`
- [x] Commit 1: 16 new tests (`test_operator_assistant.py`)
- [x] Commit 2: `--ask "question"` CLI flag + `run_ask_query()` in `main.py`
- [x] Commit 2: LLM wired when `llm_base_url` set; deterministic fallback when absent
- [x] Commit 2: 10 new tests (`test_main_ask.py`)
- [x] 1430 tests passing, 111 skipped — 0 regressions
- [x] ruff: All checks passed. mypy: 75 source files, no issues.
- [x] Layer A invariant: no SandboxExecutor/FileLocker/ApprovalManager in operator_assistant.py
- [x] `docs/learning/35-operator-assistant.md` created

## Phase B — Proactive Signals MVP (2026-05-15, completed)

- [x] Commit 1: `errander/agent/probe.py` — `probe_vm()` + `run_env_probe()` calling existing SRE nodes directly
- [x] Commit 1: `ProbeVMResult` + `DigestReport` dataclasses in `errander/models/reports.py`
- [x] Commit 1: `render_digest_report()` in `errander/observability/reporting.py`
- [x] Commit 1: `DAILY_PROBE_STARTED`, `DAILY_PROBE_COMPLETE`, `DAILY_PROBE_FAILED` event types
- [x] Commit 1: 16 new tests (`test_probe.py`, `test_digest_reporting.py`)
- [x] Commit 2: `signals: str | None` field in `ScheduleSchema`
- [x] Commit 2: `post_digest()` on `SlackClient`
- [x] Commit 2: `run_env_probe_main()` + `--probe-now <env>` CLI in `main.py`
- [x] Commit 2: Probe cron job registration in scheduler loop
- [x] Commit 2: `example/settings.yaml` documenting `signals` cron
- [x] Commit 2: 9 new tests (`test_main_probe.py`)
- [x] 1403 tests passing, 111 skipped — 0 regressions
- [x] ruff: All checks passed. mypy: 73 source files, no issues.
- [x] `docs/learning/34-proactive-signals.md` created

## Phase A.5 — Static gates cleanup (2026-05-15, completed)

- [x] Commit 1: ruff auto-fixes (382 → 327, -76)
- [x] Commit 2: ruff manual — TC001/TC003/E402/N814/SIM/B905/F841 (327 → 270, -57)
- [x] Commit 3: ruff E501 — line-length 100→120, per-file-ignores for web/, surgical splits (270 → 0)
- [x] Commit 4: mypy unused-ignore + type-arg (112 → 86, -26)
- [x] Commit 5: mypy call-overload/arg-type/attr-defined (86 → 38, -48); fix real bug run_bootstrap_known_hosts
- [x] Commit 6: remaining mypy + docs (38 → 0)
- [x] `uv run ruff check errander/` passes clean
- [x] `uv run mypy errander/` passes clean (72 source files, no issues)
- [x] 1378 tests passing, 111 skipped

## Phase A — Privilege Model Fixes (2026-05-15) — handed to Sonnet

Implementation plan: `tasks/sonnet-phase-a-plan.md`

### Commit 0 — Positioning docs (completed by Opus, 2026-05-15)
- [x] `docs/AI-ARCHITECTURE.md` — canonical two-layer model
- [x] `README.md` — new headline, Non-Goals section, AI-ARCHITECTURE link, updated Design Principles
- [x] `CLAUDE.md` — AI Safety Invariant section + anchor phrases
- [x] `docs/SPEC.md` — AI Safety Model summary + link
- [x] `STATUS.md` — Phase A context
- [x] `tasks/sonnet-phase-a-plan.md` — full implementation plan for Sonnet

### Commit 1 — Quick privilege fixes (Sonnet, completed 2026-05-15)
- [x] Remove `/usr/bin/env DEBIAN_FRONTEND=noninteractive` from `rollback.py` and `AptManager.install_version`; use `-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold` instead
- [x] Drop sudo from `AptManager.simulate_upgrade`
- [x] Add `EventType.SUDO_PREFLIGHT_FAILED`; migrate `sudo_preflight_node` to new event type
- [x] Add `tests/agent/test_sudo_preflight.py` with all behavior tests
- [x] Opportunistic ruff/mypy cleanup only in touched files

### Commit 2 — Docker wrapper mode (Sonnet, completed 2026-05-15)
- [x] Add `docker_command_mode: Literal["wrapper", "direct_sudo", "disabled"] = "wrapper"` to `EnvironmentSchema`
- [x] Refactor `docker_prune.py` to honor mode (wrapper / direct_sudo / disabled)
- [x] Add `parse_assess_output()` helper for the wrapper output format
- [x] Plumb `docker_command_mode` through `vm_graph.py`, `graph.py`, `main.py`
- [x] Update `REQUIRED_BINARIES_BY_ACTION` for mode-aware preflight
- [x] Update SETUP.md Docker hardening section with single `errander-docker-assess` wrapper + output format spec
- [x] Add `tests/agent/subgraphs/test_docker_prune_modes.py`
- [x] Extend `tests/agent/test_sudo_preflight.py` for mode-aware preflight checks

### Commit 3 — `--check-targets <env>` CLI (Sonnet, completed 2026-05-15)
- [x] Document supported distro matrix in SETUP.md and README.md
- [x] Create `errander/execution/target_validation.py` with `TargetReadiness` dataclass + `check_target()` + `render_readiness_report()`
- [x] Add `--check-targets <env>` flag to `main.py`
- [x] Wrapper script `--check` support documented in SETUP.md
- [x] Add `tests/execution/test_target_validation.py`
- [x] Extend `tests/test_main.py` for CLI flag behavior

### Phase A definition of done
- [x] All tests pass (`uv run pytest`) — 1378 passing, 111 skipped
- [x] Anchor phrases present in `docs/AI-ARCHITECTURE.md`, `CLAUDE.md`
- [x] `STATUS.md` and `tasks/todo.md` updated when Phase A complete
- [x] New learning doc: `docs/learning/XX-sudo-privilege-model.md`

## AI SRE Audit v2 — P0/P1/P2 Fixes (2026-05-14)

- [x] P0-3 — docker_prune/disk_cleanup/log_rotation execute_node: check result.success, propagate failures
- [x] P0-3 — commands.py AptManager/DnfManager upgrade_all: capture apt/dnf exit code, suppress only unhold/versionlock-delete failures
- [x] P0-4 — patching rollback_node: return ROLLED_BACK on success, ROLLBACK_FAILED on failure (distinct from FAILED)
- [x] P1-3 — validate_no_pkg_lock: fail-closed (block patching) when probe fails in live mode; dry-run keeps old permissive behavior
- [x] P1-5 — drift_baseline_node: skip compare_and_save in dry_run mode (read-only, no operational state mutation)
- [x] P2-3 — check_connectivity: consistent known_hosts policy with SSHConnectionManager (TOFU with warning when no path given)
- [x] P2-1 — BACKUP_VERIFY reclassified LOW (read-only action); moved to front of DEFAULT_PRIORITY
- [x] P1-1 — disk_cleanup/log_rotation/docker_prune runners now read approved action params from planned_actions
- [x] P1-4 — approval_gate_node: approval_timeout_seconds/approval_poll_interval_seconds wired from settings
- [x] 1305 tests passing, 111 skipped — no regressions

## SRE HITL Guardrails — Fourth-Pass Fixes (2026-05-14)

- [x] Fail closed when no approval_manager supplied — `approval_gate_node` returns `approved=False` with error instead of auto-approving when `require_live_approval=True` and `approval_manager is None`
- [x] `autonomous_live_apply_enabled` enforced — when False (default), any attempt to pass `require_live_approval=False` is silently overridden to True; gate is real not decorative
- [x] `require_live_approval` hardcoded — NOT loadable via settings.yaml/env vars until P0-1/P0-2 done; comment updated to say so explicitly
- [x] New tests: fail-closed with no approval_manager; autonomous gate prevents HITL bypass; deferred tests now supply mock approval_manager
- [x] 1310 tests passing, 111 skipped — no regressions

## SRE HITL Guardrails — P0-1/P0-2 Deferral Contract (2026-05-14)

- [x] Add `require_live_approval: bool = True` to Settings — ALL live batches require human approval regardless of policy tier; only relaxes when operator explicitly sets False
- [x] Add `autonomous_live_apply_enabled: bool = False` to Settings — product-level gate documenting current HITL-only posture
- [x] Default `approval_policy` → `strict` (schema.py + graph.py fallback) — was `moderate`
- [x] `approval_gate_node`: when `require_live_approval=True` and not dry-run, override approval tiers to include ALL risk tiers including LOW
- [x] `_format_plan_for_approval`: honest disclaimer in Slack message — "You are approving action categories and parameters, not exact pinned commands/packages"; deferred re-approvals flagged with :repeat: header
- [x] `_window_opener`: deferred execution now re-plans and requests fresh human re-approval (not silent re-execution of old approval); audit event updated accordingly
- [x] `run_env_batch`: `is_deferred_reapproval: bool = False` parameter threads through to graph state
- [x] SPEC.md: removed false exactness claims from `PlannedAction`; added honest note about current pre-P0-1 limitation
- [x] Updated two policy auto-approve tests to pass `require_live_approval=False` (testing policy tier logic, not HITL override); added HITL override test
- [x] 1308 tests passing, 111 skipped — no regressions

## AI SRE Audit v2 — Second-Pass Residuals (2026-05-14)

- [x] Residual P0-3 — log_rotation: logrotate failure with empty large_files now returns FAILED; fallback succeeds only if all per-file rotations succeed AND there were actual large_files
- [x] P2-3 full — check_connectivity now has strict_host_keys param (default True); refuses without known_hosts_path in strict mode, consistent with SSHConnectionManager
- [x] mypy: action_params extraction uses isinstance(raw, dict) instead of dict(object) — type-safe
- [x] mypy: _get_connection_params in disk_cleanup/log_rotation/docker_prune uses str() cast — removes wrong typeddict-item ignores
- [x] mypy: 112 errors (down from 142 at second-pass audit — net improvement)
- [x] 1307 tests passing, 111 skipped — no regressions
- [ ] P0-1 — Immutable approved plan artifact (architecture work — deferred)
- [x] P0-2 — Deferred execution applies exact approved artifact (commits 29e72de, b4641e1 — 2026-05-16)

## SRE Production Wiring Fix (2026-05-14)

- [x] High 1 — Wire `VMDiskHistoryStore`, `BaselineStore`, `VMStateStore` through `async_main` → `run_env_batch` → `build_batch_graph` → `make_wave_dispatcher` → `build_vm_graph`
- [x] High 2 — Thread `critical_services` from `TargetSchema` → `yaml_targets` → `VMGraphState` → `PatchingGraphState` via both `Send()` paths
- [x] High 3 — Pass `audit_store` + `vm_state_store` to `build_patching_subgraph`; add `batch_id` to `PatchingGraphState` so nodes read correct id from state
- [x] Medium 4 — Remove `authentication failure` from `failed_logins_command` grep (regex couldn't parse it — honest fix)
- [x] New test file `tests/agent/test_sre_wiring.py` — 10 tests proving full wiring chain; 1,303 total passing

## SRE Auditor Second Pass — Non-Blocking Items (2026-05-14)

- [x] URL-quote all path segments in UI links/form actions (`_uq = urllib.parse.quote(safe="")`) — defense in depth alongside `_esc`
- [x] Fix stale `test_inventory_playwright.py` — add `_YAML_FLEET` VMTargets, pass as `base_inventory` to fixture server, update empty-state assertion
- [x] Auditor verdict: "substantially fixed, acceptable for pre-production" — no more blockers
- [x] 1303 tests passing — no regressions

## Inventory UI — Full YAML Fleet (2026-05-14)

- [x] Add `_BASE_INVENTORY_KEY` app key to `metrics.py`
- [x] Add `base_inventory: list[VMTarget] | None` param to `start_metrics_server`
- [x] Rewrite `_ui_inventory_get`: YAML VMs as base with DB override status, ad-hoc VMs appended; YAML vs ad-hoc badge per row
- [x] `main.py`: call `load_inventory()` and pass `flat_inventory` to `start_metrics_server`
- [x] 1303 tests passing — no regressions

## SRE UI Revalidation — 3 Remaining Issues (2026-05-14)

- [x] XSS — `_page()` still injected raw `title` into `<title>` and `.tb-title`; fixed with `_esc(title)`
- [x] XSS — dashboard/batches/approvals still rendered raw `batch_id` and `vm_id` in links and form actions; all escaped
- [x] Settings DB overrides not applied on restart — `OverridesStore` now initialized before `_build_components()`, DB overrides fetched and passed to second `load_settings()` call so restart picks them up
- [x] 1303 tests passing — no regressions

## SRE UI Audit Remediation (2026-05-14)

- [x] Critical 1 — Add `@web.middleware` to `_csrf_middleware` (was missing → HTTP 500 on all POST /ui/* routes)
- [x] Critical 2 — Fix `_inject_csrf` return value (returned token not modified html); wire into `_page()` via `request=` param; call from settings/inventory/approvals GET handlers
- [x] High 1 — XSS: apply `html.escape` to all untrusted DB/URL fields (batch_id, vm_id, action_type, detail, env_name, vm_name, host, os_family, flash messages, settings display_val)
- [x] High 2 — Settings "restart required" note: added amber warning that LLM settings take effect after agent restart
- [x] Medium 1 — test-llm endpoint: GET→POST so API keys never appear in URLs/access logs/browser history
- [x] Medium 2 — `_VALID_OS_FAMILIES` narrowed to `{"ubuntu","debian","rhel"}` matching core `OSFamily` enum
- [x] 1303 tests passing, 111 skipped — no regressions

## UI Nav Bug Fix (2026-05-13)

- [x] Audit all UI route wiring — found duplicate `/batches` in NAV_ITEMS causing both "Active Batch" and "Batch History" to highlight simultaneously
- [x] Remove "Active Batch" nav item (redundant — fleet dashboard already shows active batch card)
- [x] Delete dead `sidebar()` and `_sidebar_nav()` functions (never called by `layout()`)
- [x] Verify all 8 routes return 200 and each page has exactly one active nav item

## SRE Monitoring — PR-G Groundwork (2026-05-13)

- [x] G1 — `ActionStatus.BLOCKED` enum value
- [x] G2 — 8 new `EventType` values (preflight, reboot, service health, disk, drift, failed logins)
- [x] G3 — `VMTarget.critical_services` field + inventory inheritance
- [x] G4 — migrations framework (`errander/safety/migrations.py`, 4 migrations); `AuditStore.initialize()` delegates to `run_migrations()`
- [x] G5 — `VMStateStore` (vm_state.py), `BaselineStore` + `DriftCheck` Protocol (baselines.py), `VMDiskHistoryStore` (disk_history.py)
- [x] G6 — `BatchReport` model + supporting dataclasses (models/reports.py); `SRESignalSettings` config block
- [x] All 84 new tests passing (996 total); mypy strict clean; ruff clean

## SRE Phase 1 — Signal Collection

- [x] 1.1 — Package lock detection pre-flight: `PackageManager.detect_lock()` + `validate_no_pkg_lock()` + `preflight_lock_node` in patching subgraph; 35 new tests; 1031 total passing
- [x] 1.2 — Reboot-required detection (post-patch): `reboot_check.py` with `RebootStatus`, `reboot_required_command()`, `parse_reboot_status()`, `detect_reboot_required()`; `reboot_check_node` after verify; `format_reboot_required_section()` in reporting; 46 new tests; 1077 total passing
- [x] 1.3 — Service health checks (pre/post action snapshot): `service_check.py` with `ServiceStatus`, `service_status_command()`, `parse_service_statuses()`, `find_regressions()`, `check_services()`; `service_health_pre_node` + `service_health_post_node`; `SERVICE_HEALTH_REGRESSION` audit event; `sre_service_check` flag; 47 new tests; 1124 total passing
- [x] 1.4 — Disk growth trend: `disk_trend.py` with `disk_bytes_command()`, `parse_df_bytes()`, `compute_growth_alert()`, `detect_growth_alerts()`, `record_and_detect_disk_growth()`; `disk_snapshot_node` in vm_graph wired between discover and drift_check; 24 new tests; 1,148 total passing
- [x] 1.5 — Configuration drift detection + failed SSH logins: 4 drift check modules (authorized_keys, sudoers, listening_ports, scheduled_jobs) in `drift_checks/`; `failed_logins.py`; `drift_baseline_node` + `failed_logins_node` in vm_graph; generalized SRE chain wiring; 97 new tests; 1,245 total passing

## SRE Phase 2 — Signal Aggregation + Report Rendering

- [x] 2.1 — SRE signal threading: `disk_snapshot_node` serialization adds `window_start`/`window_end`; `_merge_sre_list` reducer; `BatchGraphState` SRE fields; `run_vm_node` extracts SRE signals; 1,283 total passing
- [x] 2.2 — `render_batch_report()`: deterministic Slack-formatted renderer, all 7 sections, drift grouped by kind, sections omitted when empty; 47 tests in `test_reporting.py`
- [x] 2.3 — `generate_report_node` refactor: deserializes SRE dicts to typed objects, builds `BatchReport`, calls `render_batch_report()`

## PR-2 Gap Closure (2026-05-14)

- [x] Gap 1 (correctness) — `parse_listening_ports`: strip `pid=\d+` and `fd=\d+` via `_EPHEMERAL_RE` before sorting; 4 new tests; 1,287 total passing
- [x] Gap 2 (docs debt) — `example/settings.yaml`: added full annotated `sre_signals:` block with all 10 tuneable fields
- [x] Gap 3 (feature) — `disable_failed_login_check: bool = False` per-VM tag: `TargetSchema` → `yaml_targets` dict → `VMGraphState` → `failed_logins_node` early-exit; documented in `example/inventory.yaml`

## Plan Gap Closure Round 2 (2026-05-14)

- [x] Systemd timers — `scheduled_jobs.py` now includes `systemctl list-timers | awk '{print $NF}'` as 4th source; timer unit names captured; volatile timestamps excluded; 6 new tests; 1,293 total passing
- [x] `docs/learning/README.md` — entries 25–31 added
- [x] `README.md` — test count updated 929 → 1293 (3 occurrences)

## Operations Hub UI — Glossary + Inventory + Settings + Admin (2026-05-13)

- [x] Glossary page: 18-term glossary grid (CORE/SAFETY/ACTIONS/INFRA categories) + animated LangGraph DAG workflow diagram with node-click modal popups
- [x] Inventory page: KPI tiles (Total VMs, OS Types, Reachable), filter bar (search + env/os/status dropdowns), full VM table
- [x] Settings page: 4 read-only config cards (LLM, Slack, Scheduling, Safety & Audit) in 2-col grid
- [x] Admin page: Agent controls card, system health checks, lock manager (empty state), override toggles (CSS), danger zone
- [x] Wired handle_inventory(), handle_settings(), handle_admin() route handlers
- [x] NAV_ITEMS updated with ADMIN section + Admin Panel link
- [x] All routes registered in create_app() — /inventory, /settings, /admin, /glossary all live

## Fourth-Round Audit: Action Params in Plan Artifact (2026-05-12)

### From ai_sre_audit.md fourth re-audit (2026-05-12)
- [x] Medium Risk — Batch plan includes action params: `plan_vm_node` now serializes `"params": a.params`; plan hash covers params; Slack summary shows non-empty params; 4 regression tests added

## Third-Round Audit: 2 Blockers + 2 High Risks (2026-05-12)

### From ai_sre_audit.md third re-audit (2026-05-12)
- [x] Blocker 1 — Empty approved plan distinguisher: added `pre_approved_plan_set: bool` sentinel to `VMGraphState`; `route_after_drift_check` routes empty approved plan → audit_results (not re-plan)
- [x] Blocker 2 — Missing approved plan fail-closed: live mode VM not in `vm_id_to_approved_actions` → injects `error` + `pre_approved_plan_set=True`; never falls back to re-planning after approval
- [x] High Risk 1 — Log rotation verify real read: `verify_node` in `log_rotation.py` now passes `dry_run=False`
- [x] High Risk 2 — DNF rollback version comparison: `_rollback_patching_dnf` now parses rpm output and compares each package version against snapshot; returns failure on any mismatch

## Re-Audit: 7 Production Blockers (2026-05-12)

### From ai_sre_audit.md re-audit (2026-05-11)
- [x] Blocker 1 — Pre-approved plan enforced: `route_after_drift_check` skips re-planning when `planned_actions` populated; `dispatch_current_wave` passes `vm_id_to_approved_actions` to each VM; `drift_check` conditional edges include `"dispatch_action"`
- [x] Blocker 2 — LLM in planning: `plan_vm_node` passes `llm_client`, `ai_decision_store`, `env_policy`, `batch_id`, `vm_id` to `prioritize_actions()`
- [x] Blocker 3 — Live mode unblocked: `--unsafe-legacy-live` removed from `main.py`; live guard block removed; `--live` flag works directly
- [x] Blocker 4 — Read-only always live: all assess/snapshot/verify nodes across all 5 subgraphs use `dry_run=False`
- [x] Blocker 5 — Verify → rollback: `verify_node` in patching sets `status=FAILED`; `route_after_verify` conditional edge routes FAILED to rollback; graph wired with `add_conditional_edges("verify", route_after_verify, ["rollback", END])`
- [x] Blocker 6 — DNF rollback: `_rollback_patching_dnf` added; `rollback_action` dispatches by `os_family`; `rollback_node` passes `os_family`
- [x] Blocker 7 — Audit mode wiring: `AuditStore(strict_mode=(settings.audit_mode == "strict"))` in both `async_main` and `run_audit_query`
- [x] Fix test failures: disk_cleanup needs 11 SSH calls (6 assess + 5 execute); added missing responses; fixed `drift_check` conditional edges missing `"dispatch_action"`

## Phase 4: E2E Verification (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 4.1 Staging soak — `tests/staging/soak_checklist.md`: 8-step checklist covering OS verification, dry-run/live run per action type, fleet abort, SSH host key pinning, chaos DB lock, AI audit; destroy VMs after each run
- [x] 4.2 Chaos suite — `tests/chaos/test_fault_injection.py`: 19 fault-injection tests covering SSH drop, patching rollback trigger, dpkg lock, audit strict/best-effort modes, LLM timeout/malformed/unavailable, approval manager rejection, fleet abort node
- [x] 4.3 Windows test infra fix — hardcoded `/tmp/test-locks` → `tmp_path / "locks"` in `test_graph.py:181`; confirmed no bare `TemporaryDirectory()` usage in test suite; all 918 tests pass on Windows

## Phase 3: Honest AI Integration (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 3.1 Thread LLMClient into graph — `build_batch_graph` → `make_wave_dispatcher` → `build_vm_graph` → `plan_actions_node`; `run_env_batch` accepts `llm_client`; all 3 call sites in main.py updated
- [x] 3.2 Constrained plan schema — injection guard (`_INJECTION_RE`) rejects shell metacharacters in LLM action type strings; policy enforcement filters LLM output; `_parse_action_types` validates against allow-list; policy name logged on every call
- [x] 3.3 AI eval harness — `tests/ai_evals/test_golden_plans.py`: 32 tests across golden plans, injection corpus (8 payloads), schema-violation corpus, per-decision audit capture
- [x] 3.4 Per-decision AI audit — `errander/safety/ai_audit.py`: `AIDecisionStore` + `AIDecision` dataclass; `ai_decisions` SQLite table; logs model, base_url, prompt_template_id, prompt_hash, response, outcome, latency_ms, token counts per call; integrated into `prioritize_actions()`

## Phase 2: Policy Enforcement + Fleet Safety (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 2.1 Wire `requires_approval()` into validate_action — policy param now used; CRITICAL always blocked; policy name in rejection reasons; `env_policy` threaded from BatchGraphState → VMGraphState via Send payload
- [x] 2.2 Enforce `fleet_failure_threshold` — `check_fleet_health_node` between validate_targets and planning fan-out; FLEET_ABORT audit event; routing aborts to generate_report when threshold exceeded
- [x] 2.3 Strict OS verification — `validate_targets_node` replaced `echo ok` with `cat /etc/os-release` + `parse_os_release()` + `verify_os_match()`; mismatches emit OS_MISMATCH audit event; detected os_family stored in target dict for downstream
- [x] Added `FLEET_ABORT` and `OS_MISMATCH` to EventType enum
- [x] `tests/agent/test_phase2_policy.py` — 21 tests covering all three items

## Phase 1: Security Hardening (2026-05-11)

### From ai_sre_remediation_plan.md
- [x] 1.1 SSH host key verification — `known_hosts_path` + `strict_host_keys` in settings + SSHConnectionManager; TOFU logs WARNING; strict mode refuses connection; `--bootstrap-known-hosts <env>` CLI; 6 tests
- [x] 1.2 Shell injection — `errander/execution/command_builder.py` with `safe_path`, `safe_pkg`, `safe_ver`, `pkg_version_spec`, `build_cmd`; injection sites fixed in `backup_verify.py`, `log_rotation.py`, `commands.py`, `rollback.py`; 22 injection corpus tests
- [x] 1.3 Kernel exclusion fix — `AptManager.upgrade_all` / `DnfManager.upgrade_all` now query exact installed kernel names via dpkg-query/rpm, filter in Python, hold/versionlock by exact name; no more glob-based apt-mark
- [x] 1.4 Docker prune scope — default uses `docker image prune -f && docker container prune -f` (dangling-only); `docker_prune_aggressive=True` reclassified HIGH runs `system prune -af`; 4 tests
- [x] 1.5 UI security — bind default `127.0.0.1`; mandatory auth when non-loopback; CSRF double-submit cookie middleware on all POST /ui/*; `_inject_csrf` helper adds hidden token to all form tags; `ui_bind_address` setting

## Phase 0: SRE Audit Remediation (2026-05-11)

### Ship-stopper fixes from ai_sre_remediation_plan.md
- [x] Finding #2 — dry_run single source of truth: `SandboxExecutor.execute()` per-call override; all sub-graphs read `state["dry_run"]`
- [x] Finding #3 — plan/apply before execution: planning fan-out → ImmutablePlan with SHA-256 hash → approval gate BEFORE execution
- [x] Finding #3 (gap) — `verify_plan_hash_node` re-verifies hash at execution time; tampered hash aborts cleanly
- [x] Finding #5 — patching rollback (Option A): dpkg snapshot + apt-get --allow-downgrades + verification in `rollback_node`
- [x] Finding #6 (gap) — `env_policy` threaded from inventory → `BatchGraphState` → approval gate; strict policy gates MEDIUM tier
- [x] Finding #13 — audit fail-closed: `AuditWriteError` raised in strict mode; dry-run stays best-effort
- [x] Phase 0 gate: `--unsafe-legacy-live` guard in main.py
- [x] Fix all 9 test failures from Phase 0 changes (mock sigs, routing, deferred logic, audit mode, rollback assertions, SSH call counts)
- [x] `tests/agent/test_plan_apply_flow.py` — 20 tests for plan/apply integrity (hash verify, routing, policy thresholds)

## Phase 1.8 — End-to-End Validation + configure.sh Polish (2026-05-10)

### configure.sh UX fixes
- [x] Ask "Do you want to add VMs?" before showing section header (fresh install)
- [x] Suppress SSH key step header when key already exists
- [x] Remove SSH key generation — script now verifies only; users own key creation (SETUP.md Step 2)
- [x] Split "Keep existing VMs and add more?" into two separate prompts
- [x] Remove stale "Complete Steps 2-3" reminder from final summary
- [x] Final summary shows Step 6 verify before Step 7 dry-run, matching SETUP.md order
- [x] Add --force --force-reason to dry-run command in summary and SETUP.md Step 7
- [x] Fix approval gate — dry-run batches auto-approved, no human gate needed
- [x] Fix "Add more VMs? (y/N)" defaulting to yes on Enter — flip case branches
- [x] Fix fresh install Enter on "Add VMs?" silently adding no VMs
- [x] Fix "Keep + Add more" silently dropping new VMs (append TARGETS_YAML)
- [x] Fix re-run resetting UI password — read existing creds from .env first
- [x] Prompt for web UI username + password explicitly (with confirmation loop)
- [x] Add optional Fernet encryption — key to ~/.errander.key, enc:v1: blobs in .env
- [x] chmod 600 .env always on write
- [x] Auto-wire encryption key to shell RC and systemd — no manual steps

### SETUP.md fixes
- [x] Step 7 and Step 8 --env dev → --env <your-env-name>
- [x] Azure Foundry URL: openai.azure.com → cognitiveservices.azure.com
- [x] systemd service: hardcoded errander user → $(whoami) + $(pwd)
- [x] Quick path description: "SSH key" → "verify your SSH key path"
- [x] Add UI password change warning

### bootstrap.sh
- [x] Correct step numbers in completion message; surface configure.sh quick path

## Phase 1: Scaffold + First Action End-to-End (disk_cleanup)

### 1.1 Project Foundation
- [x] Scaffold project structure (Option C: Parent + Fan-Out + Sub-Graphs)
- [x] Create pyproject.toml with all dependencies
- [x] Define data models (VM, Action, Plan, Event)
- [x] Define state dataclasses (BatchState, VMMaintenanceState, per-action states)
- [x] Define strategy pattern stubs (PackageManager, AptManager, DnfManager)
- [x] Define policy system (relaxed/moderate/strict)
- [x] Create test structure mirroring src
- [x] Run `uv sync` and verify all imports work
- [x] Run `uv run pytest` and verify passing tests

### 1.2 Core Infrastructure
- [x] Implement Settings loading from env vars + YAML (settings.py, schema.py, inventory.py)
- [x] Implement inventory YAML loading with environment→host inheritance
- [x] Implement config schema validation (Pydantic models)
- [x] Implement audit logging to SQLite (AuditStore with write/query/count)
- [x] Implement SSH execution layer (SSHConnectionManager with pooling + retry)
- [x] Implement OS detection via SSH (parse /etc/os-release, df, docker, uptime)
- [x] Implement sandbox/dry-run execution wrapper (SandboxExecutor + CommandRecord)
- [x] Implement file-based VM locking (FileLocker with TTL + stale detection)

### 1.3 First Action: Disk Cleanup (lowest risk)
- [x] Implement disk_cleanup sub-graph (validate → assess → execute → verify)
- [x] Implement whitelist enforcement (hardcoded, never LLM-decided)
- [x] Implement dry-run simulation for disk cleanup
- [x] Implement AptManager + DnfManager command generation (clean_cache, autoremove, etc.)
- [x] Write tests for disk cleanup sub-graph (31 tests)
- [ ] Test against a real VM (dry-run mode)

### 1.4 Per-VM Graph
- [x] Implement vm_graph (lock → discover → plan → dispatch → check_more → audit → unlock)
- [x] Implement discovery node (SSH gather system state via detect_os)
- [x] Implement action dispatch with conditional routing to sub-graphs (disk_cleanup; others SKIPPED)
- [x] Implement hardcoded action prioritization (LLM deferred to Phase 1.6)
- [x] Write tests for per-VM graph (28 tests)

### 1.5 Batch Orchestrator
- [x] Implement batch graph (init_batch → validate_window → validate_targets → fan_out → collect → report)
- [x] Implement Send() fan-out to per-VM graphs (via conditional edge routing function)
- [x] Implement result collection and aggregation (append-only reducer)
- [x] Implement report generation (template-based; LLM Phase 1.6)
- [x] Write tests for batch orchestrator (21 tests)

### 1.6 Integrations
- [x] Implement LLM client (OpenAI SDK → vLLM) with fallback (23 tests)
- [x] Implement Slack client (post message, poll reactions)
- [x] Implement approval gate (post plan → poll → approve/reject/timeout)
- [x] Implement Prometheus metrics and /health endpoint
- [x] Implement dual-channel approval (Slack reactions + UI buttons racing via asyncio.wait)

### 1.7 Config & Scheduling
- [x] Implement inventory YAML loader and validator
- [x] Implement maintenance window enforcement
- [x] Implement APScheduler setup
- [x] Create example inventory.yaml

### Playwright UI Tests
- [x] Add pytest-playwright to dev dependencies + install Chromium
- [x] Write server fixture (aiohttp in background thread, seeded :memory: SQLite)
- [x] Dashboard tests (6): loads, status, event count, batches, nav links, navigation
- [x] Batch list tests (3): loads, both batches, link navigation
- [x] Batch detail tests (8): loads, count, event types, detail text, VM link, back link, empty state
- [x] VM history tests (6): loads, count, detail, back link, slash URL, empty state
- [x] Endpoint smoke tests (2): /health, /metrics

### Documentation
- [x] Write docs/SETUP.md (end-to-end setup: prerequisites, vLLM, SSH, Slack, config, first run, systemd, monitoring, troubleshooting)

### vLLM Deployment
- [x] Create deploy/vllm/docker-compose.yml (GPU passthrough, exact serve command, healthcheck)
- [x] Create deploy/vllm/.env.example (MODEL_ID, HF_TOKEN, GPU_MEM_UTIL, VLLM_PORT, MODEL_CACHE_DIR)
- [x] Add LLMClient.check_endpoint() (reachability + model list + test completion latency)
- [x] Add --check-llm CLI flag to main.py

### Web UI (built into aiohttp server)
- [x] Extend start_metrics_server() with audit_store parameter
- [x] Implement /ui dashboard (status, event count, recent batches, auto-refresh)
- [x] Implement /ui/batches batch history page
- [x] Implement /ui/batches/{id} batch detail page
- [x] Implement /ui/vms/{vm_id} VM history page (slash-safe URL matching)
- [x] Implement /ui/approvals page (pending approvals with Approve/Reject buttons)
- [x] Implement POST /ui/approvals/{id}/approve and /reject endpoints
- [x] Add pending approvals count card to dashboard

### SQLite Audit Integration (native)
- [x] Add action_type filter to AuditStore.get_events()
- [x] Add AuditStore.get_recent_batches() method
- [x] Add --audit CLI mode to main.py (--batch-id, --vm-id, --action-type, --event-type, --last, --batches)
- [x] Write integration tests: action_type filter, get_recent_batches, vm_graph audit trail, CLI queries (21 tests)

### 1.8 End-to-End Validation
- [x] Wire validate_window_node in graph.py to real is_within_window() call
- [x] Implement main.py entry point (load config, start scheduler + metrics server, wire graph)
- [x] Overhaul SETUP.md — prerequisites, network ports, Azure NSG, architecture diagram, step labels
- [x] Create scripts/bootstrap.sh — distro-agnostic Linux bootstrap (Ubuntu/Debian/RHEL/CentOS/Oracle/Fedora)
- [x] Create scripts/bootstrap.ps1 — Windows bootstrap (winget git, official uv installer, no admin)
- [x] Create scripts/configure.sh — interactive setup (LLM, VMs, SSH key, Slack, writes .env + inventory.yaml)
- [x] Create .gitattributes — enforce LF line endings for .sh files
- [x] Fix Step 4: verify LLM inline (no .env), collect values → Step 5 creates .env
- [x] Fix Step 5 + old Step 6 confusion: merged Slack into Configure step, steps 6-9 renumbered
- [x] Add Steps 4-6 quick path section (configure.sh one-liner)
- [ ] Dry-run disk_cleanup on a test VM via the full graph pipeline
- [ ] Verify audit trail captures all events
- [ ] Verify Slack notification works
- [ ] Verify metrics are exposed

## Phase 2: Remaining Action Types
- [x] Implement log_rotation sub-graph (path validation, logrotate + manual fallback, idempotency)
- [x] Implement docker_prune sub-graph (docker availability check, dangling/stopped detection, idempotency)
- [x] Implement patching sub-graph (kernel exclusion via fnmatch, version snapshot, rollback, idempotency)
- [x] Implement backup_verify sub-graph (read-only: exists/recent/non-zero checks, no execute node)
- [x] Wire all sub-graphs into vm_graph.py dispatch (all 5 action types now dispatched)
- [x] Idempotency via pre-check skipping in all assess nodes (nothing_to_do flag)
- [x] 28 log_rotation tests, 18 docker_prune tests, 24 patching tests, 14 backup_verify tests
- [x] Design review — 10 issues found and fixed (kernel exclusion, whitelist, approval, rollback, etc.)
- [x] All 587 tests passing, lint clean

## Phase 3: Hardening
- [x] Rolling updates (percentage-based fleet caps)
- [x] Canary logic (run on 1 VM first, then fleet)
- [x] Drift detection (pre-flight check before live execution)
- [x] Comprehensive error handling and edge cases (25 new tests, 677 total)
- [x] Load testing with multiple VMs (20 tests: wave partitioning, fleet batch graph, concurrent locks)
- [x] Playwright approvals UI tests (22 tests: page content, navigation, approve/reject actions, badge cross-page)

## Phase 4: LLM Flexibility + Secrets Encryption + UI Config

### Phase A — LLM Provider Flexibility
- [x] Remove hardcoded `Qwen/Qwen3-8B-AWQ` from `llm.py`; add `model: str` + `temperature: float` to `LLMClient.__init__`
- [x] Remove `thinking: bool` param and `/no_think` prefix from `complete()`
- [x] Add `llm_model` + `llm_temperature` to `Settings` dataclass + `load_settings()`
- [x] Add `model` + `temperature` fields to `LLMSettingsSchema` with validator (0.0–2.0)
- [x] Update `_build_components()` and `run_llm_check()` in `main.py` to pass model/temperature
- [x] Update `decisions.py` — remove `thinking=` kwarg from all `client.complete()` calls
- [x] Add `model` + `temperature` to `config/settings.yaml` and `example/settings.yaml`
- [x] Write `docs/LLM-PROVIDERS.md` (vLLM, Ollama, OpenAI, Anthropic, Groq configs)
- [x] Rewrite `tests/integrations/test_llm.py` (removed thinking tests, added verbatim/temp/model tests)

### Phase A.5 — Secrets Encryption
- [x] Implement `SecretsManager` with Fernet (`enc:v1:<token>` format) in `errander/integrations/secrets.py`
- [x] Add `--generate-secrets-key` and `--encrypt VALUE` CLI flags to `main.py`
- [x] Add YAML decryption (`_decrypt_yaml_strings`) to all `validate_*` functions in `schema.py`
- [x] Update `_load_env_str()` in `settings.py` to decrypt `enc:v1:` env var values
- [x] Implement `SecretsRedactingFilter` log filter in `errander/observability/redaction.py`
- [x] Attach redaction filter to root logger in `main.py`
- [x] Write `docs/SECRETS.md` (setup, threat model, key rotation)
- [x] Write `tests/integrations/test_secrets.py` (24 tests)
- [x] Write `tests/observability/test_redaction.py` (9 tests)
- [x] Write `tests/config/test_secrets_loading.py` (6 tests)

### Phase B — UI Settings + Inventory Management
- [x] Implement `OverridesStore` (SQLite) in `errander/safety/overrides.py` — `settings_overrides` + `inventory_overrides` tables
- [x] Extend `load_settings()` with `db_overrides: dict[str, str]` param — env > DB > YAML > default precedence
- [x] Add `SETTINGS_CHANGED` + `INVENTORY_CHANGED` to `EventType` enum in `events.py`
- [x] Add `GET/POST /ui/settings` routes with source indicators, model presets, reset buttons, LLM test
- [x] Add `GET/POST /ui/inventory` routes — disable YAML VMs, add/delete ad-hoc VMs
- [x] Add HTTP Basic Auth middleware on `/ui/*` (via `secrets.compare_digest`)
- [x] Implement inventory merge in `run_env_batch()` (YAML → filter disabled → append db_additions)
- [x] Wire `overrides_store` into scheduler loop `_run()` closure
- [x] Add `ui_user`, `ui_password`, `sources` fields to `Settings`
- [x] Update `async_main()`: init `OverridesStore`, pass to `start_metrics_server()`
- [x] Write `tests/safety/test_overrides.py` (18 tests — T1)
- [x] Write `tests/config/test_settings_precedence.py` (21 tests — T2)
- [x] Write `tests/agent/test_inventory_merge.py` (9 tests — T3)
- [x] Write `docs/learning/22-ui-settings-and-inventory.md`
- [x] Update `docs/SETUP.md` — Step 5b: Secure the Web UI
- [x] Update `STATUS.md` and `tasks/todo.md`
- [x] 799 tests passing, lint clean

### Phase B — Playwright Tests (T4-T6)
- [x] Write `tests/ui/test_settings_playwright.py` (15 tests): page load, save+persist, reset, env-lock source labels
- [x] Write `tests/ui/test_inventory_playwright.py` (17 tests): page load, VM display, toggle, add ad-hoc VM, delete
- [x] Write `tests/ui/test_ui_auth_playwright.py` (13 tests): 401 without creds, 200 with creds, wrong user/pass, WWW-Authenticate, /metrics+/health open
- [x] Bug fix: nested `<form>` inside main settings form broke Save button (Chromium closes outer form). Fixed via HTML5 `form="reset-{key}"` out-of-band pattern.
- [x] Create `errander/__main__.py` so `python -m errander` works
- [x] 844 tests passing, lint clean

### Deferred Execution — Window-Gated Approval
- [x] Add `EXECUTION_DEFERRED` + `DEFERRED_EXECUTION_STARTED` to `EventType` (`errander/models/events.py`)
- [x] Create `DeferredExecutionStore` with SQLite table `deferred_executions` (`errander/safety/deferred.py`)
- [x] Add `next_window_open()` and `window_start_cron()` to `errander/scheduling/windows.py`
- [x] Extend `BatchGraphState` with `env_name` and `deferred` fields (`errander/agent/graph.py`)
- [x] Add deferral logic to `approval_gate_node` — saves to `DeferredExecutionStore`, logs audit event, Slack notification
- [x] Add `deferred_store` param to `build_batch_graph()`
- [x] Add `_window_opener()` function to `errander/main.py`
- [x] Register window-opener cron jobs in scheduler loop
- [x] Initialize `DeferredExecutionStore` alongside `AuditStore` in `async_main()`
- [x] Thread `env_name` into batch initial state
- [x] Write `tests/safety/test_deferred.py` (15 tests)
- [x] Write 9 new window helper tests in `tests/scheduling/test_windows.py`
- [x] Write 6 new approval gate deferred tests in `tests/agent/test_graph.py`
- [x] Write 3 new `_window_opener` tests in `tests/test_main.py`
- [x] Update `STATUS.md`, `todo.md`, `command-log.md`, `docs/learning/24-deferred-execution.md`
- [x] 878 tests passing
