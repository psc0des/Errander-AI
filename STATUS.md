# Errander-AI — Project Status

## Last Updated
2026-06-10

## Current Phase
**§8d Step 0 — CI (2026-06-10, COMPLETE).**

GitHub Actions CI added following a zero-trust review by Fable 5 (senior SRE / enterprise AI architect). CI runs on every push to `main` and every PR: `ruff check .`, `mypy errander/`, `pytest` (2,626 tests, excluding Playwright/staging), and `gitleaks` secret scan. Three ruff errors fixed (B904 in metrics.py, E501 in metrics.py, I001 in test_prompts.py). mypy `exclude` added to `pyproject.toml` so `uv run mypy .` passes (tests/scripts were the source of 621 pre-existing errors; errander/ package was already clean).

### Files changed (2026-06-10 — CI setup, §8d step 0)
- `.github/workflows/ci.yml` — NEW: lint, test (SQLite), secrets (gitleaks) jobs
- `.gitleaks.toml` — NEW: allowlist for example/demo/deploy placeholder credentials
- `errander/observability/metrics.py` — fix B904 (raise from None) + E501 (split 122-char HTML line)
- `tests/config/test_prompts.py` — fix I001 (auto-fixed import sort)
- `pyproject.toml` — add `exclude = ["^tests/", "^scripts/"]` to [tool.mypy]
- `README.md` — CI badge

## Previous Phase
**configure.sh and add-target.sh now install wrappers automatically (2026-06-09, COMPLETE).**

After the wizard, `configure.py` (called by `configure.sh`) now SSHes into each VM and — with a per-item confirmation prompt — checks and installs Node Exporter, docker wrappers, and the service restart wrapper based on what's enabled in inventory.yaml. `add_target.py` does the same after adding new VMs. Service restart unit names are now collected in the wizard (required), not deferred. The `service_restart_intent` concept is removed.

### Files changed (2026-06-09 — auto wrapper install)
- `errander/config/inventory_wizard.py` — collect restart units immediately (required); remove intent-only path
- `errander/config/configure.py` — add `_check_docker_wrappers`, `_install_docker_wrappers`, `_check_restart_wrapper`, `_install_restart_wrapper`; `_configure_vm` now handles all three install steps with prompts
- `errander/config/add_target.py` — same wrapper functions; post-save install loop per new VM
- `tests/config/test_inventory_wizard.py` — remove intent-only test; update `_make_env` helper

## Previous Phase
**Wizard prompt clarity — backup_verify and critical_services (2026-06-09, COMPLETE).**

Added inline explanations to two confusing wizard prompts: `backup_verify` now explains it is read-only (does NOT create backups, checks file existence + age on disk) and what `backup:` in settings.yaml means; `critical_services` now explains the watch-only role and explicitly distinguishes it from `service_restart`.

### Files changed (2026-06-09 — wizard prompt clarity)
- `errander/config/inventory_wizard.py` — `backup_verify` and `critical_services` prompt text

## Previous Phase
**`add_target.py` UX improvements (2026-06-09, COMPLETE).**

`add_target.py` now asks the same three questions as the full wizard when adding a new VM:
numbered OS family menu (ubuntu/debian/rhel), Docker installed question (only when `docker_hygiene` is enabled in the env), and service_restart intent question. Builds the target dict with overrides (`docker_hygiene: {enabled: false}` or `service_restart: {enabled: false, restartable_units: []}`) when appropriate. Switched from `yaml.dump` (comment-stripping) to `ruamel.yaml` for comment-preserving round-trips.

### Files changed (2026-06-09 — add_target.py UX)
- `errander/config/add_target.py` — numbered OS menu; docker/service_restart questions; ruamel.yaml I/O

## Previous Phase
**Approval surface wording — replace "Slack approval" with "human approval (Slack or Web UI)" (2026-06-09, COMPLETE).**

Every user-facing description that implied Slack was the only approval channel was updated to accurately reflect both surfaces (Slack reactions + Web UI). 12 instances across 7 files. No behaviour change — docs/UI text only.

### Files changed (2026-06-09 — approval surface wording)
- `AGENTS.md` — opening line + risk tier table
- `CLAUDE.md` — risk tier table
- `README.md` — action table, CLI comment, safety gates table
- `errander/config/inventory_wizard.py` — approval policy menu + generated YAML comments + patching comment
- `errander/main.py` — `--help` text, docstring, terminal `print()` operators see
- `errander/web/server.py` — admin panel label, Service Restart glossary chip, action execution note

## Previous Phase
**Enterprise inventory wizard + comment-preserving YAML (2026-06-09, COMPLETE).**

