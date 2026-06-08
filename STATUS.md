# Errander-AI — Project Status

## Last Updated
2026-06-08

## Current Phase
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

## Next Up (roadmap order)
1. **Prometheus test on real VM** — run `install-prometheus.sh` on a dedicated monitoring VM, verify targets UP (not agent VM)
2. **LangSmith wiring** — set `LANGCHAIN_*` in dev/staging, confirm traces, add learning doc
3. **Layer A Investigation Agent** — implementation (`tasks/investigation-agent-implementation-plan.md`)
4. **Dashboard Chat** — `/ui/chat` ops-console (after #3)

## Blockers
None.

## Test count
2506 passing.
