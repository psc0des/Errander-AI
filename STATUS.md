# Errander-AI — Project Status

## Last Updated
2026-06-22

## Current Phase
**Agent Workflow diagram — added the missing Layer A lane (COMPLETE 2026-06-22).**

Follow-up to the doc accuracy sweep below: owner pointed out the Glossary page's
interactive "Agent Workflow" diagram only showed the Level-1/2/3 **batch maintenance**
graph (APScheduler → ... → Report) — it had no representation of the Investigation
Agent, Dashboard Chat, or Prometheus/AI-Decisions observability, all shipped this
session. Added a clearly separated "LAYER A — ASK & CHAT" lane below the existing
pipeline (dashed pink border/edges, own section divider, own legend entry) rather than
interleaving nodes into the batch flow, since these are parallel, read-only entry points
— not sequential steps in that state machine.

- `errander/web/server.py` (`page_glossary()` / `GLOSS_CSS` / `_WF_JS`) — 4 new clickable
  nodes: **Ask (CLI)** (`--ask [--agentic]`), **Dashboard Chat** (`/ui/chat`),
  **Investigation Engine** (Operator Assistant ↔ Investigation Agent, with fallback
  behavior in its detail popup), **Metrics & AI Decisions** (`/metrics` + `/ui/ai-decisions`)
  — each with full click-to-expand detail (checks/onfail/code/note) mirroring the existing
  nodes' format
- Canvas extended 845px → 1060px; new `.wf-node-layer-a` (dashed pink), `.wf-dot-pink`,
  `.wf-section-divider` CSS; new legend entry "Layer A (read-only)"
- **Pre-existing duplication discovered (not touched, out of scope):** the workflow/glossary
  CSS is defined twice in `server.py` — once in the global `CSS` constant (line ~489,
  loaded in `<head>` via `layout()`) and again in `GLOSS_CSS` (loaded inline in the
  glossary page body). Confirmed via cascade order (GLOSS_CSS renders later in the DOM,
  same specificity → wins) that editing only `GLOSS_CSS` is sufficient; the global copy is
  dead weight for this page but removing it is a separate refactor, not done here.

**Verification:** `ruff`/`mypy` clean, glossary smoke test passes, confirmed live via curl
against the running demo server — all 4 new node IDs and the "LAYER A — ASK" divider text
render in the HTML.

**Not yet committed** — awaiting owner go-ahead (the prior doc-sweep commit `c5a5d43`
predates this addition).

## Previous Phase
**Doc accuracy sweep — Glossary, README, CLAUDE.md, AGENTS.md, docs/SPEC.md (COMPLETE 2026-06-22).**

Owner noticed (while reviewing the demo) that several docs hadn't caught up with the
v1.1 `docker_hygiene` cutover and the web UI Glossary page had stale terms. Findings:

- **Web UI Glossary** (`errander/web/server.py`): page order swapped per owner preference
  — `page_glossary()` now returns `workflow_section + grid_section` (Agent Workflow
  diagram + legend on top, term cards at the bottom; was reversed). `_GLOSS` updated:
  "Docker Prune" entry replaced with "Docker Hygiene" (was describing a removed v1.1
  action); added 3 missing terms for shipped-but-undocumented features — "Investigation
  Agent", "Dashboard Chat", "Planning Note".
- **`CLAUDE.md`** had a real internal contradiction: its own Risk Tiers / Rollback Tiers
  tables still said "Docker prune" while its Domain Rules section (3 subsections below)
  correctly states `docker_prune` was fully removed in v1.1. Fixed both table rows.