Replaced the bare 9-line bash inventory stub in `configure.sh` with a full Python interactive wizard (`errander/config/inventory_wizard.py`). Collects: environment name, SSH creds, maintenance window/days, per-env action toggles (5 actions), and per-VM details (host, name, OS family, tags, critical services, optional service_restart units). Generates a richly annotated `inventory.yaml` with inline comments on every field and all optional sections present-but-commented. Also fixed `errander/config/configure.py` to use `ruamel.yaml` for comment-preserving round-trips on `node_exporter:` updates (previous `yaml.safe_load + yaml.dump` stripped all comments). 20 new tests.

### Files changed (2026-06-09 — inventory wizard)
- `pyproject.toml` — added `ruamel.yaml>=0.18` dependency + mypy override
- `errander/config/inventory_wizard.py` — NEW: full interactive wizard + YAML renderer + helpers
- `scripts/configure.sh` — removed bash VM loop (lines 193–291) + bash YAML generation; step 2 now calls Python wizard; reads result vars from `~/.errander_wizard_result`
- `errander/config/configure.py` — `_update_inventory_yaml` uses ruamel.yaml round-trip (preserves comments)
- `tests/config/test_inventory_wizard.py` — NEW: 20 tests (render, schema validation, ruamel round-trip, helpers)
- `docs/learning/52-configure-wizard.md` — NEW: learning doc
- `SETUP.md` — Step 5 configure.sh description updated for new wizard behavior

## Previous Phase
**Per-target `actions:` support (2026-06-09, COMPLETE).**

`actions:` was previously env-level only — all VMs in an environment shared the same `docker_hygiene.enabled` and `restartable_units`. Added per-target `actions:` to `TargetSchema` with a `resolve_actions(env_actions)` method that merges target overrides on top of env defaults. Updated all three fan-out paths in `graph.py` to use per-target resolved values from the target dict (batch-level env values remain as fallback for DB-added VMs). Fixed `run_check_targets` and `run_restart_service` to validate per-VM resolved config instead of env-level. Added validation in `EnvironmentSchema` for per-target docker_hygiene/service_restart contradictions. 11 new tests.

### Files changed (2026-06-09)
- `errander/config/schema.py` — `TargetSchema`: `actions:` field + `resolve_actions()` + per-target validation in env validator
- `errander/main.py` — `yaml_targets` loop with per-target `enabled_actions`/`docker_command_mode`; `run_check_targets` + `run_restart_service` use per-VM resolved config
- `errander/agent/graph.py` — `validate_targets_node`, `route_plan_vms`, `make_simple_fan_out`, `make_wave_dispatcher` all use per-target values
- `example/inventory.yaml` — per-target `actions:` header docs + production env examples
- `SETUP.md` — step 5b/5c rewritten with per-target syntax and "why per-target" callout
- `tests/config/test_schema_actions.py` — `TestPerTargetActions` (11 tests)

## Previous Phase
**configure.sh security hardening + add-target.sh new-environment support (2026-06-09, COMPLETE).**

Fixed 6 security issues in configure.sh: (1) `ERRANDER_ELK_API_KEY` written plaintext despite encryption being enabled — now goes through `encrypt_val`; (2–5) four API key prompts used `prompt_val` (visible) instead of `prompt_secret` (hidden) — vLLM API key, "Other" provider API key, ELK API key (both new-entry and re-entry paths); (6) `ERRANDER_SIGNING_SECRET` never generated — docker_hygiene web approval URLs would silently fail or crash at runtime. `ERRANDER_WEB_BASE_URL` auto-detected from the VM's primary IP (no prompt — it's always this VM; override in `.env` if behind NAT/LB). Extended `add_target.py` with `[n] New environment` option so operators can add a brand-new env without re-running the full configure.sh wizard.

### Files changed (2026-06-09)
- `scripts/configure.sh` — `ERRANDER_ELK_API_KEY` encrypted; 4 API key prompts → `prompt_secret`; SIGNING_SECRET auto-generation; WEB_BASE_URL silently auto-detected from VM's primary IP; both written to `.env` with encryption
- `errander/config/add_target.py` — `[n] New environment` option; prompts for all env-level fields; removed stale `type: ignore` comment

## Previous Phase
**`/ui/monitoring` time-range selector + Prometheus+Grafana demoted to external-only (2026-06-08, COMPLETE).**

Added a 24h / 7d / 30d time-range toggle to `/ui/monitoring` — all sections (stat cards, approval funnel, safety signals, audit trail charts) respond to the selected window by passing it to `get_monitoring_stats()`. Removed the Prometheus + Grafana install prompt from `bootstrap.sh` and reframed both stacks as optional, dedicated-external-VM-only tools in all docs. Reasoning: the built-in page reads from the audit DB (authoritative, survives restarts, has approval/safety data Prometheus never sees); running Prometheus + Grafana on the same server adds RAM pressure and disk growth with no meaningful gain over the built-in page.

