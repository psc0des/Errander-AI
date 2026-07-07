## Detect-and-Propose — genuinely agentic origination, HITL execution (2026-07-07, Phase 1 COMPLETE)

Owner decision: make the "agentic" in *supervised agentic AI* real — the agent notices
signals, investigates (read-only), and files evidenced **proposals** into the approval
pipeline; execution stays deterministic Layer B behind human approval. Refines (does not
reverse) the 2026-06-23 removal: the **chat surface stays out**; the **investigate→propose
loop comes into core** because it terminates in the core's own approval pipeline.

Full plan: `tasks/fable-plan.md`. Phase 2 adopts `tasks/investigation-agent-implementation-plan.md`
(runtime, tools, guardrails) with a re-aimed output (proposals) and trigger (probe events).
Diagrams (Mermaid, render on GitHub): pipeline in `docs/diagrams/detect-and-propose.md`
(canonical) + compact version inline in fable-plan §2; engine internals unchanged in
`docs/diagrams/investigation-agent-dashboard-chat.md`.

- [x] Phase 0 — decision record: lessons.md reconciliation entry, AI-ARCHITECTURE "Detect-and-propose" subsection, README roadmap rewrite (2026-07-07)
- [x] Phase 1 — proposal bridge (deterministic, no LLM), COMPLETE 2026-07-07:
  - [x] `errander/models/proposals.py` — AgentProposal + ProposalEvidence; validated action set (disk_cleanup/log_rotation only), identifier + kind-consistency validators
  - [x] `errander/safety/proposal_store.py` + migration #16 — dedup upsert (partial unique index: one open per vm/action_key), atomic decide, snooze/wake, expiry, execution claim
  - [x] `errander/agent/proposal_detector.py` — deterministic post-probe detector (disk→cleanup+logrot; drift/logins→review-only), inventory-enabled filter, dedup-aware filing
  - [x] `main.py` — detector wired into both probe paths; `_proposal_reconciler` (D1: targeted run via existing sub-graph; config-drift + window + lock gates; dry-run-safe); `--proposals`/`--proposal-show` CLI; interval job registered
  - [x] `web/ui.py` — `/ui/proposals` queue (AGENT-ORIGINATED badge, evidence chain, approve/reject/snooze, RBAC decide_approvals), nav entry, routes; wired via `web/__main__.py`
  - [x] 9 lifecycle EventTypes; per-transition audit
  - [x] Tests: 60 new (models 14, store 18, detector 15, reconciler 10, web UI 12); green
  - [x] Fixed a pre-existing latent circular import (validators↔subgraphs.patching) the new module exposed — see lessons.md 2026-07-07
- [ ] Phase 2 — agentic investigation engine: resurrect Plan A (hand-rolled tool loop, read-only tools, budgets, per-hop audit) + `proposed_work` output validated against action set/inventory, `--ask --agentic`, default OFF
- [ ] Phase 3 — event-driven trigger: probe anomalies enqueue bounded investigations that enrich Phase 1 proposals; caps + dedup window + kill switch (default off)
- [ ] Phase 4 — memory loop: proposal decisions/outcomes as VM facts, facts fed into investigation context, rejected-2× suppression policy with digest surfacing
- [ ] Phase 5 — evals + LangSmith: golden-scenario replay harness (offline fake-LLM in CI), proposal precision/recall scoring, opt-in LangSmith tracing

**Key invariants:** proposal approval = work origination, not execution authorization
(targeted run through the existing Layer B path; exact-object gate unchanged); LLM-optional
at every stage (detector works with LLM down); agent never imports the approval store;
proposals validated against the fixed action set — no free-text targets or actions.

---

## Removed Plan A + Plan B (chat / agentic investigation) from core (2026-06-23, COMPLETE)

Owner decision after a deep design discussion (system of action vs system of insight):
the chat over-complicated the deterministic core for unvalidated demand. Pulled it back
out; preserved the design as a candidate separate project.

- [x] Restored 14 Plan A/B-only code files to pre-chat state (`git checkout 9168880 -- …`)
- [x] `git rm` 9 net-new files (investigation_agent.py, chat_store.py, their tests + learning docs 60/61)
- [x] Restored `web/server.py` to original diagram + re-applied 3 keeper glossary edits (Docker Hygiene term, Planning Note term, page reorder)
- [x] Surgically de-referenced chat/agentic in kept docs: README roadmap (→ "separate future project"), OBSERVABILITY, AI-ARCHITECTURE, SPEC, CLAUDE/AGENTS (MCP blockquote), learning index
- [x] Kept: deterministic `--ask`, docker/postgres bootstrap, docker_prune→hygiene staleness fixes, MCP clarification, AGENTS↔CLAUDE re-sync
- [x] Doc sync: STATUS.md, this file, tasks/lessons.md, docs/command-log.md

**Verification:** `ruff` clean, `mypy` clean (112 files), full suite green. The implementation plans (`tasks/investigation-agent-implementation-plan.md`, `tasks/dashboard-chat-implementation-plan.md`) kept as the spec for the future project. **Not committed/pushed yet.**

---

## Workflow diagram accuracy fix — ELK/Prometheus node text (2026-06-22, COMPLETE)

Owner asked "is this correct?" about the ELK popup — it wasn't. Traced actual callers:
ELK/Prometheus are read only by the daily probe (read-only observation) + Layer A, never
the execution path; journalctl runs unconditionally (not an ELK fallback).

- [x] `errander/web/server.py` — fixed ELK + Prometheus WF_NODES popups ("Layer B reads ... at probe/plan time" → "daily probe + Layer A read it; execution path never touches them; planner uses stored Postgres signals")
- [x] Fixed "falls back to journalctl when ELK off" → "journalctl runs unconditionally in parallel, not a switch-over" (popup + glossary ELK term)
- [x] Fixed data-band sublabel ("written by Layer B, read by both" → "Postgres = Errander's store · Prometheus & ELK = external read-only") + Investigation Engine note
- [x] Doc sync: STATUS.md, docs/command-log.md, this file, tasks/lessons.md

**Verification:** `ruff`/`mypy` clean, smoke test passes, live curl confirms corrected phrasing + zero stale claims. **Not committed yet.**

---

## Workflow diagram redesign — three honest bands (2026-06-22, COMPLETE)