- **`AGENTS.md`** (516 lines, meant to mirror CLAUDE.md for non-Claude AI tools) had
  drifted to a snapshot that predated docker_hygiene, R3 process separation, and RBAC
  entirely — `diff` showed it 100% different from current CLAUDE.md. Replaced with an
  exact copy of the now-corrected CLAUDE.md (confirmed byte-identical, confirmed no
  Claude-Code-specific content exists in CLAUDE.md's body to strip).
- **`docs/SPEC.md`** (1714 lines, "kept for lineage" per its own header) — added as-built
  notes at every point where the original design materially diverged and wasn't already
  flagged: §5.2 Docker Prune (renamed heading to "historical", full as-built note pointing
  to `docker_hygiene.py`), Risk/Rollback Tiers tables, Dry-Run detail table, policy YAML
  blocks (`docker_prune_all` is an obsolete key), §10 LLM Integration ("Action Planning &
  Prioritization" no longer reorders/filters the plan post-R1; "Failure Analysis" function
  was deleted entirely; "Natural language querying of audit trail" shipped early as the
  Investigation Agent + Dashboard Chat, not still V2-deferred), §2 Network Architecture
  (R3 process split not reflected in the diagram), §16 Agent Lifecycle (mermaid diagram
  still said "Start Slack poller", contradicting its own prose 3 lines below), Appendix
  directory tree (`docker_prune.py` → `docker_hygiene.py`, pointer to CLAUDE.md for the
  current tree). Preserved the doc's own "original design, kept for lineage" framing —
  did not rewrite the historical command-level detail in §5.2/§9/§13, only added
  signposting so a reader can't mistake lineage for current behavior.
- **`README.md`**: one stale "docker prune" mention in the graph-isolation bullet (§Three-
  Level Graph Structure) fixed to "docker hygiene".

**Verification:** `uv run ruff check .` clean, `uv run mypy errander/` clean (114 files),
`tests/ui/test_web_server_smoke.py::test_page_glossary_renders` passes. Manually verified
via the running demo server (logged in, fetched `/ui/glossary`, confirmed "Agent Workflow"
renders before "Glossary" in the HTML and all 4 refreshed terms are present).

**Not yet committed** — owner has not asked for a commit yet this session.

## Previous Phase
**Plan B — Dashboard Chat, phase 1 (CODE COMPLETE 2026-06-22, awaiting owner manual test pass).**

`/ui/chat` (opt-in, `ERRANDER_CHAT_ENABLED`, default off) — a multi-turn web
console over the **same** Plan A investigation engine, not a second brain.
Threads are per-user (`ChatStore`, migration #16); each turn folds prior
messages into the question text (a deliberate v1 simplification — the
engine's `question: str → AssistantResponse` contract is unchanged),
redacts, calls `InvestigationAgent.investigate_agentic()` or
`OperatorAssistant.investigate()` depending on settings, then persists +
renders the answer with citations. CSRF is enforced by the existing global
middleware with zero new per-handler code; ownership is. Logs a second,
chat-specific AI-decision row (`decision_type="dashboard_chat_turn"`) per
turn alongside whatever the engine itself logs.

The biggest as-built gap the source plan didn't anticipate: the web process
had **zero** LLM/Prometheus/ELK/disk-history/baseline wiring — only
`AuditStore` and friends. `errander/web/__main__.py` now constructs all of
these (gated on `settings.chat_enabled`, so the cost is zero when chat is
off), bundled under one `ChatEngineDeps` app-state object rather than six
more individual `AppKey`s. A second gap: the engine needs
`inventory: InventoryConfig` but the web process only had
`base_inventory: list[VMTarget]` (a different loader, different shape) —
chat now calls `validate_inventory()` separately.

Phase 1 only — no streaming (SSE), no "propose an action → approval flow"
handoff. Both deferred to later phases per the source plan.

This completes the two-plan sequence from the wrist-injury sprint decision
(`tasks/lessons.md`, 2026-06-22): Plan A + Plan B are both code-complete with
automated tests green; **manual testing against a real LLM endpoint and
real browser use is still the owner's pending step**, once recovered.

### Files changed (2026-06-22 — Plan B: dashboard chat)
**Code:**
- `errander/safety/chat_store.py` — NEW: `ChatStore`/`ChatThread`/`ChatMessage`, mirrors `ApprovalRequestStore`'s shape minus the approval-race machinery
- `errander/safety/migrations.py` — migration 16 (`chat_threads`, `chat_messages`)
- `errander/web/ui.py` — `ChatEngineDeps`, `CHAT_STORE_KEY`/`CHAT_ENGINE_DEPS_KEY`, `_ui_chat`/`_ui_chat_thread`/`_ui_chat_new_thread`/`_ui_chat_message_post`, nav entry, chat CSS, threaded through `build_ui_app`/`start_web_server`
- `errander/web/__main__.py` — constructs `ChatStore` + `ChatEngineDeps` (LLM/Prometheus/ELK clients, disk-history/baseline stores, `InventoryConfig`) gated on `settings.chat_enabled`; closes Prom/ELK aiohttp sessions on shutdown
- `errander/config/settings.py` — 3 new fields: `chat_enabled`/`chat_max_history_turns`/`chat_max_threads_per_user`

**Tests (53 new/extended):**
- `tests/safety/test_chat_store.py` — NEW (13): create/append/order, per-user scoping, ownership-checked delete
- `tests/web/test_chat.py` — NEW (9): disabled/not-configured notices (never a 500), auth redirect, CSRF enforcement, engine-call + render + persistence, cross-user ownership
- `tests/web/test_import_isolation.py` — +1: explicit proof the chat handler's lazy engine imports don't drag in execution code
- `tests/safety/test_migrations.py` — +1 new test, 2 hardcoded migration-count assertions updated (16→17 migrations)

**Verification:** `uv run ruff check .` clean; `uv run mypy errander/` clean (114 files); Plan B test scope (40 tests) + Plan A test scope, all green. Manual smoke test against the **real running `python -m errander.web` process** (not just `TestClient`): logged in, created a thread, posted a question, confirmed the deterministic-fallback answer rendered and persisted across a reload.

## Previous Phase
**Plan A — Layer A Investigation Agent (CODE COMPLETE 2026-06-22, awaiting owner manual test pass).**

Adds an opt-in agentic tool-calling loop (`--ask --agentic`,
`ERRANDER_INVESTIGATION_AGENT_ENABLED`, default off) alongside the existing
deterministic `OperatorAssistant.investigate()` — both untouched, both
produce the same `AssistantResponse` contract. `InvestigationAgent.investigate_agentic()`
gives the LLM a bounded ReAct loop over six read-only tools
(`query_prometheus`, `search_logs`, `get_audit_events`, `get_disk_trend`,
`get_vm_facts`, `list_inventory`), capped by `max_tool_calls` (default 8) and
an overall `timeout_seconds` deadline (default 180 — deliberately larger
than `LLMClient`'s 60s per-call timeout) with a shrinking per-call timeout.
Falls back cleanly to the deterministic path on any failure (unsupported
endpoint, turn-1 zero-tool-calls, LLM down mid-loop, budget exhausted,
unexpected exception) — never raises, never blocks.

Built per a plan independently reviewed by a second model (Opus 4.8) before
implementation; the review found 4 correctness gaps in the original draft,
all fixed during this build: (1) citation validation against
internally-tracked source IDs the model can never see — fixed by embedding
`[source_id=...]` in each tool result message; (2) the web-wiring inventory
type mismatch (deferred to Plan B); (3) per-hop audit rows would have grown
quadratically — fixed by logging only the hop's delta; (4) a silently-wrong
answer on an endpoint that ignores `tools=` — fixed by treating turn-1
zero-tool-calls as a capability failure, not a lucky answer. A 5th bug
(`call_tools or tools` re-offering the full tool list right after
"exhausting" the budget) was caught during implementation itself, not the
review — see `tasks/lessons.md`.

This is **Plan A of a two-plan sequence** (owner fractured their wrist
2026-06-22, decided: code now without hands-on testing, owner tests once
recovered — see `tasks/lessons.md`). **Plan B (Dashboard Chat) builds on
this engine next**, before any manual test pass — see "Files changed" below
and the approved plan for the full two-plan scope.

### Files changed (2026-06-22 — Plan A: investigation agent)
**Code:**
- `errander/agent/investigation_agent.py` — NEW: `InvestigationAgent.investigate_agentic()`, tool registry (6 tools), the ReAct loop, per-hop audit logging, fallback metric
- `errander/integrations/llm.py` — NEW `LLMClient.complete_with_tools()` (`ToolCallRequest`/`ToolCallResult` dataclasses), module-level `asyncio.Semaphore(1)` for self-hosted sequential-call safety; `complete()` untouched
- `errander/integrations/prometheus.py` — NEW `PrometheusClient.query()` (arbitrary PromQL); `fetch_vm_metrics()` untouched
- `errander/integrations/elk.py` — NEW `ElkClient.search()` (arbitrary terms); `fetch_vm_errors()` untouched
- `errander/config/settings.py` — 3 new fields: `investigation_agent_enabled`/`_max_tool_calls`/`_timeout_seconds`
- `errander/observability/metrics.py` — `INVESTIGATION_TOOL_CALLS_TOTAL`, `INVESTIGATION_FALLBACK_TOTAL`
- `errander/main.py` — `--agentic` CLI flag; `run_ask_query()` branches on flag + setting

**Tests (109 new/extended):**
- `tests/agent/test_investigation_agent.py` — NEW (13): multi-hop success + citation handling, budget-cap forced-final-answer, timeout, capability detection (unsupported / empty-turn1 / llm-down), malicious tool-result redaction, defensive clamp, never-raises
- `tests/agent/test_investigation_tools.py` — NEW (26): per-tool read-only/validation/caps
- `tests/agent/test_investigation_agent_isolation.py` — NEW (1): Layer-A isolation, written from scratch (no prior operator_assistant-scoped isolation test existed to mirror)
- `tests/integrations/test_llm.py` — +11: `TestCompleteWithTools`
- `tests/integrations/test_prometheus.py` — +6: `query()`
- `tests/integrations/test_elk.py` — +7: `search()`

**Verification:** `uv run ruff check .` clean; `uv run mypy errander/` clean (113 files); `uv run pytest tests/agent/ tests/integrations/ tests/ai_evals/test_golden_plans.py` — 1015 passed (zero regression to the deterministic batch path); manual CLI smoke test (`--ask --agentic` with flag off, with flag on + no LLM configured) confirms both fallback paths render correctly. **Not yet manually tested against a real LLM endpoint** — that's the owner's pending verification step once recovered.

## Previous Phase
**§8d Step 5 — R1: advisory-LLM batch planning (COMPLETE 2026-06-14).**

`prioritize_actions()` is now 100% deterministic — always `_hardcoded_priority(available_actions, vm_info)`. The LLM can no longer add, remove, or reorder plan actions (fixes F2 — silent plan shrinkage). A new, separate Layer A call, `generate_planning_note()`, produces a short (≤700 char) informational note about the already-finalized plan — stored as `ai_note` inside each per-VM `vm_plans` dict, so it's covered by the existing plan hash and is part of the immutable approval artifact / deferred-replay record. The note never feeds back into plan content: `TestGoldenPlanSafety::test_planning_note_llm_output_never_changes_plan` proves the plan is byte-identical regardless of LLM presence or output. F4 sweep in the same change: deleted `analyze_failure()`, `_FailureAnalysis`, `_build_failure_prompt()`, `_check_failure_analysis`, `_VALID_RECOMMENDATIONS`, and the dead "3.2b policy enforcement" block (computed a filtered plan and then discarded it).

`ai_note` renders on the web approval page (`_render_approval_plan`, new `.apv-ai-note` block, HTML-escaped) and in the Slack plan summary (`_format_plan_for_approval`, appended after the approval instructions so Slack's ~2800-char truncation only ever costs the note). `ai_decisions` gains `decision_type="planning_note"` / `prompt_template_id="planning_note_v1"` (outcomes `success`/`fallback`/`no_llm`), mirroring the old `prioritize_actions` audit rows 1:1.

### Files changed (2026-06-14 — §8d Step 5 R1 advisory planning note)
**Code:**
- `errander/agent/decisions.py` — `prioritize_actions()` always `_hardcoded_priority` (dropped `llm_client`/`policy`/`ai_decision_store` params); new `_PlanningNote`, `_PLANNING_NOTE_MAX_CHARS=700`, `_sanitize_note()`, `_build_planning_note_prompt()` (renamed from `_build_prioritize_prompt`), `generate_planning_note()`; deleted `_PrioritizedActions`, `_FailureAnalysis`, `analyze_failure()`, `_build_failure_prompt()`, the dead policy-filter block, `BUILTIN_POLICIES` import
- `errander/agent/graph.py` — `plan_vm_node` calls `generate_planning_note()` after the deterministic plan, stores `ai_note` when non-empty; `_format_plan_for_approval` appends an "AI analysis — informational only" section per VM; dropped unused `env_policy` local
- `errander/agent/vm_graph.py` — `plan_actions_node` simplified to `prioritize_actions(vm_info)`
- `errander/web/ui.py` — `_render_approval_plan` renders `.apv-ai-note` (HTML-escaped) when present + matching CSS
- `errander/evals/replay.py` — new `_check_planning_note` (missing/empty/over-cap); removed `_check_failure_analysis`/`_VALID_RECOMMENDATIONS`
- `errander/safety/ai_audit.py` — docstring: `analyze_failure` → `planning_note`

**Tests (net +9: 6 fallout fixes + 3 new):**
- `tests/agent/test_decisions.py` — `TestAnalyzeFailure` deleted; redaction tests migrated to `generate_planning_note`
- `tests/agent/test_plan_vm_stored_signals.py` — `_build_prioritize_prompt` → `_build_planning_note_prompt(vm_info, plan, signals)`
- `tests/ai_evals/test_golden_plans.py` — new F2 regression `test_planning_note_llm_output_never_changes_plan`; removed LLM-filtering/exception tests now structurally impossible; `TestAIDecisionAudit` → `TestPlanningNoteAudit`
- `tests/ai_evals/test_adversarial.py` — removed `TestLLMExceptionFallback`/`TestAuditOutcomesOnErrors` (moot — `prioritize_actions` no longer takes `llm_client`); kept `_INJECTION_RE`/`_parse_action_types` payload tests
- `tests/ai_evals/test_replay.py` — `planning_note` assertion tests replace `failure_analysis` tests
- `tests/agent/test_approval_message_p01.py` — +2 tests: `ai_note` section shown/absent in Slack message
- `tests/web/test_approval_ai_note.py` — NEW (3 tests): `ai_note` rendered/absent/HTML-escaped on web approval page
- `tests/integrations/test_llm.py`, `tests/chaos/test_fault_injection.py` — fallout: migrated to `generate_planning_note` (6 tests, were failing pre-fix)

**Verification:** `uv run ruff check errander/ tests/` clean; `uv run mypy errander/` clean (112 files); full suite 8 failed / 2476 passed / 171 errors (485.93s) — the 8 failures + 171 errors are pre-existing and unrelated (confirmed via `git stash` reproducing identically on pre-R1 HEAD; see `tasks/lessons.md`).

### Infra — automated Docker + Compose + PostgreSQL provisioning (2026-06-14)

`bootstrap.sh` now installs Docker Engine + Compose plugin (via `get.docker.com`), enables `docker.service`, and adds `errander-agent` to the `docker` group (new step 6/8; web-user and clone-repo steps renumbered to 7/8 and 8/8). `configure.sh` brings up local PostgreSQL automatically with `docker compose up -d --wait` (falls back to a `pg_isready` poll loop on older Compose) whenever the operator keeps the default `ERRANDER_AUDIT_DB_URL`; pointing at an external PostgreSQL server skips this entirely. `docker-compose.yml`'s `postgres` service gets `restart: unless-stopped` so it survives host reboots, and `deploy/errander-agent.service` / `deploy/errander-web.service` now declare `After=network.target docker.service` + `Requires=docker.service` (previously `After=...postgresql.service`, which implied a native systemd Postgres that nothing installs).

**Files changed:**
- `scripts/bootstrap.sh` — new step 6/8 "Docker + Docker Compose"; renumbered steps 0–8
- `scripts/configure.sh` — `docker compose up -d --wait` bring-up block for the default local DB URL
- `docker-compose.yml` — `restart: unless-stopped` on `postgres`
- `deploy/errander-agent.service`, `deploy/errander-web.service` — `After=docker.service` + `Requires=docker.service`
- `SETUP.md` — Step 1 bullet list, Step B note, Step 5 note, teardown note
- `docs/learning/59-docker-postgres-bootstrap.md` — new learning doc

**Verification:** `bash -n` on both scripts clean; `docker compose config` clean; `grep -rn "postgresql.service" deploy/ SETUP.md` shows no stray native-Postgres references. No `errander/` Python changes — no pytest/ruff/mypy impact. No fresh Linux VM available for true end-to-end testing in this session.

## Previous Phase
**§8d Step 4 — R3: process separation (COMPLETE 2026-06-13).**

✓ **Migration #15** (hygiene_approval_requests table + users.totp_secret column)
✓ **HygieneApprovalStore** (DB-backed hygiene approval, mirrors approval_requests pattern)
✓ **TOTP helpers** (`errander/web/totp.py`: RFC 6238 support)
✓ **Web UI extraction** (`errander/web/ui.py`: 3,300+ lines, all UI routes + auth/CSRF middleware)
✓ **Slim metrics server** (agent-side: `/metrics` + `/health` only, no UI routes)
✓ **TOTP login wiring** (admin users → TOTP challenge in public mode; pending-MFA cookie pattern)
✓ **Import isolation** (web process never imports execution/agent/vm_graph modules)
✓ **Deploy artifacts** (systemd units for both processes, nginx Mode 2 config, env-var split, bootstrap script)

**2494 tests green** (up from 2460): TOTP handlers + flows, import isolation, web entry smoke, TOTP settings page, admin bypass in public mode.

**Two processes, two OS users, key isolation enforced:**
- `errander-agent` runs `python -m errander` on 127.0.0.1:9090 (`/metrics`, `/health`). Has SSH keys. Can execute on targets.
- `errander-web` runs `python -m errander.web` on 127.0.0.1:9091 (approval UI). No SSH access, nologin shell. Can read audit DB. Behind nginx reverse proxy in production.

**Pending-MFA cookie pattern** (300s TTL, HMAC-signed) bridges password verification → TOTP challenge without schema changes. First-time setup generates + persists secret; recurring login validates against existing secret.

**Public mode** (nginx Mode 2): mandatory TOTP for admin group, IP allowlist, TLS hardening, rate limiting on `/ui/login` (5 req/min per IP).

### Files changed (2026-06-13 — §8d Step 4: web extraction, TOTP, deploy)
**Code:**
- `errander/web/ui.py` — NEW: production web UI (3,300 lines, extracted from metrics.py); handlers, auth middleware, CSRF, CSS, design system; TOTP login flow + settings page; `/ui/*` routes (login, dashboard, approvals, batches, inventory, settings, glossary, AI decisions, monitoring, hygiene, docker-hygiene approve)
- `errander/observability/metrics.py` — slim: 173 lines, only metrics + HTTP handlers (`/metrics`, `/health`, `start_metrics_server`)
- `errander/web/__main__.py` — production entry: argparse (port, bind, db-url, public-mode), stores init, async startup
- `errander/web/totp.py` — RFC 6238: `generate_secret`, `make_qr_uri`, `verify_code` (±30s window)
- `errander/main.py` — `HygieneApprovalStore` init + reconciler calls; trim unused imports
- `scripts/bootstrap.sh` — create errander-web system user (nologin), update steps 0–7

**Deploy:**
- `deploy/errander-agent.service` — systemd unit (User=errander-agent, EnvironmentFile)
- `deploy/errander-web.service` — systemd unit (User=errander-web, EnvironmentFile)
- `deploy/.env.agent.example` — LLM + Slack + SSH + DB secrets
- `deploy/.env.web.example` — DB + signing secret + web port + base URL
- `deploy/nginx-mode2.conf.example` — reference: TLS, HSTS, rate limiting, IP allowlist, upstream

**Tests:**
- `tests/observability/test_rbac.py` + 5 new tests (TestTOTPFlow): admin → TOTP, reader bypasses, code validation, flow not available in non-public mode
- `tests/web/test_import_isolation.py` — updated to handle pre-imported modules from earlier tests
- All 2494 tests: green

**Commit log:**
- `d6e8ed2` feat: R3 step 4 — extract web UI to errander/web/ui.py (process split)
- `2caefa7` feat: R3 step 4 — TOTP login wiring (admin MFA in public mode)
- `bbd158f` feat: R3 step 4 — deploy artifacts + bootstrap for two-process split

## Previous Phase
**§8d Step 3 — R2: users/groups RBAC + web-only approval (2026-06-12, COMPLETE).**

Slack lost its decision authority entirely (fable §8a): the only place a decision can be recorded is the authenticated Web UI, by a named user in a permission-carrying group. Migration #14 adds `users`/`groups`/`group_permissions`/`user_groups`/`sessions` (seeded `admin` + `reader`; a third group is plain INSERTs, never a migration). New `errander/safety/user_store.py`: scrypt password hashes (stdlib, params stored per hash), DB-backed sessions (cookie token hashed at rest, survives restarts, R3-process-split-ready), per-request group resolution so membership changes apply without restart. Server-side RBAC via `_require_permission` in the handlers — `decide_approvals` gates batch + hygiene decisions (signed URLs locate, sessions authorize), `manage_settings` gates settings/inventory POSTs. Every decision records `decided_by="ui:<username>"` + `decided_by_group`. All three Slack decision paths removed: gate reaction watcher, docker_hygiene thread-reply parser (volumes now report-only in v1 web approval — fail closed), and the service-restart CLI's reaction gate (now a durable store row + web decision + atomic execution claim; the reconciler gained a 120 s claim grace so cross-process executors keep their own approvals). Slack messages are notify-and-link (plan summary + `/ui/approvals` URL). CLI user management (`--user-add/--user-remove/--user-list/--user-set-groups/--user-set-password`, all audited with the acting OS user); one-time `ERRANDER_UI_USER/PASSWORD` → admin-account seed for existing deployments. Zero users = read-only UI on loopback, mutations 403, non-loopback bind refuses to start. TOTP + nginx Mode 2 deferred to Step 4 (R3). 2,460 tests green on PostgreSQL (40 new: user store 23 + RBAC end-to-end 17; reaction/reply-channel tests removed), ruff + mypy clean.

### Files changed (2026-06-12 — §8d Step 3 R2 web-only approval + RBAC)
- `errander/safety/migrations.py` — migration #14 (users/groups/group_permissions/user_groups/sessions); `SEED_GROUPS_SQL` + `seed_default_groups()` applied on every run
- `errander/safety/user_store.py` — NEW: `User`/`UserStore`/`SessionStore`, scrypt hashing, permission constants
- `errander/models/events.py` — `USER_CREATED`/`USER_DELETED`/`USER_GROUPS_CHANGED`/`USER_PASSWORD_CHANGED`
- `errander/observability/metrics.py` — auth middleware rewritten on DB users/sessions (`?next=`, zero-users mode, loopback gate); `_require_permission`; login/logout against stores; decide + hygiene + settings/inventory handlers RBAC-gated; `decided_by_group` recorded + shown; `/ui/approvals` lists pending hygiene approvals with self-generated signed links; `build_ui_app()` factory extracted
- `errander/safety/approval_store.py` — `decide(decided_by_group=...)`
- `errander/safety/approval.py` — `poll_approval`/`watch_slack_reactions` deleted; `request_approval` = notify-and-link (web link + timeout note)
- `errander/agent/graph.py` — gate posts notify+link, no watcher; `approval_poll_interval_seconds` threading removed
- `errander/agent/vm_graph.py` — hygiene reply poller removed (web decision only)
- `errander/safety/hygiene_approval.py` — reply parser/poller deleted; formatter is notify-and-link, volumes marked report-only in web UI
- `errander/integrations/slack.py` — `get_reactions`/`conversations_replies`/reaction constants removed (post-only)
- `errander/config/settings.py` + `errander/config/schema.py` — `approval_poll_interval_seconds` removed (YAML key accepted-but-ignored); `ui_user/ui_password` = seed-only
- `errander/main.py` — reconciler pass 2 deleted + `_RECONCILER_CLAIM_GRACE_SECONDS`; restart CLI on the durable store (Slack optional); `run_user_management` + `--user-*` flags; startup user/session store wiring + legacy-credential seed
- `errander/config/inventory_wizard.py`, `errander/web/server.py`, `errander/web/evidence.py` — stale approval wording in generated comments/demo copy
- `tests/conftest.py` — re-seed default groups after per-test TRUNCATE
- Tests: `tests/safety/test_user_store.py` (NEW), `tests/observability/test_rbac.py` (NEW); rewrites: `tests/safety/test_approval.py`, `tests/safety/test_hygiene_approval.py`, `tests/agent/test_service_restart_cli.py`, `tests/observability/test_ui_security.py`, `tests/test_approval_reconciler.py` (grace tests), `tests/agent/test_hygiene_orchestration.py`, `tests/integrations/test_slack.py`, `tests/config/test_settings.py`, `tests/safety/test_migrations.py`, `tests/agent/test_graph.py`, `tests/ui/test_ui_auth_playwright.py`; deleted: `tests/safety/test_hygiene_reply_polling.py`
- Docs: fable.md (§8a checklist + §8d row 3 DONE), README.md, CLAUDE.md, AGENTS.md, SETUP.md (user bootstrap + mobile VPN + Slack scopes), RUN.md (user CLI + approval flow), docs/SPEC.md (reaction flow marked historical), docs/langgraph-primer.md, docs/OBSERVABILITY.md, docs/SECRETS.md, docs/learning/57-web-only-approval-rbac.md (NEW) + README index + 14 banner, STATUS.md, tasks/todo.md, tasks/lessons.md, docs/command-log.md

## Previous Phase
**§8d Step 2 — R3 keystone: durable `approval_requests` store (2026-06-11, COMPLETE).**

Approvals moved from the in-memory `ApprovalManager` (lost on restart — fable finding F9) to a DB-backed `ApprovalRequestStore` (migration #13). The approval gate persists the pending row durable-first, then posts to Slack; a transitional background watcher writes ✅/❌ reactions into the store; the web UI writes its decisions into the same store via atomic `UPDATE ... WHERE status='pending'` (exactly one decider wins). A 60-second restart reconciler expires overdue requests, resumes Slack watchers for orphaned pending rows, and executes approved-but-unclaimed batches through the exact-artifact replay path — guarded by an atomic execution claim (`mark_execution_started`) so no batch can run twice. Rode along: the latent deferred-replay hash bug fix (`preloaded_batch_id` — every replay previously aborted because a fresh batch_id broke the plan hash) and exact-object continuity (per-item selections now survive defer/restart via `approved_items_json`). `ApprovalManager`/`PendingApproval`/`await_dual_approval` deleted. GitHub Actions bumped off Node-20-deprecated majors (checkout@v5, setup-uv@v7).

### Files changed (2026-06-11 — §8d Step 2 approval_requests store)
- `errander/safety/migrations.py` — migration #13: `approval_requests` table
- `errander/safety/approval_store.py` — NEW: `ApprovalRequestStore` + `ApprovalRequest` (atomic decide/claim, expire_overdue, wait_for_decision poll+event hybrid)
- `errander/safety/approval.py` — `ApprovalManager`/`PendingApproval`/`await_dual_approval`/`BatchApprovalResult` deleted; `watch_slack_reactions` added (transitional R3 channel)
- `errander/agent/graph.py` — gate rewritten on the store (durable-first, claim before execution); `preloaded_batch_id` in state + `init_batch_node`; `build_operator_approved_packages` helper
- `errander/main.py` — `_approval_reconciler` + 60 s interval job; `ApprovalRequestStore` wiring; `preloaded_batch_id`/`preloaded_approved_items` through `run_env_batch` and `_window_opener`
- `errander/scheduling/scheduler.py` — `add_interval_job` (max_instances=1)
- `errander/observability/metrics.py` — `_APPROVAL_STORE_KEY`; all handlers + `_ui_approval_decide` repointed at the store (`decided_by="ui:<username>"`)
- `errander/web/providers.py` + `errander/web/server.py` — `refresh(approval_store=...)`
- `.github/workflows/ci.yml` — checkout@v5, setup-uv@v7
- Tests: `tests/safety/test_approval_store.py` (NEW, incl. AC4 decide race), `tests/test_approval_reconciler.py` (NEW, AC3 restart recovery), rewrites in `tests/safety/test_approval.py`, `tests/agent/test_plan_apply_flow.py`, `tests/agent/test_graph.py`, `tests/agent/test_deferred_replay.py` (+ hash-fix lock-in), `tests/safety/test_deferred_artifact.py`, `tests/chaos/test_fault_injection.py`, `tests/test_main.py`, `tests/ui/test_approval_ui.py`, `tests/ui/test_approvals_playwright.py`
- Docs: fable.md §8b/§8d, CLAUDE.md + AGENTS.md architecture, README.md (approval flow + structure), docs/langgraph-primer.md (approval flow rewrite), docs/learning/56-approval-requests-store.md (NEW), tasks/todo.md, tasks/lessons.md, docs/command-log.md
- Doc sweep (2026-06-12 follow-up): RUN.md + SETUP.md live-mode table (ApprovalManager → durable store), docs/SPEC.md §9 as-built note, superseded banners on learning docs 14 + 50, learning README index completed through 56, tasks/dashboard-chat + investigation-agent plans repointed at `ApprovalRequestStore`, tasks/todo.md Project E trigger marked covered

## Previous Phase
**PostgreSQL-Only Migration (2026-06-10, COMPLETE).**

Owner decision: drop SQLite entirely — one standard, less headache for users (supersedes the §8c dual-backend "Grafana model"). `AsyncDatabase` now rejects non-Postgres URLs; all dialect branches removed; DDL written in Postgres flavor (INTEGER→BIGINT for byte counts — int32 overflow caught on first run); LangGraph checkpointer moved to `AsyncPostgresSaver`; repo ships `docker-compose.yml` (postgres:16, `errander` + `errander_test` DBs) so clone → `docker compose up -d` → run stays zero-config. Test suite (2445 tests) runs against real Postgres via `make_test_db()` + per-test TRUNCATE isolation (~5 min). CI: single PostgreSQL test job (full suite) + web-role least-privilege verification. configure.sh asks for the PostgreSQL URL.

### Files changed (2026-06-10 — PostgreSQL-only)
- `errander/db/core.py` — Postgres-only URL normalization, ValueError on anything else; pools/dialect removed
- `errander/safety/migrations.py` — `run_migrations(conn)` (no dialect), `_adapt_ddl` deleted, BIGSERIAL/BIGINT DDL
- `errander/safety/audit.py`, `errander/web/providers.py` — STRING_AGG only; `errander/observability/startup_scan.py` — `id` ordering only
- `errander/commands/runs.py` — checkpoint probes ported from raw sqlite3 to AsyncDatabase/Postgres
- `errander/main.py` — AsyncPostgresSaver checkpointer (psycopg URL + `.setup()`)
- `errander/config/settings.py` — default `postgresql://errander:errander@localhost:5432/errander`
- `errander/web/server.py` — Postgres default fallbacks
- `pyproject.toml` — asyncpg core dep; `postgres` extra deleted; +langgraph-checkpoint-postgres, psycopg[binary]; −aiosqlite, −langgraph-checkpoint-sqlite
- `docker-compose.yml` — NEW; `deploy/postgres-init/01-create-test-db.sql` — NEW
- `tests/conftest.py` — `make_test_db()`, session migration fixture, autouse TRUNCATE cleanup
- ~40 test files — `AsyncDatabase(":memory:")` → `make_test_db()`; sqlite-path fixtures → TEST_DB_URL
- `.github/workflows/ci.yml` — single Test (PostgreSQL) job, full suite, postgres:16
- `scripts/configure.sh` — PostgreSQL URL prompt; `.env` writes the URL
- Docs: CLAUDE.md, AGENTS.md, README.md, SETUP.md (+ SQLite-migration note), example/settings.yaml, docs/OBSERVABILITY.md, docs/SECRETS.md, fable.md §8c superseded note, docs/learning/55-postgresql-only.md

## Previous Phase
**§8d Step 1 — R4: PostgreSQL Dual-Backend + DB Layer (2026-06-10, COMPLETE).**

Replaced all `aiosqlite` direct usage across 15 store files and 30+ test files with SQLAlchemy Core async (`text()` + named `:param` style) via a new `AsyncDatabase` wrapper class. Added dialect-aware migration runner with migrations #10-#12 folding in three previously-orphaned inline DDL blocks. Added Postgres CI job with role-grant verification. All 2446 tests pass (SQLite `:memory:`), mypy clean, ruff clean.

### Files changed (2026-06-10 — §8d Step 1 PostgreSQL dual-backend)
- `errander/db/__init__.py` — NEW: package marker
- `errander/db/core.py` — NEW: `AsyncDatabase` class (URL normalization, `begin()`, `dialect`, `close()`)
- `errander/safety/migrations.py` — ported to `AsyncConnection` + named params; `_adapt_ddl()` for SQLite→PG DDL; migrations #10 (settings_overrides/inventory_overrides), #11 (ai_decisions), #12 (deferred_executions)
- `errander/safety/audit.py` — ported to SQLAlchemy; `GROUP_CONCAT` → `STRING_AGG` dialect switch; `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`
- `errander/safety/batches.py` — ported; `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`
- `errander/safety/artifacts.py` — ported
- `errander/safety/agent_lease.py` — ported; `INSERT OR REPLACE` → `ON CONFLICT(id) DO UPDATE`
- `errander/safety/vm_state.py` — ported
- `errander/safety/baselines.py` — ported
- `errander/safety/disk_history.py` — ported
- `errander/safety/vm_facts.py` — ported; `GROUP_CONCAT` → dialect switch
- `errander/safety/deferred.py` — ported; `initialize()` calls `run_migrations()`
- `errander/safety/overrides.py` — ported; `initialize()` calls `run_migrations()`
- `errander/safety/ai_audit.py` — ported; inline ALTER TABLE guards removed
- `errander/observability/vm_metrics.py` — ported; `INSERT OR REPLACE` → `ON CONFLICT DO UPDATE`
- `errander/observability/durability.py` — ported
- `errander/observability/startup_scan.py` — ported; `rowid DESC` → dialect-aware `id` fallback
- `errander/evals/replay.py` — ported
- `errander/web/providers.py` — `GROUP_CONCAT` → `STRING_AGG` dialect switch
- `errander/web/server.py` — wrap `AuditStore` with `AsyncDatabase` in plan-show endpoint
- `errander/main.py` — construct one `AsyncDatabase`; pass to all stores
- `pyproject.toml` — add `sqlalchemy[asyncio]>=2.0`; add `postgres = ["asyncpg>=0.29"]` extra
- `tests/conftest.py` — `TEST_DB_URL` at module level; `session_db` + `async_db` fixtures
- `tests/safety/test_migrations.py` — use `TEST_DB_URL`; dialect-agnostic table introspection via `sa_inspect`
- `tests/safety/test_*.py` (12 files) — updated to use `AsyncDatabase`
- `tests/agent/test_*.py` (8 files) — updated to use `AsyncDatabase`
- `tests/ai_evals/test_*.py` (3 files) — updated to use `AsyncDatabase`
- `tests/observability/test_*.py` (3 files) — updated to use `AsyncDatabase`
- `tests/chaos/test_fault_injection.py` — updated; patch `store._db.begin`; fix noqa directives
- `tests/test_main.py` — updated to use `AsyncDatabase`
- `.github/workflows/ci.yml` — add `test-postgres` job with Postgres service + role-grant verification
- `deploy/postgres-setup.sql` — NEW: `errander_agent` + `errander_web` role grants for production

## Previous Phase
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
| 1 | R4: PostgreSQL-only + DB layer | ✅ COMPLETE (2026-06-10) |
| 2 | R3 keystone: `approval_requests` DB-backed store | ✅ COMPLETE (2026-06-11) |
| 3 | R2: users/groups RBAC + web-only approval | ✅ COMPLETE (2026-06-12) |
| 4 | R3: process split (two OS processes, key isolation, nginx Mode 2 + TOTP) | ✅ COMPLETE (2026-06-13) |
| 5 | R1: advisory-LLM batch planning (F2+F6 fix) | ✅ COMPLETE (2026-06-14) |
| 6 | Plan A: investigation agent | ✅ CODE COMPLETE (2026-06-22), awaiting owner manual test |
| 7 | Plan B: dashboard chat | ✅ CODE COMPLETE (2026-06-22), awaiting owner manual test |

## Blockers
None.

## Test count
Full suite: 2551 passed, 8 failed, 181 errors, verified 2026-06-22 (after Plan A + Plan B).
The 8 failures (`tests/ui/test_approval_ui.py`, needs a seeded user account) and 181 errors
(`tests/ui/*` + `tests/web/*` pytest-asyncio runner-state pollution when run together) are
pre-existing and unrelated to Plan A/B — the error count rose from 171 to 181 by exactly the
10 new tests added to `tests/web/test_chat.py` (already-affected directory), not a regression;
see `tasks/lessons.md`. ruff + mypy clean (114 source files).