### Files changed (2026-06-08)
- `errander/observability/metrics.py` — `_ui_monitoring()`: `?days=` query param (1/7/30), `get_monitoring_stats(daily_days, summary_days)` call, `_tr_btn()` helper, toggle HTML, dynamic window labels on all sections; toggle CSS (`.tr-sel`, `.tr-btn`, `.tr-btn.on`)
- `scripts/bootstrap.sh` — removed Prometheus + Grafana install block; updated header comment; updated Done banner
- `SETUP.md` — removed Prometheus prompt mention from Step 1A; reframed "Monitoring stack" section as external-VM-only
- `README.md` — updated tech stack table; reframed "Installing monitoring stack" section
- `SETUP-Win-Controller.md` — updated monitoring section to external-VM-only
- `docs/MONITORING-VALIDATION.md` — recorded decision: built-in sufficient, comparison not required

## Previous Phase
**Monitoring page gap-fill — approval funnel, safety signals, duration averages (2026-06-05, COMPLETE).**

Filled three observability gaps in `/ui/monitoring` that were documented in `docs/OBSERVABILITY.md` but not yet surfaced: (1) approval funnel — 4 stat cards showing requested/approved/rejected/timed-out with response rate %; (2) safety & health signals — 30-day counts of drift detections, preflight blocks, reboot required, service regressions, SSH anomalies; (3) performance section — avg batch duration, avg approval wait, and avg per-action-type duration from Prometheus histograms. Page now covers every observability surface except LangSmith (Layer A external tracer) and raw logs (ELK/Loki).

### Files changed (2026-06-05 — monitoring gap-fill)
- `errander/safety/audit.py` — `get_monitoring_stats()` extended: two new SQL queries (approval funnel + safety signals), two new return keys (`approvals`, `safety`)
- `errander/observability/metrics.py` — `_hist_avg()` + `_hist_avg_by_label()` helpers, `_read_prom_counters()` extended with histogram averages, `_ui_monitoring()` extended with approval cards, safety section, performance section

## Previous Phase
**Controller Monitoring page — built-in `/ui/monitoring` with Chart.js visualizations (2026-06-05, COMPLETE).**

Adds a `Monitoring` nav item and `/ui/monitoring` page to the Errander web UI. Two data sources: (1) audit DB aggregate queries for persistent history; (2) in-process Prometheus counter reads for live stats since last restart. Charts rendered with Chart.js 4.4 via CDN. No Prometheus+Grafana install required.

### Files changed (2026-06-05 — Controller Monitoring)
- `errander/safety/audit.py` — new `get_monitoring_stats()` method
- `errander/observability/metrics.py` — monitoring CSS, `_ACTION_COLORS`, `_read_prom_counters()`, `_build_chart_json()`, `_ui_monitoring()` handler, sidebar nav entry, route registration

## Completed (summary)
- v1.0: Full agent scaffold, LangGraph orchestration, all 6 actions, safety gates, rollback, Slack approval, audit trail, Web UI
- v1.1: docker_hygiene replaces docker_prune — rich assessment, object-level approval (dual surface: Slack + web), per-object audit
- v1.2–v1.5: Extended docker_hygiene scope (unused images, volumes, build cache)
- AI Trust Layer: decision explainability, context budget/redaction, prompt versioning, source citation, prefix caching
- Web UI: login page, session auth, full fleet dashboard, approvals, batches, AI decisions, monitoring
- Bootstrap: two-phase install (bootstrap.sh + configure.sh), Windows controller doc, teardown.sh
- Observability: `/ui/monitoring` — all OBSERVABILITY.md surfaces covered except LangSmith + raw logs

## Next Up — §8d Master Roadmap (Fable 5 review, 2026-06-10)

| # | Step | Status |
|---|---|---|
| 0 | CI (pytest + ruff + mypy + gitleaks) | ✅ COMPLETE |
| 1 | R4: PostgreSQL dual-backend + DB layer | next |
| 2 | R3 keystone: `approval_requests` DB-backed store | after 1 |
| 3 | R2: users/groups RBAC + web-only approval | after 2 |
| 4 | R3: process split (two OS processes, key isolation) | after 3 |
| 5 | R1: advisory-LLM batch planning (F2+F6 fix) | after 4 |
| 6 | Plan A: investigation agent | after 5 |
| 7 | Plan B: dashboard chat | after 6 |

## Blockers
None.

## Test count
2626 passing (full suite); 2450 in CI (excluding tests/ui Playwright + tests/staging).