Owner felt the diagram was "off" + could be richer/self-explanatory (couldn't name it).
Diagnosis: linear state-machine drawing of a layered system; the Layer A lane read as a
downstream step; the shared data substrate was invisible. Redesigned in the SAME style
(owner: "don't change the style") into 3 labeled bands.

- [x] `errander/web/server.py` — reorganized into LAYER B (execution, unchanged flow) / DATA & OBSERVABILITY (new: Postgres, Prometheus, ELK, Metrics&AI-Log) / LAYER A (Investigation Engine + Ask/Chat). Layer A now reads UPWARD into the substrate (kills the "downstream step" illusion); Audit Logging → Postgres "WRITES AUDIT" arrow added; "reads · direct HTTP/SQL · no MCP" + "recommends to operator · never executes" labels
- [x] 3 new clickable nodes (postgres/prometheus/elk) with full detail popups; metrics-observability moved into data band; 17 nodes total
- [x] New CSS (`.wf-node-data`, `.wf-dot-blue`, `.wf-band-tag`, `.wf-band-sep`); canvas 1060→1170px; band tags + separators replace the apologetic section-divider
- [x] Doc sync: STATUS.md, docs/command-log.md, this file, tasks/lessons.md

**Verification:** `ruff`/`mypy` clean, glossary smoke test passes, Playwright confirmed all 17 nodes open their modals + screenshotted the layout. **Not committed yet.**

---

## MCP reality-gap doc fix (2026-06-22, COMPLETE)

Owner asked how the agent connects to Prometheus/ELK/PostgreSQL ("is it via MCP?").
Confirmed via code: no MCP anywhere — direct aiohttp HTTP + asyncpg/SQLAlchemy, plain
in-process tool calls. MCP is only an architectural allowance in docs, never built. Docs
gave a misleading impression; fixed (docs only):

- [x] `errander/web/server.py` — Glossary: Prometheus/ELK terms now name transport ("direct HTTP (aiohttp) — no MCP"); added PostgreSQL term + MCP term (states it's permitted-but-unimplemented); Investigation Engine workflow node note now says "in-process Python calls — direct HTTP/SQL, no MCP"
- [x] `CLAUDE.md` + `AGENTS.md` (byte-identical mirror) — blockquote clarifying MCP is an allowed-not-built Layer A capability; kept the slogan as a principle
- [x] `docs/AI-ARCHITECTURE.md` — as-built note under the Layer A "allowed to use" table (permission boundary, not build manifest); fixed one stale "Docker prune"
- [x] Doc sync: STATUS.md, docs/command-log.md, this file, tasks/lessons.md

**Verification:** `ruff`/`mypy` clean, glossary smoke test passes, confirmed live via curl. **Not committed yet.**

---

## Agent Workflow diagram — add the Layer A lane (2026-06-22, COMPLETE)

Owner-reported follow-up: the Glossary page's interactive workflow diagram only showed
the batch maintenance graph — no Investigation Agent, Dashboard Chat, or Metrics/AI
Decisions, all shipped earlier this session.

- [x] `errander/web/server.py` — 4 new clickable nodes (Ask CLI, Dashboard Chat, Investigation Engine, Metrics & AI Decisions) in a new "LAYER A — ASK & CHAT" lane below the existing batch pipeline, visually separated (dashed pink) since they're parallel entry points, not sequential steps
- [x] Canvas extended 845px → 1060px; new CSS classes + legend entry; full click-to-expand detail for each new node (checks/onfail/code/note) matching the existing node format
- [x] Discovered (not fixed, out of scope): workflow/glossary CSS is defined twice in `server.py` (global `CSS` constant + `GLOSS_CSS`) — confirmed via cascade order that editing only `GLOSS_CSS` is sufficient since it renders later in the DOM
- [x] Doc sync: STATUS.md, docs/command-log.md, this file, tasks/lessons.md

**Verification:** `ruff`/`mypy` clean, glossary smoke test passes, confirmed live via curl against the running demo server — all 4 new node IDs and the divider text render.

**Not committed yet** — awaiting owner go-ahead.

---

## Doc accuracy sweep — Glossary + README/CLAUDE.md/AGENTS.md/SPEC.md (2026-06-22, COMPLETE)

Owner-reported, while reviewing the demo: web UI Glossary stale + page layout wrong;
README/docs not properly updated for the v1.1 docker_hygiene cutover.

- [x] `errander/web/server.py` — `page_glossary()` returns `workflow_section + grid_section` (was reversed); owner wants the Agent Workflow diagram on top, term cards at the bottom
- [x] `_GLOSS` — "Docker Prune" entry replaced with "Docker Hygiene" (described a removed v1.1 action); added "Investigation Agent", "Dashboard Chat", "Planning Note" (shipped features missing from the glossary)
- [x] `CLAUDE.md` — fixed an internal contradiction: Risk/Rollback Tiers tables said "Docker prune" while the Domain Rules section 3 subsections below correctly says it was fully removed in v1.1
- [x] `AGENTS.md` — had drifted to a pre-docker_hygiene/pre-R3/pre-RBAC snapshot (100% diff vs CLAUDE.md); replaced with an exact mirror of the corrected CLAUDE.md
- [x] `docs/SPEC.md` — added as-built notes at every divergence point not already flagged (§2 network diagram, §5.2 Docker Prune heading + full note, Risk/Rollback Tiers + dry-run tables, policy YAML `docker_prune_all`, §10 LLM functions — planning no longer reorders the plan post-R1, Failure Analysis was deleted, NL audit querying shipped as Investigation Agent/Dashboard Chat — §16 lifecycle diagram/prose mismatch, Appendix tree); preserved the doc's "kept for lineage" framing, didn't rewrite historical command-level detail
- [x] `README.md` — one stale "docker prune" mention (graph-isolation bullet) fixed
- [x] Doc sync: STATUS.md, docs/command-log.md, this file

**Verification:** `uv run ruff check .` clean, `uv run mypy errander/` clean (114 files), `tests/ui/test_web_server_smoke.py::test_page_glossary_renders` passes. Manually verified against the running demo server: logged in via curl, fetched `/ui/glossary`, confirmed "Agent Workflow" renders before "Glossary" and all 4 refreshed terms are present in the HTML.

**Not committed yet** — awaiting owner go-ahead.

---

## Plan B — Dashboard Chat, phase 1 (2026-06-22, CODE COMPLETE — awaiting owner manual test)

Roadmap item #4 (`tasks/dashboard-chat-implementation-plan.md`), built on
Plan A's engine immediately after it, per the wrist-injury sprint decision.
Phase 1 only (read-only chat) — phases 2 (streaming) and 3 (action handoff)
explicitly deferred, matching the source plan's own phasing.

- [x] `errander/safety/chat_store.py` — `ChatStore`/`ChatThread`/`ChatMessage`, mirrors `ApprovalRequestStore`'s shape minus approval-race machinery; server-generated `thread_id` (never client-supplied)
- [x] `errander/safety/migrations.py` — migration 16 (`chat_threads`, `chat_messages`, FK cascade-delete)
- [x] `errander/web/ui.py` — `ChatEngineDeps` (bundles 6 engine deps under one AppKey), `CHAT_STORE_KEY`/`CHAT_ENGINE_DEPS_KEY`, 4 routes/handlers (`_ui_chat`, `_ui_chat_thread`, `_ui_chat_new_thread`, `_ui_chat_message_post`), nav entry, chat CSS; ownership-checked, double-submit rate-limited, redacts assembled history
- [x] `errander/web/__main__.py` — constructs `ChatStore` + `ChatEngineDeps` (LLM/Prometheus/ELK clients via lazy import, `VMDiskHistoryStore`/`BaselineStore`, `validate_inventory()`) gated on `settings.chat_enabled`; closes Prom/ELK sessions on shutdown
- [x] `errander/config/settings.py` — `chat_enabled` (default False) / `chat_max_history_turns` (default 20) / `chat_max_threads_per_user` (default 50)
- [x] Tests (53 new/extended): `tests/safety/test_chat_store.py` (13, NEW), `tests/web/test_chat.py` (9, NEW), `tests/web/test_import_isolation.py` (+1), `tests/safety/test_migrations.py` (+1 new, 2 hardcoded counts updated)
- [x] Doc sync: STATUS.md, docs/OBSERVABILITY.md (new chat callout), README.md (Roadmap "Shipped" + chat description), docs/learning/61-dashboard-chat.md (NEW) + README.md index row, docs/command-log.md, tasks/lessons.md (2 new lessons)

**Verification:** `uv run ruff check .` clean; `uv run mypy errander/` clean (114 files); Plan B's 40-test scope + Plan A's scope, all green. Manual smoke test against the **real running web server process** (login → create thread → post question → confirm deterministic-fallback answer renders and persists across reload) — confirms `__main__.py` wiring works end-to-end, not just `TestClient`. **Not yet tested against a real LLM endpoint or in a real browser** — owner's pending step.

**NEXT:** Owner's manual test pass on Plan A + Plan B together (real LLM endpoint, real browser). Then roadmap items #1/#2 (Prometheus test, LangSmith wiring). Plan B phases 2 (streaming) and 3 (action handoff) are separate future sessions.

---

## Plan A — Layer A Investigation Agent (2026-06-22, CODE COMPLETE — awaiting owner manual test)

Roadmap item #3 (`tasks/investigation-agent-implementation-plan.md`), reconciled
against as-built code and reviewed by a second model (Opus 4.8) before
implementation — see the approved plan and `tasks/lessons.md` for the review
deltas. Owner fractured their wrist 2026-06-22; decision: code Plan A + Plan B
now, owner does the manual test pass once recovered.

- [x] `errander/agent/investigation_agent.py` — `InvestigationAgent.investigate_agentic()`: tool registry (`query_prometheus`, `search_logs`, `get_audit_events`, `get_disk_trend`, `get_vm_facts`, `list_inventory`), bounded ReAct loop, citation-by-embedded-source-id, turn-1 capability detection, per-hop audit delta, fallback metric, defensive clamp, never raises
- [x] `errander/integrations/llm.py` — `LLMClient.complete_with_tools()` (`ToolCallRequest`/`ToolCallResult`), empty-tools omits `tools=`/`tool_choice=` (provider 400 avoidance), `asyncio.Semaphore(1)` for sequential self-hosted calls; `complete()` untouched
- [x] `errander/integrations/prometheus.py` / `elk.py` — `query()` / `search()` arbitrary read-only methods alongside the existing fixed ones (untouched)
- [x] `errander/config/settings.py` — `investigation_agent_enabled` (default False) / `_max_tool_calls` (default 8) / `_timeout_seconds` (default 180)
- [x] `errander/observability/metrics.py` — `INVESTIGATION_TOOL_CALLS_TOTAL`, `INVESTIGATION_FALLBACK_TOTAL{reason}`
- [x] `errander/main.py` — `--agentic` flag (modifier on `--ask`), `run_ask_query()` branches on flag + setting, off-but-requested prints a one-line notice
- [x] Tests (109 new/extended): `tests/agent/test_investigation_agent.py` (13, NEW), `tests/agent/test_investigation_tools.py` (26, NEW), `tests/agent/test_investigation_agent_isolation.py` (1, NEW), `tests/integrations/test_llm.py` (+11), `tests/integrations/test_prometheus.py` (+6), `tests/integrations/test_elk.py` (+7)
- [x] Doc sync: STATUS.md, docs/OBSERVABILITY.md (flip "planned" → "available, opt-in"), README.md (Roadmap "Shipped" + CLI example), docs/learning/60-investigation-agent.md (NEW) + README.md index row, docs/command-log.md, tasks/lessons.md

**Verification:** `uv run ruff check .` clean; `uv run mypy errander/` clean (113 files); `uv run pytest tests/agent/ tests/integrations/ tests/ai_evals/test_golden_plans.py` — 1015 passed, zero regression to the deterministic batch path; manual CLI smoke test (flag off, flag on + no LLM) confirms both fallback paths. **Not yet tested against a real LLM endpoint** — owner's pending step.

**NEXT:** Plan B — Dashboard Chat (`tasks/dashboard-chat-implementation-plan.md`), builds on this engine. Then the owner's manual test pass on both. Then roadmap items #1/#2 (Prometheus test, LangSmith wiring).

---

## Infra — automate Docker + Docker Compose + PostgreSQL provisioning (2026-06-14, COMPLETE)

Not a §8d step — pure infra/setup change, no `errander/` Python code touched.

- [x] `scripts/bootstrap.sh` — new step "6/8 — Docker + Docker Compose" (install via `get.docker.com` if missing, `systemctl enable --now docker`, `usermod -aG docker errander-agent`); web-user step renumbered 6/7 → 7/8, clone-repo step renumbered 7/7 → 8/8
- [x] `scripts/configure.sh` — updated DB-URL prompt text; added `docker compose up -d --wait` bring-up block (with `pg_isready` polling fallback for older Compose) gated on `DB_URL` exactly matching the default `postgresql://errander:errander@localhost:5432/errander`; custom URLs skip provisioning entirely
- [x] `docker-compose.yml` — `restart: unless-stopped` on the `postgres` service so it survives host reboots
- [x] `deploy/errander-agent.service`, `deploy/errander-web.service` — `After=network.target postgresql.service` → `After=network.target docker.service` + `Requires=docker.service` (Postgres runs as a Docker container, not a native systemd service)
- [x] `SETUP.md` — Step 1 bullet list (Docker install), Step B code block (removed manual `docker compose up -d` instruction, added auto-provisioning note), Step 5 section (note that `configure.sh` starts Postgres automatically), "Starting fresh / teardown" (Docker/Postgres survive teardown; `docker compose down -v` to wipe data)
- [x] Doc sync: STATUS.md, docs/command-log.md, docs/learning/59-docker-postgres-bootstrap.md (NEW) + README.md index row, tasks/lessons.md

**Verification:** `bash -n scripts/bootstrap.sh` / `scripts/configure.sh` clean; `docker compose config` clean; `grep -rn "postgresql.service" deploy/ SETUP.md` shows no stray native-Postgres references (the one match in SETUP.md's `restartable_units` example refers to a *target VM's* Postgres service, unrelated). No fresh Linux VM available for end-to-end testing.

---

## §8d Step 5 — R1: advisory-LLM batch planning (2026-06-14, COMPLETE)

- [x] `errander/agent/decisions.py` — `prioritize_actions()` now always `_hardcoded_priority` (deterministic, F2 fix); new `_PlanningNote`/`_PLANNING_NOTE_MAX_CHARS`/`_sanitize_note`/`_build_planning_note_prompt`/`generate_planning_note()`; deleted `_PrioritizedActions`, `_FailureAnalysis`, `analyze_failure()`, `_build_failure_prompt()`, dead 3.2b policy-filter block, `BUILTIN_POLICIES` import (F4 sweep)
- [x] `errander/agent/graph.py` — `plan_vm_node` calls `generate_planning_note()`, stores `ai_note` in vm plan dict (covered by existing plan hash); `_format_plan_for_approval` appends labeled AI-analysis section after approval instructions
- [x] `errander/agent/vm_graph.py` — `plan_actions_node` simplified to `prioritize_actions(vm_info)`
- [x] `errander/web/ui.py` — `_render_approval_plan` renders `.apv-ai-note` (HTML-escaped) when present + new CSS
- [x] `errander/evals/replay.py` — new `_check_planning_note`; removed `_check_failure_analysis`/`_VALID_RECOMMENDATIONS`
- [x] `errander/safety/ai_audit.py` — docstring `analyze_failure` → `planning_note`
- [x] Tests: `tests/agent/test_decisions.py`, `tests/agent/test_plan_vm_stored_signals.py`, `tests/ai_evals/test_golden_plans.py` (new F2 regression `test_planning_note_llm_output_never_changes_plan`), `tests/ai_evals/test_adversarial.py`, `tests/ai_evals/test_replay.py`, `tests/agent/test_approval_message_p01.py` (+2), `tests/web/test_approval_ai_note.py` (NEW, 3), fallout fixes in `tests/integrations/test_llm.py` + `tests/chaos/test_fault_injection.py` (6 tests)
- [x] Doc sync: fable.md (§8 checklist + acceptance criteria + roadmap row 5), STATUS.md, README.md, docs/OBSERVABILITY.md, docs/AI-ARCHITECTURE.md, docs/learning/58-advisory-planning-note.md (NEW), tasks/todo.md, tasks/lessons.md, docs/command-log.md

**Verification:** `uv run ruff check errander/ tests/` clean; `uv run mypy errander/` clean (112 files); full suite 8 failed / 2476 passed / 171 errors (485.93s) — the 8 failures (`tests/ui/test_approval_ui.py`, needs seeded user account) + 171 errors (`tests/ui/*`+`tests/web/*` pytest-asyncio runner pollution) are pre-existing and unrelated to R1, confirmed via `git stash` reproducing identically on pre-R1 `main`.

**NEXT:** §8d Step 6 — Plan A: investigation agent (`tasks/investigation-agent-implementation-plan.md`).

---

## §8d Step 4 — R3: process separation (IN PROGRESS)

- [x] Migration #15 — `hygiene_approval_requests` table + `users.totp_secret` column
- [x] `errander/safety/hygiene_store.py` — `HygieneApprovalStore`/`HygieneApprovalRow` (mirrors `ApprovalRequestStore`: create/decide/mark_timeout/expire_overdue/wait_for_decision/list_pending)
- [x] `HygieneApprovalManager` → thin facade over `HygieneApprovalStore`; `PendingHygieneApproval` removed
- [x] `DockerHygieneAssessment.to_json()`/`from_json()` (dataclass JSON round-trip)
- [x] `metrics.py` + `web/server.py` hygiene approve handlers call `store.decide(...)` directly; `_HYGIENE_STORE_KEY`
- [x] `_approval_reconciler` also calls `hygiene_store.expire_overdue()`
- [x] `errander/web/totp.py` — pyotp wrapper (generate_secret/make_qr_uri/verify_code)
- [x] Tests: `tests/safety/test_hygiene_store.py` (16, NEW), `tests/web/test_totp.py` (7, NEW), `tests/safety/test_migrations.py` (+1)
- [ ] Extract `errander/web/ui.py` from `errander/observability/metrics.py` (AppKeys, `_ui_*` handlers, CSS, auth/CSRF middleware, `build_ui_app()`, new `start_web_server()`)
- [ ] Slim `errander/observability/metrics.py` to Prometheus defs + `/metrics` + `/health` only
- [ ] `errander/web/__main__.py` — production entry (`python -m errander.web`)
- [ ] `errander/main.py` — drop user/session/ai_decision stores from agent startup; slim `start_metrics_server` call
- [ ] TOTP login flow wired into `ui.py` (challenge/setup routes, admin-group + public-mode gating)
- [ ] `tests/web/test_import_isolation.py`, `tests/web/test_web_entry.py`
- [ ] Update `tests/observability/test_rbac.py`, `test_metrics.py`, `test_ui_security.py`, `tests/agent/test_hygiene_orchestration.py` for new import paths
- [ ] Update `tests/ui/*` (9 files) + `scripts/*` (3 files) that import `start_metrics_server`/`build_ui_app` with full UI kwargs
- [ ] Deploy artifacts: `deploy/errander-agent.service`, `deploy/errander-web.service`, `deploy/nginx-mode2.conf.example`, `.env.agent.example`, `.env.web.example`, `scripts/bootstrap.sh` OS-user step
- [ ] Doc sync: fable.md §8b, SETUP.md, RUN.md, CLAUDE.md, docs/SECURITY.md (new), docs/langgraph-primer.md, docs/learning/58-process-separation.md

---

## §8d Step 3 — R2: users/groups RBAC + web-only approval (2026-06-12, COMPLETE)

- [x] Migration #14 — users / groups / group_permissions / user_groups / sessions (+ seed admin/reader groups + admin permissions; seed re-applied idempotently every run_migrations + after test TRUNCATE)
- [x] `errander/safety/user_store.py` — UserStore (scrypt hashes, groups/permissions) + SessionStore (DB-backed, token hashed at rest)
- [x] New EventTypes: USER_CREATED / USER_DELETED / USER_GROUPS_CHANGED / USER_PASSWORD_CHANGED
- [x] Web UI auth rewrite (metrics.py) — DB users + DB sessions, `request["user"]`, `?next=`, zero-users guard (non-loopback → RuntimeError; loopback → GET-only); `build_ui_app()` factory for tests
- [x] Server-side RBAC — `_require_permission`: decide_approvals (approvals + hygiene GET/POST), manage_settings (settings/inventory POSTs); decided_by=`ui:<user>` + decided_by_group recorded + shown in history
- [x] /ui/approvals lists pending hygiene approvals with self-generated signed links
- [x] Slack → notify-and-link: request_approval rewrite (+web link, no reactions); poll_approval/watch_slack_reactions deleted; gate drops watcher; reconciler drops pass 2, gains 120 s claim grace
- [x] Service-restart CLI → durable store approval (web decision, claim before execute; Slack optional)
- [x] docker_hygiene Slack reply channel removed (poller + parser deleted; formatter keeps web link, drops reply syntax; volumes report-only in v1 web approval — fail closed)
- [x] CLI user management — --user-add/--user-remove/--user-list/--user-set-groups/--user-set-password (audited, cli:<os-user>) + startup seed from ERRANDER_UI_USER/PASSWORD
- [x] settings: approval_poll_interval_seconds removed (YAML key accepted-but-ignored); ui_user/ui_password = seed-only
- [x] Tests: test_user_store (23) + test_rbac (17); gate/reconciler/restart-CLI/message/slack/settings/migrations rewrites; test_hygiene_reply_polling.py deleted
- [x] Doc re-sweep "Slack or Web UI" → "Web UI (Slack notifies and links)" (README/CLAUDE/AGENTS/SETUP/RUN/SPEC/primer/OBSERVABILITY/SECRETS + UI/demo copy) + fable.md checkboxes + learning doc 57
- [x] Full suite green on Postgres, ruff + mypy clean, single commit, CI green
- [ ] NEXT: §8d Step 4 — R3 process split (two services, two OS users, key + import isolation, nginx Mode 2 + TOTP)

---

## §8d Step 2 — R3 keystone: `approval_requests` DB-backed store (2026-06-11, COMPLETE)

- [x] Migration #13 — `approval_requests` table (status CHECK, decided_by/_group, approved_items_json, execution_started_at)
- [x] `errander/safety/approval_store.py` — `ApprovalRequestStore`: atomic `decide()` (UPDATE WHERE status='pending'), `mark_execution_started` claim, `expire_overdue` (RETURNING), `wait_for_decision` (2 s poll + in-process event)
- [x] `approval_gate_node` rewrite — durable row first → Slack notify → `watch_slack_reactions` watcher → store wait; execution claim before window-defer; fail-closed when store missing
- [x] Delete `ApprovalManager`/`PendingApproval`/`await_dual_approval`/`BatchApprovalResult` (keep `request_approval`/`poll_approval`)
- [x] Deferred-replay hash bug fix — `preloaded_batch_id` (state + init_batch_node + run_env_batch + window opener + reconciler); lock-in test added
- [x] Exact-object continuity — `preloaded_approved_items` recovered from the approval row on both replay paths
- [x] Restart reconciler (`_approval_reconciler`, 60 s interval job): expire → resume Slack watchers → execute orphaned approved (claim-guarded, window-aware)
- [x] `scheduler.add_interval_job` (max_instances=1)
- [x] Web UI repointed — `_APPROVAL_STORE_KEY`, async pending counts, `_ui_approval_decide` atomic store decide (`ui:<username>`), providers/server refresh kwarg
- [x] Tests — store CRUD + AC4 decide race; reconciler AC3 restart recovery + double-execution guard; gate rewrites across 8 test files; full suite green on Postgres; ruff + mypy clean
- [x] GH Actions bump — checkout@v5, setup-uv@v7 (Node 20 deprecation)
- [ ] NEXT: §8d Step 3 — R2: users/groups RBAC + web-only approval (§8a)

---

## PostgreSQL-Only Migration (2026-06-10, COMPLETE — owner decision, supersedes §8c dual-backend)

- [x] pyproject: asyncpg core, drop `postgres` extra, +langgraph-checkpoint-postgres/psycopg, −aiosqlite/−langgraph-checkpoint-sqlite
- [x] `errander/db/core.py` — Postgres-only; reject other URLs with SETUP.md-pointing error
- [x] `errander/safety/migrations.py` — drop dialect/_adapt_ddl; BIGSERIAL/BIGINT DDL (int32 fix)
- [x] Dialect branches removed (STRING_AGG, id-ordering); runs.py checkpoint probes ported
- [x] `main.py` checkpointer → AsyncPostgresSaver (psycopg URL + setup())
- [x] settings default + web/server fallbacks → postgresql://errander:errander@localhost:5432/errander
- [x] `docker-compose.yml` (postgres:16) + `deploy/postgres-init/01-create-test-db.sql`
- [x] conftest: `make_test_db()` + session migrations + autouse TRUNCATE; ~40 test files converted
- [x] CI: single Test (PostgreSQL) job, full suite, web-role INSERT-denied check
- [x] configure.sh PostgreSQL URL prompt
- [x] 2445 tests green on Postgres, ruff clean, mypy clean
- [x] §8d Step 2 — `approval_requests` DB-backed store — DONE 2026-06-11 (see section above)

---

## §8d Step 1 — R4: PostgreSQL Dual-Backend + DB Layer (2026-06-10, COMPLETE)

- [x] `errander/db/__init__.py` + `errander/db/core.py` — `AsyncDatabase` wrapper
- [x] `pyproject.toml` — `sqlalchemy[asyncio]>=2.0`; `postgres = ["asyncpg>=0.29"]` extra; `uv sync`
- [x] `errander/safety/migrations.py` — port + add migrations #10-#12; `_adapt_ddl()`
- [x] `errander/safety/audit.py` — first store; GROUP_CONCAT + INSERT OR IGNORE fixes
- [x] `errander/safety/batches.py`, `artifacts.py`, `agent_lease.py` — ported
- [x] Remaining 9 safety stores + observability + evals — all ported
- [x] `errander/web/providers.py` + `server.py` — GROUP_CONCAT fix; AsyncDatabase wrap
- [x] `errander/main.py` — construct `AsyncDatabase`; pass to all stores
- [x] `tests/conftest.py` — `TEST_DB_URL` at module level; `session_db` + `async_db` fixtures
- [x] `tests/safety/test_migrations.py` — dialect-agnostic introspection; uses `TEST_DB_URL`
- [x] All other test files — `AsyncDatabase(":memory:")` wrapping
- [x] `.github/workflows/ci.yml` — `test-postgres` job with role-grant verification step
- [x] `deploy/postgres-setup.sql` — `errander_agent` + `errander_web` role grants
- [x] 2446 tests green, ruff clean, mypy clean

---

## §8d Step 0 — CI (2026-06-10, COMPLETE)

- [x] Fix B904 in `errander/observability/metrics.py:1085` — `raise ... from None`
- [x] Fix E501 in `errander/observability/metrics.py:1017` — split 122-char HTML line
- [x] Fix I001 in `tests/config/test_prompts.py` — auto-fixed import sort
- [x] Add `exclude = ["^tests/", "^scripts/"]` to `[tool.mypy]` in `pyproject.toml`
- [x] Create `.github/workflows/ci.yml` — lint + test (SQLite) + secrets (gitleaks) jobs
- [x] Create `.gitleaks.toml` — allowlist for example/demo placeholder credentials
- [x] Add CI badge to `README.md`
- [x] `uv run ruff check .` → clean; `uv run mypy errander/` → clean; 2626 tests green

---

## Enterprise wizard input validation — shared _prompts.py (2026-06-10, COMPLETE)

- [x] `errander/config/_prompts.py` — new shared module with all prompt helpers
- [x] `errander/config/inventory_wizard.py` — remove local helpers; import from `_prompts`; all constrained inputs validated inline
- [x] `errander/config/add_target.py` — same; fix inventory keep/replace choice loop
- [x] `tests/config/test_prompts.py` — 87 new tests for all helpers
- [x] 2626 tests passing, ruff clean, mypy clean

---

## configure.sh/add-target auto wrapper install (2026-06-09, COMPLETE)

- [x] `inventory_wizard.py` — collect restart units immediately (required); remove `service_restart_intent`
- [x] `configure.py` — docker wrappers + restart wrapper check/prompt/install in `_configure_vm`
- [x] `add_target.py` — same wrapper helpers; post-save install loop with prompts per new VM
- [x] `tests/config/test_inventory_wizard.py` — remove intent-only test; update helpers
- [x] 2539 tests passing, ruff clean, mypy clean

---

## Wizard prompt clarity — backup_verify and critical_services (2026-06-09, COMPLETE)

- [x] `backup_verify` prompt — added 4-line explanation: read-only file check, does NOT create backups, requires settings.yaml
- [x] `critical_services` prompt — added 4-line explanation: watch-only sentinels vs service_restart; changed example default from "nginx,ssh" to "ssh"
- [x] 22 wizard tests passing, ruff clean

---

## `add_target.py` UX improvements (2026-06-09, COMPLETE)

- [x] Switch I/O from `yaml.dump` to `ruamel.yaml` (comment-preserving)
- [x] Numbered OS family menu: 1) ubuntu  2) debian  3) rhel
- [x] Docker question: "Is Docker installed?" (only when env has docker_hygiene enabled)
- [x] Service restart intent question with TODO comment in YAML
- [x] Build target dict with `actions:` overrides when appropriate
- [x] 2539 tests passing, ruff clean, mypy clean

---

## Approval surface wording fix (2026-06-09, COMPLETE)

- [x] `AGENTS.md` — opening line + risk tier table
- [x] `CLAUDE.md` — risk tier table
- [x] `README.md` — action table, CLI comment, safety gates table
- [x] `errander/config/inventory_wizard.py` — approval policy menu text + generated YAML comments
- [x] `errander/main.py` — `--help` text, docstring, terminal print
- [x] `errander/web/server.py` — admin panel label, glossary chip, action execution note
- [x] All 2537 tests still passing

---

## Enterprise inventory wizard + comment-preserving YAML (2026-06-09, COMPLETE)

- [x] `pyproject.toml` — add `ruamel.yaml>=0.18` + mypy override; `uv sync`
- [x] `errander/config/inventory_wizard.py` — NEW: wizard dataclasses, prompt helpers, `_render_inventory_yaml`, `_render_env`, `_render_target`, `_summarise_existing`, `_count_vms`, `_write_result`, `main()`
- [x] `scripts/configure.sh` — remove bash VM collection loop (193–291) + bash YAML block; step 2 calls Python wizard; reads `~/.errander_wizard_result`; `_inv_count` set immediately after wizard call
- [x] `errander/config/configure.py` — `_update_inventory_yaml` → ruamel.yaml round-trip (comments preserved)
- [x] `tests/config/test_inventory_wizard.py` — NEW: 20 tests (render, schema validation, ruamel round-trip, helpers)
- [x] 2537 tests passing (2517 + 20 new)
- [x] ruff clean, mypy clean (621 pre-existing errors unchanged)
- [x] `docs/learning/52-configure-wizard.md` — NEW learning doc
- [x] `SETUP.md` — Step 5 description updated
- [x] `STATUS.md`, `todo.md`, `lessons.md`, `command-log.md` doc sync

---

## Per-target `actions:` support (2026-06-09, COMPLETE)

- [x] `errander/config/schema.py` — `TargetSchema`: add `actions: dict[str, ActionConfig] | None`, `resolve_actions()` method; validate per-target overrides (docker_prune, docker_hygiene contradiction, service_restart units) in `EnvironmentSchema._apply_action_defaults_and_validate`
- [x] `errander/main.py` — `yaml_targets`: embed per-target `enabled_actions` + `docker_command_mode` in each target dict; `run_check_targets`: use per-target resolved actions; `run_restart_service`: check per-target resolved units per VM
- [x] `errander/agent/graph.py` — all three fan-out paths (`validate_targets_node`, `route_plan_vms`, `make_simple_fan_out`, `make_wave_dispatcher`) use per-target values from target dict with batch-level fallback for DB-added VMs
- [x] `example/inventory.yaml` — per-target `actions:` header comment + production env examples
- [x] `SETUP.md` — step 5b/5c rewritten to show per-target syntax with "why per-target" callout
- [x] Tests: 11 new tests in `TestPerTargetActions` (resolve, validation, YAML roundtrip)
- [x] 2517 tests passing (2506 + 11 new)

---

## configure.sh security hardening + add-target new-env support (2026-06-09, COMPLETE)

- [x] `scripts/configure.sh` — `ERRANDER_ELK_API_KEY` now encrypted via `encrypt_val` (was written plaintext)
- [x] `scripts/configure.sh` — 4 API key prompts switched `prompt_val` → `prompt_secret`: vLLM, "Other" provider, ELK API key (x2)
- [x] `scripts/configure.sh` — `ERRANDER_SIGNING_SECRET` auto-generated; encrypted; written to `.env`
- [x] `scripts/configure.sh` — `ERRANDER_WEB_BASE_URL` now prompted in Step 5; pre-filled on re-run
- [x] `errander/config/add_target.py` — `[n] New environment` option; prompts env-level YAML fields before VM loop
- [x] Doc sync: STATUS.md, todo.md, lessons.md, SETUP.md, docs/SECRETS.md

---

## `/ui/monitoring` time-range selector + Prometheus+Grafana demoted (2026-06-08, COMPLETE)

- [x] `errander/observability/metrics.py` — `?days=` query param (1/7/30), dynamic window labels, `_tr_btn()` toggle, toggle CSS
- [x] `scripts/bootstrap.sh` — removed Prometheus + Grafana install block; updated Done banner
- [x] `SETUP.md`, `README.md`, `SETUP-Win-Controller.md`, `docs/MONITORING-VALIDATION.md` — reframed as external-VM-only
- [x] 2506 tests passing, mypy clean, ruff clean (pre-existing issues only)

---

## Monitoring page gap-fill — approval funnel, safety signals, durations (2026-06-05, COMPLETE)

- [x] `errander/safety/audit.py` — `get_monitoring_stats()` extended: approval funnel query + safety signals query, two new return keys
- [x] `errander/observability/metrics.py` — `_hist_avg()` + `_hist_avg_by_label()` helpers; `_read_prom_counters()` adds histogram averages; `_ui_monitoring()` adds approval cards, safety section, performance section
- [x] 2506 tests passing, mypy clean, ruff clean (pre-existing issues only)
- [x] `/ui/monitoring` now covers every OBSERVABILITY.md surface except LangSmith + raw logs

---

## Controller Monitoring page — /ui/monitoring with Chart.js (2026-06-05, COMPLETE)

- [x] `errander/safety/audit.py` — `get_monitoring_stats()`: 30-day summary, 7-day daily, 30-day by-type SQL aggregations
- [x] `errander/observability/metrics.py` — monitoring CSS, `_ACTION_COLORS`, `_read_prom_counters()`, `_build_chart_json()`, `_ui_monitoring()` handler
- [x] Sidebar nav entry + active state detection in `_page()`
- [x] Route `/ui/monitoring` registered in `start_metrics_server()`
- [x] 2506 tests passing, mypy clean, ruff clean (pre-existing issues only)

---

## teardown.sh — full uninstall for clean re-testing (2026-06-05, COMPLETE)

- [x] `scripts/teardown.sh` — new: removes Grafana, Prometheus, errander-agent user+home, uv; confirmation prompt; curl-runnable; handles partial installs
- [x] `SETUP.md` — "Starting fresh / teardown" section added before Troubleshooting
- [x] `README.md` — teardown callout in Quick Start
- [x] Doc sync: STATUS.md, todo.md, command-log.md

---

## Grafana install — tarball rewrite (2026-06-05, COMPLETE)

- [x] `scripts/install-grafana.sh` — rewritten: official OSS tarball (no apt/yum), handles Grafana 10+ and 9.x binary layouts, removes broken apt/rpm installs, zero interactive prompts, all distros
- [x] `scripts/bootstrap.sh` — removed needrestart workaround; kept DEBIAN_FRONTEND=noninteractive on _install()
- [x] `README.md` — updated monitoring description: tarball, not package repo
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## Grafana monitoring stack — auto-provisioned dashboard (2026-06-05, COMPLETE)

- [x] `scripts/install-grafana.sh` — new: Grafana OSS via official APT/YUM repo, datasource + dashboard provisioning, random admin password via grafana-cli, SSH tunnel access instructions
- [x] `deploy/grafana/provisioning/datasources/errander.yml` — Prometheus datasource config
- [x] `deploy/grafana/provisioning/dashboards/errander.yml` — dashboard provider config
- [x] `deploy/grafana/dashboards/errander.json` — 10-panel Fleet Operations dashboard (exact metric names from metrics.py: actions by type/status, batch duration p50/p95, LLM health/fallback, SSH errors, approval wait p50/p95)
- [x] `scripts/bootstrap.sh` — Prometheus + Grafana combined into single "Install monitoring stack?" prompt
- [x] `SETUP.md` — monitoring section updated
- [x] `README.md` — tech stack table + Prometheus section updated
- [x] `docs/OBSERVABILITY.md` — Prometheus+Grafana row updated
- [x] Doc sync: STATUS.md, todo.md, command-log.md

---

## Bootstrap refactor v2 — zero-sudo install.sh + Windows doc split (2026-06-04, COMPLETE)

- [x] `SETUP-Win-Controller.md` — new file: all Windows controller steps extracted from SETUP.md
- [x] `SETUP.md` — Windows sections removed; Step 1 Linux updated (Step B loses git clone; bootstrap handles it); Prometheus monitoring note updated
- [x] `scripts/bootstrap.sh` — step 6 added (clone repo as errander-agent); Prometheus opt-in added (uses `read </dev/tty` for curl|bash compat); step count updated; done banner updated
- [x] `scripts/install.sh` — Prometheus step removed; simplified to 2 steps (uv sync + configure.sh); zero sudo
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## Bootstrap refactor — two-phase install (2026-06-03, COMPLETE)

- [x] `scripts/bootstrap.sh` — rewrite: system-only (distro, git, curl, uv, Python 3.12, service user + .ssh); remove clone/move/double-sync
- [x] `scripts/install.sh` — new: runs as errander-agent; uv sync + Prometheus opt-in + exec configure.sh
- [x] `scripts/configure.sh` — fix Prometheus URL default `9090` → `9091`
- [x] `SETUP.md` — Linux Step 1 two-step flow; manual fallback updated; Prometheus line updated
- [x] `README.md` — two bootstrap references updated

---

## Architecture diagram — draw.io polish + first commit of docs/diagrams/ (2026-06-03, COMPLETE)

- [x] Polish `docs/diagrams/errander-system-architecture.drawio`: planned indicators (dashed borders + "· planned ·" labels on Dashboard Chat, Investigation Agent, Operator Chat Interface); Slack outbound-HTTPS edge; Audit DB label cleanup; obs_note; no legend; full-width blocked bar
- [x] Restore `docs/diagrams/errander-view.html` to draw.io embedded viewer (developer had replaced with broken custom HTML)
- [x] Update `docs/diagrams/errander-system-architecture.md` (Mermaid): remove stale RM subgraph, fold planned components inline, add Fleet Prometheus + Operator Chat Interface, move LangSmith to OBS, update Reading section
- [x] Static server on port 8767 (`python -m http.server 8767 --directory docs/diagrams`)
- [x] Doc sync: STATUS.md, todo.md; first commit of docs/diagrams/

---

# ROADMAP — NOT yet built / verified (target/end-state)

The architecture diagram shows these as target components — that's the **vision**, NOT a completion claim. Build/verify in this order:

- [ ] **1. Prometheus test** — script + docs DONE; **not yet run on a dedicated monitoring VM**. Run `scripts/install-prometheus.sh` on a separate monitoring VM; confirm `http://<monitoring-vm>:9091/targets` shows `errander-agent` UP. Not needed on the agent VM.
- [ ] **2. LangSmith wiring** — SETUP/OBSERVABILITY docs + `.env`/env-var entries DONE; **not yet enabled/verified**. Set `LANGCHAIN_*` in dev/staging, confirm traces, add learning doc.
- [ ] **3. Layer A Investigation Agent** — plan DONE (`tasks/investigation-agent-implementation-plan.md`); **implementation pending** (build first; it's the engine).
- [ ] **4. Dashboard Chat (SRE ops-console)** — plan DONE (`tasks/dashboard-chat-implementation-plan.md`); **implementation pending** (after #3; reconcile step first; chat never executes — action handoff goes through approval).
- [ ] _(later, separate)_ Chat assignment / ownership — only with multiple operators

---

## README — Web UI demo screenshots (2026-05-29, COMPLETE)

- [x] Write `scripts/capture_ui_screenshots.py` — seeds in-memory stores (audit, exact-object pending approval, AI decisions, inventory+overrides), serves on loopback, headless-Chromium screenshots 6 pages with baked-in DEMO banner
- [x] Capture 6 PNGs into `docs/images/` (dashboard, approvals, ai-decisions, batches, batch-detail, inventory)
- [x] Add `## Screenshots` section to README with demo-data disclaimer and per-image captions
- [x] Doc sync: STATUS.md, todo.md, command-log.md

---

## SETUP.md — LangSmith wiring docs + env vars (2026-06-02, COMPLETE)

- [x] CLAUDE.md line 3: "Docker pruning" → "Docker hygiene (object-level approval)"
- [x] SETUP.md: new "LangSmith tracing (optional — Layer A only, dev/staging)" section (after Prometheus monitoring, before ELK) — setup steps, disable, egress warning, useful-vs-redundant panels note, "no code changes needed" callout
- [x] SETUP.md .env template: three LANGCHAIN_* vars commented out with egress warning
- [x] SETUP.md env-var table: LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, LANGCHAIN_PROJECT rows
- [x] Memory: updated project_state (2507 tests), docker_hygiene_v11 (marked complete), post_review_planning (marked superseded); added observability/positioning memory
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## README — reposition as supervised agentic AI + roadmap (2026-05-29, COMPLETE)

- [x] Headline + opening: "supervised agentic AI SRE platform" framing (was "deterministic maintenance automation"); fixed stale "Docker pruning" → "Docker hygiene"
- [x] Named the two-layer architecture (Layer A brain / Layer B hands) in the intro, not just Design Principles
- [x] New "## Roadmap" — Near-term (investigation agent, optional LangSmith, dashboard chat, with plan-file links) + V2 subsection
- [x] Positioning decision captured: supervised agentic AI (not plain gen AI, not unqualified agentic)
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## Dashboard Chat — implementation plan (Plan B) (2026-05-29, PLAN AUTHORED)

- [x] Authored `tasks/dashboard-chat-implementation-plan.md` — contract-altitude plan depending on Plan A, with a reconcile-against-as-built-engine pre-flight step
- [x] Scope: read-only `/ui/chat` ops-console (Layer A) + multi-turn conversation state + web UI; optional v1.1 action handoff via existing approval flow (chat never executes); assignment/ownership explicitly deferred
- [x] Added "Downstream" forward pointer in Plan A; recorded roadmap in STATUS "Next Up" + this ROADMAP section
- [ ] **NOT IMPLEMENTED** — hand off to Sonnet after Plan A is built
- [x] Doc sync: STATUS.md, todo.md, command-log.md

---

## Layer A Investigation Agent — implementation plan (2026-05-29, PLAN AUTHORED)

- [x] Authored `tasks/investigation-agent-implementation-plan.md` — self-contained build plan for a future (Sonnet) session
- [x] Grounded in code: `operator_assistant.py`, `integrations/llm.py` (OpenAI SDK, no tool-calling yet), `prometheus.py`, `elk.py`
- [x] Locked decisions: Layer A only (read-only tools, recommendations only, never Layer B); batch planner stays deterministic; runtime = hand-rolled tool loop on existing OpenAI SDK (recommended) vs LangGraph create_react_agent (alt)
- [ ] **NOT IMPLEMENTED** — hand off to Sonnet; tracked as future work
- [x] Doc sync: STATUS.md, todo.md, command-log.md

---

## OBSERVABILITY.md — "What Errander can see" fixed signal menu (2026-05-29, COMPLETE)

- [x] Verified gathering mechanisms in code: `disk_trend.py` (df→history→slope), `failed_logins.py` (journald/auth.log), `integrations/prometheus.py` (3 fixed PromQL), `integrations/elk.py` (1 fixed host-aggregated ES query)
- [x] New section: signal-menu table (source/scope/needs); who writes queries (devs, hardcoded, runtime fills params only); disk-trend-is-SSH-not-Prometheus + logs-are-system/host-not-app clarifications; "signal not on the menu" behavior (no improvisation, graceful degradation, adding = code change)
- [x] Cross-ref to agentic Layer-A investigation upgrade (the one place the line moves, `--ask` only)
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## README — "The two layers in one run" mapping (2026-05-29, COMPLETE)

- [x] Added subsection to How It Works: step→layer table (SSH gather=B, Prometheus/ELK=B feeds A, recommend=A, approve=human, apply=B, summarize=A)
- [x] "brain proposes → human approves → hands act" framing; noted Layer B runs twice per run
- [x] Bridges the gap: explicit Layer A/B labels now connected to the workflow in README (was only in AI-ARCHITECTURE.md)
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## OBSERVABILITY.md — built-in vs. bring-your-own boundary (2026-05-29, COMPLETE)

- [x] Reworked overview into two tables: **built-in** (audit trail, AI decision log, `/metrics`, structured logs — system of record) vs. **bring-your-own external** (Prometheus/Grafana, LangSmith-or-equivalent, ELK/Loki — recommended, not bundled, not authoritative)
- [x] Added structured JSON logs as a surface (was missing)
- [x] LangSmith section → "External Layer-A tracing — LangSmith *or equivalent*"; recommendation-not-dependency framing; value-vs-redundant/N/A table (Traces/Run Types high, LLM Calls redundant, Cost&Tokens conditional, Tools N/A, Feedback future)
- [x] Coding-agent guidance: built-in = guaranteed/rely on it; external = never assume/depend; external observes, never participates in Layer B
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## docs/OBSERVABILITY.md — per-layer observability reference (2026-05-29, COMPLETE)

- [x] Gather code facts: `EventType` list (`models/events.py`), `AuditStore.get_events` + `AIDecisionStore.get_decisions/get_decision_by_id` signatures, audit/ai-decision CLI flags (`main.py`), AIDecision fields
- [x] `docs/OBSERVABILITY.md` (NEW) — 4-surface overview table; audit trail (Layer B); AI decision log (Layer A); Prometheus (Layer B + the llm_requests exception); LangSmith (Layer A, planned/not-wired, egress caveat); "For coding agents" section (code map + extension rules); cross-links
- [x] `CLAUDE.md` — pointer in AI Safety Invariant section + doc-sync "update when relevant" list
- [x] `README.md` — pointer atop Observability section
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## bootstrap — optional Prometheus install on controller node (2026-05-29, COMPLETE)

- [x] Decide approach (user delegated): binary+systemd (any distro, no Docker dep), Prometheus-only (no Grafana), opt-in prompt
- [x] Confirm agent metrics port = 9090 (`settings.metrics_port`, `ERRANDER_METRICS_PORT`) → Prometheus must use a different port → 9091
- [x] `scripts/install-prometheus.sh` (NEW) — arch detect, download official binary, `prometheus` user, /etc/prometheus + /var/lib/prometheus, scrape config (job `errander-agent` → localhost:9090/metrics), `promtool check`, systemd unit `--web.listen-address=:9091`, enable+start, health verify; idempotent; env overrides PROM_VERSION/AGENT_METRICS_PORT/PROM_PORT
- [x] `scripts/bootstrap.sh` — `SCRIPT_DIR` at top (before cd); renumber step labels `/8`→`/9`; new opt-in step 8/9 (prompt, default No, skip on non-tty) calling install-prometheus.sh before the service-user move; Done banner points to standalone script
- [x] `-f` guard (not `-x`) since invoked via `bash <script>`; confirmed sibling scripts are mode 100644 in git
- [x] `bash -n` syntax check both scripts → OK
- [x] `README.md` — install subsection recommends the script (binary, port 9091) instead of manual apt
- [x] `SETUP.md` — bootstrap bullet + new "Monitoring the agent with Prometheus" section + two-directions disambiguation note
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## README — observability section rewrite (2026-05-29, COMPLETE)

- [x] Verify against code first: grep `errander/execution/` for LLM refs (none → Layer B clean); confirm `start_metrics_server`, `_metrics_handler`, 10 metric singletons exist + wired from `main.py:2213`
- [x] `README.md` — two-layer observability table (Layer A reasoning → AI Decisions UI / LangSmith; Layer B actions → Prometheus + audit)
- [x] `README.md` — "Installing Prometheus on the controller node" subsection: scrape config (`localhost:9090`), install commands, two-relationships note (scrape vs. target node_exporter reads)
- [x] `README.md` — port-collision warning (Prometheus + agent UI both default `:9090`)
- [x] `README.md` — LangSmith optional subsection: off by default, not bundled, egress caveat, Layer-A-only
- [x] `README.md` — audit trail framed as the authoritative record of actions
- [x] Doc sync: STATUS.md, todo.md, lessons.md, command-log.md

---

## bootstrap creates errander-agent automatically (2026-05-28, COMPLETE)

- [x] `scripts/bootstrap.sh` — step 8: create `errander-agent`, `.ssh` dir, move repo, `chown`, rebuild venv as service user; copy uv to `/usr/local/bin`; step count 7→8; updated Done banner
- [x] `SETUP.md` — collapse Step 1a/1b/Option A/B into single Linux section: `bash bootstrap.sh` + `sudo su - errander-agent`

---

## bootstrap.sh — uv to /usr/local/bin (2026-05-28, COMPLETE)

- [x] `scripts/bootstrap.sh` — copy uv to `/usr/local/bin` after install so service user can reach it

---

## bootstrap.sh — uv sync --extra dev (2026-05-28, COMPLETE)

- [x] `scripts/bootstrap.sh` — `uv sync` → `uv sync --extra dev`
- [x] `SETUP.md` — Option A/B comments (later fully removed)

---

## README — explicit v1 target scope table (2026-05-26, COMPLETE)

- [x] Added "v1 target scope" table to "What Errander-AI Is — and Is Not" section
- [x] 8 target categories: Linux VMs ✅, bare-metal ✅, Docker-on-VM ✅, serverless ❌, managed cloud ❌, k8s ❌, PaaS ❌, Windows ❌
- [x] Closing paragraph: pattern is sound for cloud, but v2+ scope not a config flag

---

## add-target.sh — add target VMs without re-running configure.sh (2026-05-26, COMPLETE)

- [x] `errander/config/add_target.py` — interactive flow: show environments + VMs, pick env by number/name, prompt host/name/os_family, optional SSH verify, append + save
- [x] `scripts/add-target.sh` — thin bash wrapper (cd to repo root, guard for inventory.yaml, exec uv run)
- [x] `SETUP.md` — "Adding target VMs after initial setup" section with configure.sh vs add-target.sh decision table

---

## Approval UI overhaul — per-item approval + decision reasoning (2026-05-25, COMPLETE)

- [x] `errander/safety/approval.py` — `BatchApprovalResult` 3-tuple type; `vm_plans` + `approved_items` on `PendingApproval`; updated `register()`, `decide()`, `await_dual_approval()`
- [x] `errander/agent/graph.py` — `operator_approved_packages` BatchGraphState key; `_filter_patching_packages()` helper; updated `approval_gate_node` (3-tuple unpack, `approved_items` init) + `dispatch_current_wave`
- [x] `errander/observability/metrics.py` — per-item approval plan card (`_render_approval_plan`); Decision Reasoning tab (`_render_approval_reasoning`); username fix; expanded 35-entry `_EVENT_BADGE` map; new CSS
- [x] Updated 4 test files for `await_dual_approval` 3-tuple return: `test_approval.py`, `test_graph.py`, `test_plan_apply_flow.py`, `test_deferred_artifact.py`
- [x] 126 tests passing, ruff clean, mypy clean

---

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
**What's needed:** Project A (checkpointing) is already in place. ~~Also needs a durable `ApprovalManager` (currently in-memory)~~ — durable approvals shipped 2026-06-11 (`ApprovalRequestStore` + restart reconciler, §8d Step 2), which already covers the "crash-lost approvals" trigger without graph interrupt/resume. Design sketch in `tasks/post-review-implementation-plan.md §8` if true interrupt semantics are ever still wanted.

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
