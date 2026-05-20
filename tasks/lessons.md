# Errander-AI — Lessons Learned

## 2026-05-20 — When adding a DB migration, always update the migration count assertions in tests

Adding migration #4 broke two test assertions that hardcoded the expected version list and row count. Both are load-bearing: one asserts `[0, 1, 2, 3]`, the other `COUNT(*) == 4`. Both must be bumped atomically with the migration itself — treat them as the schema contract.

**How to apply:** grep for the current migration count in tests before adding any new migration and update them in the same change.

## 2026-05-20 — Always confirm where the UI ACTUALLY lives before editing

The user pointed at a Stitch project (`stitch.withgoogle.com/projects/695805871329192760`) as "the UI" and asked me to fix an SRE punch list. I almost spent an hour generating Stitch screens — but the real product UI is a 3.2k-line aiohttp server at `errander/web/server.py` with 11 page_* render functions. Stitch was visual-only reference; the SRE's review was of the running server.py output (the `.playwright-mcp/` snapshots confirmed this: relative URLs `/approvals`, `/audit`, etc. map directly to server.py routes).

**Why:** A confident user-provided pointer is not a verification. The user themselves said "I am not sure since sonnet did the work" — that was the cue to dig harder before acting.

**How to apply:** Before any UI edit, grep the repo for the route names / labels / page titles that the user is describing. Confirm I'm editing the artifact the reviewer actually saw. If a user names an external tool (Stitch, Canva, Figma, etc.) as "the UI", ask whether the production UI is also in-repo — many products keep design-tool prototypes in parallel with the real codebase.

## 2026-05-19 — Verification query scope must match comparison scope

When `verify_node` compares installed versions against all `approved_packages`, it must query `list_installed_versions` for ALL approved package names — not just `pending_updates`. Packages already at their target version are correctly skipped by `assess_node` and never added to `pending_updates`, so they won't appear in a `pending_updates`-only dpkg query. The comparison would then find them "missing" and fail.

Rule: query scope == comparison scope. If you compare against N items, you must query for N items. A narrower query is a silent false-negative source.

## 2026-05-19 — Approved-artifact mode must own all three phases: assess, execute, verify

For a true immutable execution artifact, the approved packages list must drive all three phases:
- **assess**: must NOT call `list_upgradable` — the approved artifact is the source of truth, not the current repo state. Use `list_installed_versions` for the approved package names and compare against targets.
- **execute**: must call `install_pinned`, not `upgrade_all`. Fail closed if `approved_packages` absent.
- **verify**: must check `installed == approved_target`, not just `installed != old_version`. A version that changed but landed at the wrong value still fails.

Fixing only execute was good. Fixing all three is what makes the contract honest.

## 2026-05-19 — "Plan enrichment with hash commitment" ≠ "immutable execution artifact"

Hashing the enriched plan (packages + versions) and verifying it at approval time is necessary but not sufficient. If live execution still calls `apt-get upgrade -y` instead of `apt-get install pkg=version`, the approved artifact is decorative — repos can change between approval and apply.

The fix has two parts that must both be present: (1) generate `install_pinned()` from the approved package list at execution time, and (2) wire the approved packages from the enriched plan preview into `PatchingGraphState`. Without (2), (1) is unreachable.

For deferred replay, hash integrity proves the stored text wasn't tampered with, but package drift between approval and window time is a separate concern handled by pinned execution failing closed at runtime if a version is unavailable.

Rule: when QA says "the feature is not truly closed," treat it as a P0 regression. Check every step in the data flow from approval to execution — not just the approval gate.

## 2026-05-19 — Glossary and workflow diagram must stay in sync with the actual codebase

When a new sub-graph or action is added to the agent, three places need updating in the glossary page: (1) the `_GLOSS` list (new term card), (2) the `_WF_JS` node popup text for the affected workflow node (e.g. Action Execution checks/note fields), and (3) the node sublabels in `_nodes` tuple list. Also check for internal ticket/sprint codes (like "P0-1") baked into badge text or sublabels — these are meaningless to operators and should be replaced with descriptive labels ("PRE-APPROVAL").

Also: when the supported LLM configuration changes (e.g. from "self-hosted only" to "any OpenAI-compatible endpoint"), update INFRA glossary terms to match. The glossary is documentation that operators read — stale architecture claims erode trust.

## 2026-05-19 — Page-level action buttons belong in topnav_extra only, not duplicated in section-hdr

When the layout() function already accepts topnav_extra for page-level actions, writing the same buttons again inside page_X()'s section-hdr creates a visible duplicate. Rule: page functions own content only — the topnav_extra in the handler owns the action buttons. Never put action buttons in both places.

Also: every page with less than ~60% viewport coverage feels abandoned. Sparse pages need a second section even if it's contextual/reference data. For a VM detail page, the fleet siblings are always relevant. For inventory, the environment breakdown is always relevant. For settings, the env vars reference is always relevant. If the primary content doesn't fill the screen, ask "what else would a user want to know here?"

## 2026-05-19 — Agent internals page needs a dedicated route, not a placeholder

When adding a page that lives under a nav section, the full wiring is: (1) page function, (2) route handler async def, (3) app.router.add_get() in create_app(). Stopping at step 1 leaves the route returning 404. Always write all three before considering a page "done". Verify with `uv run python -c "from errander.web.server import create_app; app = create_app(); ..."` to confirm route registration.

## 2026-05-20 — Pin fixture endpoint to live value when rendering time-series + current together

When a sparkline shows historical data AND a separate "current: X%" label, the last point in the history must equal X. Otherwise the sparkline visually diverges from the label — memory sparkline ends at 61% but label reads 78%, which looks like a bug even when both are technically correct from different sources.

Fix: pin `history[-1] = current_value` in the renderer. This is a display-layer concern, not a data concern — don't change the fixture; fix it at render time.

Generalizes to any "history + current" combo: wherever you show a time-series chart alongside a live scalar, always ensure the chart's endpoint is the same value as the scalar.

## 2026-05-20 — threshold lines make SRE charts actionable; they belong in every resource sparkline

A sparkline without context shows trends. A sparkline with dashed threshold lines (75% warn, 90% crit) lets an SRE read the chart in two seconds: "line crossed the orange" = "this is approaching incident territory." Both Prometheus and Metricbeat use this pattern. Always add threshold lines for CPU, memory, and disk utilization charts.

The threshold lines should be parameterizable (pass warn_pct/crit_pct to the helper) and scaled relative to the chart's actual data range, not the full 0–100% axis.

## 2026-05-19 — UI information gaps create operational risk — show the detail, don't hide it behind links

Every piece of data the agent generates should be visible inline, not tucked behind a "Details →" link that goes nowhere. The audit log had a `detail` field in every event object but the HTML table rendered 8 columns and left that field out entirely — visible only to someone reading the Python source.

Rule: when adding a detail/notes/summary field to a data model, immediately wire it into the table/card that shows that data. A link to "more detail" is only acceptable if there's a real page behind it. A broken link is worse than no link — it trains users not to click anything.

Also: when showing approval requests to operators, include all the context they need to make the decision inline. CPU/memory/load at the time of the request, what triggered it, and what happens if they reject — all in the card, not linked out.

## 2026-05-18 — Audit detail strings must use POST-execution state, not pre-execution assessment counts

`vm_graph.py` detail builders were reading `pending_updates` (packages found before patching) and `version_snapshot` (snapshot taken before patching) — both pre-execution counts. They both showed the same number, making the detail look like nothing happened. The real post-execution data (`changed_packages` — the dict of packages that actually changed version) was computed in `verify_patch_node` but only logged, never stored in state.

Rule: every action's detail string must come from the verification/outcome phase of the sub-graph, not the assessment/snapshot phase. Add a state field to capture it if one doesn't exist. The detail string must be able to answer "what happened?" not "what did we find?".

## 2026-05-18 — Test assertions against rendered text must be updated when UI labels change

When the UI nav was redesigned ("Approvals" → "Approval Queue", "Dashboard" → "Fleet Dashboard"), 5 Playwright tests kept the old link names. They passed during the redesign session because Playwright tests weren't run against the new UI HTML. Rule: after any UI text change (nav labels, button text, headings), search `tests/ui/` for the old strings and update them in the same commit.

## 2026-05-18 — LangGraph append-only reducers silently double entries when replacement nodes write to the same key

`enrich_plan_node` was designed to REPLACE the per-VM plans with enriched versions (exact package names/versions). It returned `{"vm_plans": enriched_plans}`. But `vm_plans` in `BatchGraphState` is annotated with the append-only `_merge_vm_plans` reducer — so the enriched list was appended to the raw list, doubling every entry. With 1 VM: `[raw_plan, enriched_plan]` = "2 VMs planned" for 1 physical VM.

Fix: write replacement data to a SEPARATE state key without a reducer (`enriched_vm_plans`). Add a `_effective_vm_plans(state)` helper that post-enrich consumers call — returns `enriched_vm_plans` if set, otherwise falls back to raw `vm_plans`. The append-only reducer stays intact for the fan-out planning phase.

Rule: whenever a LangGraph node is meant to REPLACE accumulated state (not extend it), it must write to a different key. Never return the same key as an append-only reducer field from a node that runs after the fan-out completes.

## 2026-05-18 — Stitch design work never makes it to the repo unless explicitly committed

Designed the "Sovereign Architect" UI in Stitch MCP but never applied it to the codebase — the Stitch project stays in Stitch as a mockup until someone writes the code. Next session found the old UI still running. Rule: after any design session in Stitch (or Figma, or Canva), immediately implement and commit the changes in the same session, or record a clear TODO item that the design work is pending code application.

## 2026-05-18 — Metrics server bind address defaults to 127.0.0.1 — public IP requires explicit config

`start_metrics_server()` defaults `bind_address="127.0.0.1"`. Accessing `http://<public-ip>:9090` fails silently even with firewall open — the socket never listens on the NIC. Requires `ERRANDER_UI_BIND=0.0.0.0` in `.env`. The non-loopback path also requires `ERRANDER_UI_USER` + `ERRANDER_UI_PASSWORD` (enforced by `metrics.py:1593`).

## 2026-05-18 — Lock files are at `.errander-locks/` (CWD-relative), not `/var/lib/errander/locks/`

`main.py:340` initialises `FileLocker(lock_dir=Path(".errander-locks"))` — relative to the working directory the agent process was launched from (`/errander`). The docstring example shows `/var/lib/errander/locks` but that is not what the code uses. To clear a stale lock: `rm /errander/.errander-locks/<env>_<vm>.lock`.

## 2026-05-18 — sudo-check all required_binaries causes false blocks for read-only commands

`check_target()` tested `sudo -n {binary} --version` for every binary in the action manifests, but many binaries (e.g. `/usr/bin/find`, `/usr/bin/stat`, `/bin/systemctl`) never go through `sudo -n` in real execution — they run as the errander user without privilege escalation. This caused `--check-targets` to report `sudo -n denied for: /usr/bin/find` and block targets that were actually ready.

Fix: use `PRIVILEGED_PATHS.values()` from `privilege.py` as the authoritative set of binaries that need sudo. Skip the sudo check for any binary not in that set. `PRIVILEGED_PATHS` is the single source of truth — if a binary goes through `privileged()` at execution time, it's in that dict.

Rule: when adding a new action that has read-only binaries, ensure they do NOT appear in `PRIVILEGED_PATHS` — that's the signal that skip the sudo check.

## 2026-05-18 — aiosqlite execute_fetchall returns Iterable[Row], not list[Row]

`execute_fetchall` is typed as returning `Iterable[Row]`, so mypy rejects direct integer indexing (`rows[0][0]`). Fix: wrap with `list()` before indexing: `list(await db.execute_fetchall(...))`. Iterating with `for row in rows:` works without the wrap. When writing mypy-strict async SQLite code, use `for row in rows` when possible; only wrap with `list()` when you must index.

## 2026-05-18 — async context manager double-await kills aiosqlite thread

`await aiosqlite.connect()` starts the connection thread. Using the result in `async with ... as db:` calls `__aenter__` which tries to start the thread again → `RuntimeError: threads can only be started once`. Fix: either use `aiosqlite.connect()` (no await) as the `async with` target, or use `@asynccontextmanager` to wrap the open connection. Never do `async with await connection`.

## 2026-05-18 — mypy loop narrowing: re-annotate loop variable with str | None

When a variable is first assigned `str` in a loop and then narrowed by `if x is None: continue`, mypy thinks the variable has type `str` in subsequent iterations. Assigning `str | None` on the next loop start then conflicts. Fix: either annotate explicitly `x: str | None = ...` at the top of the loop body, or rename the variable to break the cross-iteration scope tracking.

## 2026-05-18 — ALTER TABLE in shared migrations is unsafe when table may not exist yet

When `ai_decisions` is created by `AIDecisionStore` (not in the shared `_MIGRATIONS` list), adding an `ALTER TABLE ai_decisions` migration to `_MIGRATIONS` means `AuditStore.initialize()` (which calls `run_migrations()`) will try to alter a table that may not exist yet — crash on first startup. Fix: keep `ai_decisions` schema evolution inside `AIDecisionStore.initialize()` using `contextlib.suppress(aiosqlite.OperationalError)` for each `ALTER TABLE ... ADD COLUMN`. This is idempotent: OperationalError is raised if the column already exists (fresh install created it via `_CREATE_TABLE_SQL`), suppressed silently.

## 2026-05-18 — MagicMock breaks json.dumps for attributes fetched via getattr

`getattr(mock_obj, "_temperature", None)` returns a MagicMock, not None — the `default=None` only fires if the attribute doesn't exist at all. `json.dumps({"temperature": MagicMock()})` then raises `TypeError: Object of type MagicMock is not JSON serializable`. Fix: normalize via a type guard before encoding: `_as_float(val)` returns `float(val)` if `isinstance(val, (int, float))` else `None`.

## 2026-05-18 — ruff TC rules give false positives in test files (pytest fixture injection)

Ruff's TCH (type-checking imports) rules suggest moving imports to `TYPE_CHECKING` blocks when they're only used in annotations. In test files, `pytest` is imported for `LogCaptureFixture` annotations — if moved to `TYPE_CHECKING`, pytest's fixture injection (which calls `get_type_hints()` at runtime) will fail. Fix: add `"tests/**/*.py" = ["TCH"]` to `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`.



## 2026-05-17 — Pydantic model_validator(mode="before") receives raw dict from YAML

When YAML loads a nested structure, Pydantic's `mode="before"` validator receives the raw Python dict before field coercion. Check for key existence with `isinstance(data, dict) and "key" in data` — don't try to access `data.key`. The `mode="after"` validator runs on the already-constructed model instance and can read `self.field_name` normally.

## 2026-05-17 — from __future__ import annotations makes all string return-type annotations redundant

`-> "EnvironmentSchema"` is the same as `-> EnvironmentSchema` when `from __future__ import annotations` is present (ruff UP037 catches this). Remove quotes in model_validator return types to stay ruff-clean.

## 2026-05-17 — Circular import prevents privilege.py from importing BUILTIN_ACTIONS

`errander/execution/privilege.py` imports from subgraph modules. `errander/agent/subgraphs/__init__.py` imports from those subgraph modules. If `privilege.py` imported BUILTIN_ACTIONS (from `subgraphs/__init__.py`), which imports from `disk_cleanup.py`, which imports from `privilege.py` → circular import. Solution: do manifest-based lookups in `vm_graph.py` (which already imports BUILTIN_ACTIONS lazily in the node function body) rather than in `privilege.py`. Never put cross-layer imports in shared utility modules.

## 2026-05-17 — ruff I001 import sort: `errander.agent` before `errander.models`

When two first-party imports from the same package are in a test file, ruff's isort (I001) enforces alphabetical sort by full module path. `errander.agent.subgraphs...` < `errander.models...` is correct (a < m), but ruff may still flag it if the import block formatting doesn't match its expectations exactly. Use `ruff check --fix` to auto-fix rather than guessing.

## 2026-05-17 — ruff checks shell scripts as Python if you pass them explicitly

`uv run ruff check scripts/install-docker-wrappers.sh` fails with hundreds of `invalid-syntax` errors because ruff tries to parse `.sh` files as Python. Always pass only Python file paths or directories containing Python files. Run `uv run ruff check tests/scripts/` to check the Python test files for a shell script.

## 2026-05-17 — Remove unused imports even in test files before committing

`subprocess` and `pytest` were left as unused imports in `tests/scripts/test_install_docker_wrappers.py` (likely from a template). ruff F401 catches these. Always run ruff on new test files before the first commit.

## 2026-05-17 — Action opt-in schema is incomplete without enforcing it in the planner

Introducing `actions.enabled` in the schema is not sufficient — the planner (`prioritize_actions`) also needs to receive the enabled list as `available_actions`. Without that wire-up, disabled actions still appear in plans because `available_actions=None` silently falls back to `DEFAULT_PRIORITY`. The same pattern applies to binary readiness checks: `check_target` needs to know which actions are enabled to avoid blocking on binaries for disabled actions. Enforcement gaps like this are easy to miss if the schema tests and the planner tests live in separate files with no integration test spanning both layers.

## 2026-05-17 — RUN.md must be updated whenever a new CLI flag is added

`RUN.md` is the operator reference for all CLI commands. Any new `--flag` added to `main.py` must be documented in both the `## Common CLI flags` table and a dedicated section in `RUN.md`. This was missed for `--migrate-inventory` (commit 1.2) and `--restart-service`/`--unit`/`--vm`/`--vms` (commit S.3). Add a RUN.md check to the pre-commit checklist for every commit that touches `main.py`.

## 2026-05-17 — Edit tool requires exact whitespace match; read file before editing comments

When adding to YAML comment blocks, the Edit tool requires the old_string to match exactly — including every `#` character, every space, and every trailing space or blank line. Attempts to match comment lines without reading the file first fail with "String to replace not found." Always read the target section with Read before any Edit, especially in annotation-heavy YAML files.

## 2026-05-17 — New SSH calls in run_check_targets only trigger when action is enabled

When adding direct `ssh_manager.execute()` calls inside `run_check_targets` (for the allowlist drift check), existing tests don't break if the new code path is gated by `if service_restart_cfg and service_restart_cfg.enabled:`. Existing test inventories have `service_restart.enabled: False` (the default), so the SSH call is never reached. New tests for the drift check use inventories with `service_restart.enabled: True` and must mock `SSHConnectionManager.execute` at the class level.

## 2026-05-17 — Lazy import inside validator body avoids circular dependency risk without refactoring

`BUILTIN_ACTIONS` in `errander/agent/subgraphs/__init__.py` imports from subgraph modules, which import from `errander/models/manifest.py`. The config schema doesn't import from subgraphs at module level — importing BUILTIN_ACTIONS inside the `model_validator` body prevents any future accidental import cycle if someone adds a cross-dependency.

## 2026-05-17 — LangGraph Send() fan-out requires explicit field forwarding — parent state is NOT inherited

In LangGraph's fan-out pattern, `Send("node_name", payload_dict)` delivers exactly `payload_dict` as the invoked node's state. Fields in the parent `BatchGraphState` (e.g., `enabled_actions`) are not automatically available in the per-VM node — they must be explicitly copied into the Send payload. Any schema field added to `BatchGraphState` that downstream nodes need must be threaded through every `Send()` call in `_route_plan_vms` (or equivalent router). When writing tests for fan-out routing, test `route_plan_vms()` directly and assert on `send.arg` keys rather than relying on integration tests that may not exercise this path.

## 2026-05-17 — Passing [] vs omitting a key in Send payload has different semantics in plan_vm_node

`plan_vm_node` reads `state.get("enabled_actions")` and checks `if _enabled_names is not None`. Passing `enabled_actions=[]` via Send means `_enabled_names = []` → `available_for_planning = []` → `prioritize_actions` plans zero actions. Omitting the key means `_enabled_names = None` → `available_for_planning = None` → DEFAULT_PRIORITY fallback. Always omit the key (not send `[]`) when the intent is "operator hasn't configured opt-in — use defaults."

## 2026-05-17 — Wrapper probes must be manifest-driven, not action-specific special cases

`check_target()` initially had a docker_prune-specific wrapper probe block. When service_restart was added (also a wrapper-based action), it had no probe — an easy miss because it wasn't in the docker block. The fix: add a generic loop over `BUILTIN_ACTIONS` that probes `required_wrappers` for all enabled non-docker actions. Docker stays special-cased only because it has a `command_mode` concept (`wrapper` / `direct_sudo` / `disabled`). Any future action with `required_wrappers` is now covered automatically by the generic loop without code changes.

## 2026-05-17 — Hand-written binary tables rot; derive from manifests instead

`_ACTION_BINARIES` in `target_validation.py` listed wrong binaries for `disk_cleanup` (`truncate`, `cp`) and `backup_verify` (`cp`) because the table was written from memory, not from the actual action manifests. `BUILTIN_ACTIONS` registry is the single source of truth for `required_binaries` — any helper that maps action → binary must import from there, not maintain its own table. Always add a manifest-consistency test when adding a new action.



Self-improvement log. Updated after corrections, mistakes, and surprises.

---

## 2026-05-14 — Unconditional SUCCESS after execute is a silent corruption pattern

`status = SUCCESS if not dry_run` without checking `result.success` means a failed SSH command is logged as a success in the audit trail. All execute_nodes must check `result.success` explicitly; there is no safe default.

## 2026-05-14 — Trailing `true` in shell scripts masks the real exit code

`KERNEL_PKGS=...; apt-get upgrade -y; ...; true` always exits 0. The fix: capture the important exit code (`APT_RC=$?`), suppress only the cleanup step's failure (`|| true`), then `exit $APT_RC`.

## 2026-05-14 — Fail-open probe checks are as dangerous as no check at all

`validate_no_pkg_lock` treated SSH probe failure as "clear." In live mode, unknown lock state = block patching; in dry-run, unknown state is fine. Always distinguish live vs dry-run when the consequence of guessing wrong is a real production action.

## 2026-05-14 — Distinct rollback terminal states are required for autonomous agents

Returning `FAILED` whether rollback succeeded or failed means operators and downstream automation can't distinguish "safe to redeploy" from "manual intervention required." Every action with a rollback path needs at least `ROLLED_BACK` and `ROLLBACK_FAILED` terminal states.

## 2026-05-14 — Tests using default dry_run=True silently pass when code skips live-only paths

After adding a `if not dry_run: continue` guard, tests that were implicitly relying on live behavior still "passed" because the expected result was empty/default. Always check whether a test's dry_run default matches what it's actually testing.

---

## 2026-05-14 — Library code ≠ production feature: stores must be wired through every layer

All SRE signal stores (`VMDiskHistoryStore`, `BaselineStore`, `VMStateStore`) were implemented with tests, but `async_main` never initialized them and the graph builder chain never received them. Nodes silently no-op when stores are `None` — no errors, just silent non-execution.

**Rule**: for every new optional dependency injected into a node, trace the full path from `async_main` to the node and write a wiring test on the same commit. Silent no-ops are the hardest bugs to notice.

## 2026-05-14 — Per-VM config fields need to flow through TargetSchema → target dict → VMGraphState

`critical_services` was in `TargetSchema` and `PatchingGraphState`, but the `yaml_targets` dict literal never included it. Tests that injected it directly passed; production inventory never carried it.

**Rule**: when adding a field to `TargetSchema`, grep all `yaml_targets` dict literals in `main.py` and add it there too. Also add a default for `db_additions` (those have no inventory schema).

## 2026-05-14 — Grep patterns must match what the parser can actually consume

`failed_logins_command` included `'authentication failure'` in grep, but `_FAIL_RE` couldn't parse PAM-format lines. Lines were fetched but never counted — silent under-report.

**Rule**: every grep pattern in a command string must have a corresponding regex branch in the parser. Don't fetch what you can't count.

## 2026-05-14 — `@web.middleware` decorator is mandatory — omitting it causes 500 on every request

**`_csrf_middleware` was defined as `async def` without `@web.middleware`.** aiohttp requires the decorator to register a function as middleware. Without it, the function is a plain coroutine, not a `Middleware` object, and passing it in `middlewares=[...]` fails with `AttributeError: 'Application' object has no attribute 'method'` on every request.

**Rule**: every aiohttp middleware must have `@web.middleware`. After writing or copying a middleware function, always check the decorator is present before registering it.

## 2026-05-14 — CSRF injection helper that modifies a local variable is a silent no-op

**`_inject_csrf` modified `html` as a local variable and returned `(token, nonce)`.** The caller received the token but the modified HTML (with hidden `<input>` fields injected) was thrown away. Forms were rendered without CSRF tokens — every POST after fixing the middleware would have been rejected with 403.

**Rule**: when a helper modifies a string and the caller needs the result, the function must return the modified string. Verify the return type covers every output the caller depends on. `str` is immutable — mutating a local alias does nothing to the caller's copy.

## 2026-05-14 — API keys must never appear in GET query params

**`/ui/settings/test-llm` accepted `api_key` as a GET query parameter.** Query params land in server access logs, browser history, proxy logs, and `Referer` headers. Any of these leaks the key to unintended parties.

**Rule**: any endpoint that accepts a secret (API key, token, password) must use POST with a request body. Never put secrets in URLs, even for "convenience" test endpoints.

## 2026-05-14 — All untrusted data must be escaped before HTML interpolation

**Batch detail, VM detail, inventory rows, and flash messages interpolated raw DB values and URL query params directly into HTML.** A crafted `vm_id`, `detail`, or `flash` param could inject arbitrary HTML/JS into operator pages — including approval and settings pages.

**Rule**: apply `html.escape()` to every value that came from outside the trusted code path (DB, URL params, form fields, SSH output) before it enters an HTML string. Add a module-level alias `_esc = html.escape` so escaping is one character shorter to type than skipping it.

## 2026-05-13 — Duplicate URL in NAV_ITEMS causes both items to highlight as active

**Two nav items ("Active Batch" and "Batch History") pointed to the same URL `/batches`.** The active-state check `url == active_url` matched both, so visiting `/batches` highlighted two items simultaneously — confusing and unprofessional.

**Rule**: every URL in `NAV_ITEMS` must be unique. Before adding a nav item, grep the list for duplicate URLs. If two entries logically represent the same destination, keep only one (or give one a distinct route/anchor).

## 2026-05-13 — Dead code in helper functions goes unnoticed without a call-site check

**`sidebar()` and `_sidebar_nav()` were never called by `layout()`.** `layout()` had its own inline nav loop. The dead functions accumulated over sessions and were shipped in two commits before the audit caught them.

**Rule**: after writing a new helper function, immediately confirm it's called somewhere. `grep -n "function_name(" file.py` takes 2 seconds and prevents dead code from living in the codebase across multiple commits.

---

## 2026-05-14 — scheduled_jobs must cover systemd timers, not just cron

On modern Linux (Ubuntu 18.04+, RHEL 7+), many scheduled tasks run as systemd timer units, not cron entries. Capturing only `crontab -l` / `/etc/cron.d/*` misses all of these — logrotate, apt-daily, and package manager timers all use systemd.

**Rule**: any "scheduled jobs" drift check must include `systemctl list-timers --all --no-legend --no-pager | awk '{print $NF}'`. The awk is critical — it extracts only the unit name column; the "next trigger" timestamp changes every time the timer fires and would produce false drift if included.

## 2026-05-14 — Listening ports canonicalization must strip PIDs, not just sort

`ss -tlnp` includes `users:(("sshd",pid=1234,fd=4))` in every row. Sorting and stripping the header is not enough — if sshd restarts, the PID changes and every restart produces a false drift alert.

**Rule**: whenever capturing state for drift comparison, audit the output for *ephemeral* fields that change without the thing actually changing. For `ss`/`netstat`: strip `pid=\d+` and `fd=\d+` with a regex before hashing. Keep the process name (it signals a new service) but drop the transient numeric identifiers.

## 2026-05-14 — Example configs must document all wired settings

`schema.py` and `settings.py` fully wired `sre_signals:` but `example/settings.yaml` had no reference block. Operators reading the example file had no way to know the section existed or what the defaults were.

**Rule**: every new settings block added to `schema.py` must be mirrored in `example/settings.yaml` with annotated comments on the same commit. Doc debt compounds — add it at implementation time.

## 2026-05-14 — Per-VM opt-out tags need to flow through every Send() path

`disable_failed_login_check` was added to `TargetSchema` and `VMGraphState` but if it's not threaded through both `Send()` call sites in `graph.py` (`route_after_validate` AND `dispatch_current_wave`) the flag silently has no effect in production (wave-based dispatch path).

**Rule**: when adding a new per-VM flag, grep every `VMGraphState(...)` constructor call and add the field to all of them. The two fan-out paths often diverge in test coverage.

---

## 2026-05-13 — SQLite UNIQUE constraint on timestamps breaks tests when two saves happen in the same microsecond

Adding `UNIQUE(vm_id, baseline_kind, scope_key, captured_at)` to `vm_baselines` seemed reasonable to prevent duplicates, but two `await store.save()` calls within the same `async def test_*` body resolve to the same `datetime.now(UTC).isoformat()`. This causes `IntegrityError` in tests even though production code (saves spaced seconds apart) would never hit it.

**Rule**: don't add UNIQUE constraints on auto-generated timestamp columns. The auto-increment `id` already ensures row uniqueness. Uniqueness on "latest per group" is enforced at query time (`ORDER BY captured_at DESC, id DESC LIMIT 1`), not at write time.

## 2026-05-13 — `ORDER BY captured_at DESC LIMIT 1` is non-deterministic when rows share the same timestamp

When two rows have identical `captured_at`, SQLite returns whichever it encounters first in its internal order — not necessarily the one inserted last. Tests that verify "latest capture" were flaky.

**Rule**: always use `ORDER BY captured_at DESC, id DESC` so that the highest auto-increment `id` (most recently inserted) breaks timestamp ties deterministically. Apply the same tiebreaker in `DELETE ... WHERE id NOT IN (SELECT id ... ORDER BY ... LIMIT ?)`.

## 2026-05-13 — aiosqlite.Row, not `object`, for row converter helpers

Typing row converter helpers as `row: object` satisfies the call site (execute_fetchall returns `list[aiosqlite.Row]`) but makes mypy flag every `row[i]` access with `"Value of type 'object' is not indexable [index]"`. Changing to `row: aiosqlite.Row` resolves all indexing errors and matches the pattern used in existing helpers like `deferred.py`.

**Rule**: row converter helpers take `row: aiosqlite.Row`, not `row: object`. Add `import aiosqlite` or `from aiosqlite import Row` accordingly.

## 2026-05-12 — Plan hash must cover action params, not just action types

**`plan_vm_node` serialized only `{"action_type": ..., "risk_tier": ...}` — dropping `params`.** This meant two plans that differ only in params (e.g., different package lists, different thresholds) produced the same hash. The operator approved a hash but execution could run with different parameters than what was shown.

**Rule**: every field that affects what the agent actually does on a live VM must be included in the plan artifact, the plan hash, and the operator approval summary. Omitting any field from the hash weakens the immutability guarantee.

## 2026-05-12 — Empty list is falsy: use an explicit sentinel for "plan was set"

**`if state.get("planned_actions"):` is `False` for both `None` and `[]`.** When the batch-level approved plan is an empty list (operator approved "do nothing" for this VM), the VM graph treated it the same as "no plan injected" and fell back to re-planning — violating plan/apply immutability.

**Rule**: never use truthiness of a list to distinguish "value was explicitly set to empty" from "value was never set." Use an explicit boolean sentinel (`pre_approved_plan_set: bool`) or `Optional[list]` where `None` means "not set" and `[]` means "set to empty."

## 2026-05-12 — frozenset iteration makes BOTH cache variants execute, not just one

**`ALLOWED_CLEANUP_PATHS` contains both `"apt-cache"` AND `"yum-cache"`. The assess loop iterates ALL paths via `path in ("apt-cache", "yum-cache")` — but both paths execute the SAME `pkg_mgr.cache_size()` command.** This means disk_cleanup makes 11 SSH calls (6 assess: df + 5 paths, 5 execute: simulate per path), not 9. Tests that only mocked 9 responses passed spuriously because the `_run_disk_cleanup` broad `except Exception` swallowed `StopAsyncIteration` from the exhausted mock and returned FAILED status, which some tests don't assert on.

**Rule**: when counting expected SSH calls in integration tests, iterate ALL paths in `ALLOWED_CLEANUP_PATHS` (frozenset, order varies) including duplicates like both cache paths. Always use an explicit counting script (`counting_execute`) before finalizing test mock sizes.

## 2026-05-12 — LangGraph conditional edges must enumerate ALL possible return values

**`route_after_drift_check` was added to return `"dispatch_action"` (skip re-planning when pre-approved plan exists), but the graph was wired as `add_conditional_edges("drift_check", ..., ["plan_actions", "audit_results"])`.** LangGraph validates returned strings against the `ends` dict. Returning `"dispatch_action"` raised `KeyError: 'dispatch_action'` at runtime.

**Rule**: whenever a routing function's return set expands, update the `add_conditional_edges` call to include ALL possible return values. Check both the function body AND the edges registration together.

---

## 2026-05-11 — Patch the module where a symbol is used, not where it is defined

**When a function does a deferred `from X import Y` inside its body (like `rollback_node` doing `from errander.safety.rollback import rollback_action`), patching `errander.safety.rollback.rollback_action` patches the source but the function still gets the original via its local import.** The correct patch target is always where the name is *looked up*, not where it is *defined*. For deferred imports, that means patching `errander.safety.rollback.rollback_action` which IS the source module — and since the import happens at call time, patching the source module works. The failure mode here was the reverse: patching `errander.agent.subgraphs.patching.rollback_action` when the symbol is never bound at module level in that file (it's imported inline), so `patch` raises `AttributeError: module does not have attribute`.

Rule: if the function imports inside its body, patch the *origin* module. If the function imports at module level (`from X import Y` at the top), patch the *consumer* module.

## 2026-05-11 — ApprovalManager.decide() removes the entry — wait before decide, not after

**`ApprovalManager.decide()` pops the batch from `_pending`. If you call `decide()` before `wait_for_decision()`, the waiter raises `KeyError` because the entry is gone.** The correct test pattern: call `wait_for_decision()` first (it awaits the event), then decide from a background task that runs concurrently. Use `asyncio.create_task()` to schedule the decide call before awaiting the wait.

## 2026-05-11 — Test mocks that count SSH calls break when a new phase adds more calls

**When a new architectural phase adds SSH calls to the graph (e.g., a planning fan-out), integration tests that track `call_count` to decide success/failure per call silently shift meaning.** In `test_wave_abort_stops_fleet_at_boundary`, the mock was `_ssh_ok() if call_count <= 15 else _ssh_ok("", 1)`. Adding the 12-VM planning phase added 12 SSH calls between validate_targets and the health checks, so the original 15-call threshold was hit mid-planning, causing wave 0's health check to fail instead of wave 1.

Fix: always comment the call breakdown explicitly (`# 12 validate + 12 plan_vm + 3 wave-0 health = 27 succeed`) and update the threshold when new phases add SSH calls.

## 2026-05-11 — MagicMock(spec=...) does not auto-populate attribute values

**`MagicMock(spec=Settings)` creates a mock that only allows access to attributes defined on `Settings`, but it does NOT give those attributes meaningful values — accessing `settings.audit_db_url` returns another `MagicMock`, not a string.** When new code accesses a real attribute of a mocked object for the first time (e.g. to create a database connection), the test crashes with a type error or DB path error. Fix: explicitly set the needed attribute values on the mock (`settings.audit_db_url = ":memory:"`).

## 2026-05-11 — SSH call counts in integration tests must account for ALL SSH layers

**When a new graph node uses SSH (e.g., validate_targets switching from `echo ok` to `cat /etc/os-release`), AND a downstream node also uses SSH (plan_vm calling detect_os = 5 SSH calls), the total call count in count-based mocks changes multiplicatively.** For 10 VMs: validate_targets = 10 calls, plan_vm = 10×5 = 50 calls. A mock keyed on "first N calls succeed" needs the full N recalculated.

**Always comment the breakdown explicitly**: `# 10 validate (os-release) + 10×5 plan_vm (detect_os) + 1 canary health = 61 succeed`.

## 2026-05-11 — aiohttp mock setup methods must be coroutines

**When mocking `aiohttp.web.AppRunner` and `TCPSite` in tests, `setup()` and `start()` are awaited by the real code.** Using `lambda: None` (a synchronous callable) causes `TypeError: object NoneType can't be used in 'await' expression`. Always use `AsyncMock()` for async methods when patching aiohttp infrastructure.

## 2026-05-11 — shlex.quote behaviour is platform-dependent in tests

**`shlex.quote` on Windows does not wrap strings that contain no shell metacharacters** — it returns the original string unchanged. Tests that assert `result != original_string` to verify quoting was applied will fail on Windows even though the implementation is correct. The right test is: validate that metacharacter inputs raise, and that safe inputs pass through without error — not that the output is wrapped in quotes.

## 2026-05-11 — Deferred execution semantics: dry-run vs live

**Dry-run and live have opposite deferral semantics.** Old behavior was: dry-run outside window → defer (schedule for later). New (correct) behavior: dry-run is always immediate (sandbox, window-agnostic); live runs outside window → defer. Tests written for the old semantics needed to be inverted — a test named "dry run outside window defers" became "dry run outside window never defers", and "live run not deferred regardless of window" became "live run deferred when outside window".

**When redesigning approval/deferral logic, check ALL existing tests for semantic inversion** — not just the tests that immediately fail. The docstrings describe OLD behavior and can mislead.

---

## 2026-05-10 — apt list --upgradable needs apt-get update first

**`apt list --upgradable` on a fresh VM returns zero results without a prior `apt-get update`** — the local package index starts stale (or empty). Without refreshing it, `list_upgradable` correctly reports nothing upgradable, but only because it hasn't checked upstream yet. This was caught during live dry-run validation: a freshly provisioned Azure VM showed 0 pending updates when the real answer was dozens.

Fix: `assess_node` must call `refresh_package_lists()` (`apt-get update -qq`) before `list_upgradable()`. Failure of the refresh is non-fatal — log a warning and continue with the stale index (better than blocking the whole batch on a transient network blip).

**When mocking a node that now makes 2 executor calls instead of 1, all test mocks must shift** — unit tests using `side_effect=[result]` need to become `side_effect=[refresh_result, list_result]`. Integration tests using `call_count` need their branch numbering shifted up by 1. The compiler will not catch this; only running the tests will.

---

## 2026-05-10 — configure.sh UX and Security Lessons

**Bash case `*` catches empty string** — when a prompt has a (Y/n) default, reading the value and immediately using `*` as the "yes" catch-all works in the first case block. But if a second case block later requires explicit `y|yes`, pressing Enter (empty) falls to the wrong branch. Fix: normalize with `VAR="${VAR:-y}"` immediately after `read` so both blocks see a consistent value.

**Two case blocks on the same variable must agree on what empty means** — if one treats empty as "yes" and another treats it as "no", the user's intent is silently misread. Always normalise the variable to an explicit value before the first case.

**"Keep existing + Add more" requires an append path** — when a script reads an existing file and the user says "keep it and add more", you cannot reuse the "write new file" branch. You need an explicit append branch (`>> file`), otherwise new entries are silently discarded.

**Secrets encryption key must be in a separate file from the encrypted values** — storing `ERRANDER_SECRETS_KEY` in the same `.env` as the `enc:v1:` blobs provides zero security benefit. Anyone who reads the file gets both. Key must live separately (`~/.errander.key`, chmod 600, loaded as a second EnvironmentFile).

**Never default a password to a placeholder silently** — `changeme` as a silent default means it reaches production unnoticed. Always prompt for credentials explicitly with a confirmation loop on fresh install; on re-run show the existing value as default so the user can accept or change.

**Script step headers imply work is about to happen** — showing `[3/5] SSH key pair` then immediately saying "already exists — skipping" is contradictory. Only show a step header when the step actually does something. For "nothing to do" cases, emit a single `ok` line with the step number inline.

**chmod 600 .env must be explicit** — shell redirection (`> .env`) creates files with the user's umask (often 644 on servers). Always follow up with `chmod 600` explicitly; never rely on umask being restrictive.

**A question covering two decisions is always confusing** — "Keep existing VMs and just add more? (Y/n)" forces users to infer: Y = keep AND add, N = discard AND don't add. Split into two independent questions with clear single-intent wording.

**Dry-run must never require approval** — gating a dry-run behind human approval defeats its purpose as a safe validation tool. Approval gates must check `dry_run` first and auto-approve when true.

---

## Phase 1.3 — LangGraph Node Wrapping

**Lesson**: Async nodes that need injected dependencies must use `async def` wrapper closures, not lambdas. LangGraph calls node functions and awaits them — a lambda that returns a coroutine object is not the same as an async function.

```python
# WRONG — lambda returning coroutine, not awaitable function
builder.add_node("assess", lambda s: assess_node(s, executor=executor))

# CORRECT — async def wrapper
async def _assess(state):
    return await assess_node(state, executor=executor)
builder.add_node("assess", _assess)
```

---

## Phase 1.5 — Pre-compile Shared Graphs Once

**Lesson**: Build and compile the per-VM `StateGraph` once in the builder, not once per VM at dispatch time. `StateGraph.compile()` is expensive. Capture the compiled graph in a closure and reuse it for all `Send()` fan-out invocations.

```python
# In build_batch_graph():
vm_compiled = build_vm_graph(executor, locker, audit_store, ssh_manager).compile()

def _route_after_validate(state):
    return [Send("run_vm", vm_state) for t in healthy]

async def _run_vm(state):
    return await run_vm_node(state, vm_compiled=vm_compiled)  # closure
```

---

## Phase 1.5 — LangGraph Send() Fan-Out

**Lesson**: `Send()` objects must be returned by **conditional edge routing functions**, not by graph nodes. Nodes must return dicts. If a node returns `list[Send]`, LangGraph throws `InvalidUpdateError: Expected dict, got [Send(...)]`.

```python
# WRONG — node returning Send objects
builder.add_node("fan_out", lambda state: [Send("run_vm", {...})])

# CORRECT — conditional edge routing function returning Send objects
def _route_after_validate(state):
    if not state.get("healthy_targets"):
        return "generate_report"
    return [Send("run_vm", vm_state) for t in state["healthy_targets"]]

builder.add_conditional_edges("validate_targets", _route_after_validate, ["run_vm", "generate_report"])
```

**Why**: In LangGraph's execution model, nodes write state updates (dicts). The graph scheduler reads routing functions to determine which nodes to activate next. `Send()` is a scheduler directive, not a state update — it belongs in the routing layer, not the node layer.

---

## Phase 1.6 — Module-Scoped Fixtures for Expensive Clients

**Lesson**: When testing clients that initialise expensive transports (e.g., `AsyncOpenAI` initialises an httpx async transport), use `pytest.fixture(scope="module")` to create the client once per module, not once per test.

```python
# SLOW — creates a new AsyncOpenAI (+ httpx transport) for every test
def _make_client() -> LLMClient:
    return LLMClient(base_url="http://...", ...)

# FAST — created once, shared across all tests in the module
@pytest.fixture(scope="module")
def llm_client() -> LLMClient:
    return LLMClient(base_url="http://...", ...)
```

**Why**: `AsyncOpenAI.__init__` sets up an httpx `AsyncClient` with connection pool configuration. At ~1.4s each × 23 tests = 57s. With module scope: 2 clients × ~1.5s setup = 3s total, ~0.01s per call.

---

## Phase 1.6 — aiohttp Async Context Manager Pattern

**Lesson**: `aiohttp` sessions use `async with session.post(url) as resp:` — the response is an async context manager, NOT an awaitable. Calling `resp = await session.post(...)` returns the CM object, not the response. Tests that mock `session.post` must return an async context manager, not the response directly.

```python
# WRONG — post() is not awaitable
resp = await session.post(url, json=payload)

# CORRECT — post() returns an async context manager
async with session.post(url, json=payload) as resp:
    data = await resp.json()
```

In tests, wrap mock responses with an async CM helper:

```python
def _ctx(resp: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm

mock_session.post = MagicMock(return_value=_ctx(mock_resp))
```

**Why**: `aiohttp` uses the CM protocol for connection lifecycle management (keeping the connection open until the body is read, then releasing it back to the pool). It's a deliberate API design, not optional.

---

## Phase 1.6 — Rate-Limit Retry: `continue` vs `break`

**Lesson**: In a `for attempt in range(N)` retry loop, use `continue` to retry, not `break`. `break` exits the loop and falls through to any post-loop code (e.g., a final `raise`). `continue` restarts the next iteration.

```python
# WRONG — break exits the loop, falls through to raise SlackError("failed after retries")
if attempt == 0:
    await asyncio.sleep(retry_after)
    break  # exits loop, hits the raise at the bottom

# CORRECT — continue restarts loop with attempt=1 (the retry)
if attempt == 0:
    await asyncio.sleep(retry_after)
    continue
```

**Why**: This bug is subtle because `break` looks like "stop waiting and retry" but it actually means "exit the loop entirely". Always use `continue` when the intent is to repeat the loop body.

---

## Phase 1.6 — aiohttp web.Response: content_type param vs Content-Type header

**Lesson**: `aiohttp.web.Response` raises `ValueError` if you pass both the `content_type` keyword argument AND a `Content-Type` key in the `headers` dict. Choose one or the other — not both.

```python
# WRONG — ValueError: passing both Content-Type header and content_type param
web.Response(
    body=output,
    content_type="text/plain",
    headers={"Content-Type": "text/plain; version=0.0.4"},
)

# CORRECT — set Content-Type via headers only
web.Response(
    body=output,
    headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
)
```

**Why**: aiohttp validates this at construction time to avoid ambiguous/conflicting headers. The same rule applies for `charset` — don't pass it if `Content-Type` already specifies it via `headers`.

---

## Phase 1.7 — APScheduler 3.x: next_run_time on Pending Jobs

**Lesson**: `APScheduler 3.x` uses `__slots__` on `Job`. When the scheduler hasn't been started yet, jobs sit in a pending list with `next_run_time` uninitialized — accessing it raises `AttributeError` even though the slot is declared. Use `getattr(job, "next_run_time", None)` when reading the attribute outside of a running scheduler context.

```python
# WRONG — AttributeError if scheduler not started
str(job.next_run_time) if job.next_run_time else "paused"

# CORRECT — safe read
next_run = getattr(job, "next_run_time", None)
str(next_run) if next_run else "pending"
```

**Why**: Python's `__slots__` mechanism doesn't initialize slots to any value — they simply don't exist until explicitly set. This differs from regular instance attributes where `__init__` typically sets all attributes.

---

## Phase 1.7 — APScheduler replace_existing: Jobstore vs Pending List

**Lesson**: `replace_existing=True` in APScheduler 3.x only deduplicates against the persistent jobstore — not the in-memory pending list used before the scheduler starts. Adding two jobs with the same ID to an unstarted scheduler results in two pending jobs. Don't test APScheduler internal deduplication behavior; it's not our logic.

---

## Pre-Phase 1.8 — Empty List vs None in Python Conditionals

**Lesson**: Use `x if x is not None else default` instead of `x or default` when `x` can be a legitimately empty list. `[] or default` returns `default` because empty list is falsy, silently ignoring the explicit empty input.

```python
# WRONG — treats empty list same as None
days or ["tuesday", "thursday"]   # [] → ["tuesday", "thursday"] (wrong!)

# CORRECT — only falls back on actual None
days if days is not None else ["tuesday", "thursday"]  # [] stays []
```

---

## Phase 1.7 — Python Timezone: UTC offset varies by DST

**Lesson**: When writing timezone-specific tests, check whether DST is active on the test date. Europe/Paris is CET (UTC+1) in winter, CEST (UTC+2) in summer. April dates use CEST (UTC+2), not CET (UTC+1). Always verify the UTC offset for the specific date in your test.

```python
# April 7 — CEST (UTC+2), NOT CET (UTC+1)
# 01:00 UTC = 03:00 CEST → outside window [04:00, 06:00)
# 02:00 UTC = 04:00 CEST → inside window [04:00, 06:00)
```

---

## Phase 4 — Patching Local Imports Requires the Module's Own Path

**Lesson**: When a function does a local import (`from errander.agent.graph import build_batch_graph` inside the function body), `patch("errander.main.build_batch_graph")` fails with `AttributeError` because the name never exists on `errander.main`. Patch the module where the symbol is *defined*: `patch("errander.agent.graph.build_batch_graph")`.

```python
# WRONG — name doesn't exist on errander.main (it's a local import)
with patch("errander.main.build_batch_graph", ...):

# CORRECT — patch where the function is defined
with patch("errander.agent.graph.build_batch_graph", ...):
```

---

## Phase 4 — logging.LogRecord With Dict Args Raises KeyError

**Lesson**: Python's `logging.LogRecord.__init__` immediately interpolates `args` into the message when `args` is set in the constructor. If `args` is a `dict`, it calls `msg % args`, which requires string-keyed format markers. Setting string-keyed `dict` args without matching `%(key)s` markers causes `KeyError`. Fix: construct the `LogRecord` with no args, then set `record.args = {...}` after construction so interpolation is bypassed.

```python
# WRONG — triggers msg % args in __init__, raises KeyError
record = logging.LogRecord("test", logging.INFO, "", 0, "msg", {"key": "val"}, None)

# CORRECT — set args after construction
record = logging.LogRecord("test", logging.INFO, "", 0, "msg", None, None)
record.args = {"key": "val"}
```

---

## Phase 4 — `load_settings()` Must Stay Synchronous

**Lesson**: `load_settings()` is called before the event loop in `_build_components()`. Don't make it `async` to query the DB directly — it breaks the sync call chain. Instead, accept `db_overrides: dict[str, str]` as a pre-fetched argument. The caller (`async_main`) fetches the overrides async and passes them in.

---

## Phase 4 — B017 Blind Exception: Use the Actual Exception Type

**Lesson**: Ruff B017 flags `pytest.raises(Exception)` as too broad. For SQLite `CHECK` constraint violations, the actual exception is `aiosqlite.IntegrityError`. Always use the specific exception class in `pytest.raises()`.

```python
# WRONG — too broad, hides what exception actually fires
with pytest.raises(Exception):

# CORRECT — names the exact exception
import aiosqlite
with pytest.raises(aiosqlite.IntegrityError):
```

---

## Phase 4 — Nested HTML Forms Break Submit Buttons

**Lesson**: HTML5 forbids nesting `<form>` elements. When aiohttp renders a reset `<form>` *inside* the main settings `<form>`, Chromium implicitly closes the outer form at the inner `<form>` tag. The Save button ends up outside any form and clicks on it do nothing — but only when a DB override exists (which triggers the reset form). Tests that click Save on a "fresh" page (no DB override) pass; tests after a prior save fail silently.

Fix: use the HTML5 `form="<id>"` attribute to associate the reset button with a standalone `<form id="...">` rendered *outside* the main form.

```html
<!-- outside main form -->
<form id="reset-ERRANDER_LLM_MODEL" method="POST" action="/ui/settings/reset">
  <input type="hidden" name="key" value="ERRANDER_LLM_MODEL">
</form>

<!-- inside main form's row, but associated to the out-of-band form -->
<button type="submit" form="reset-ERRANDER_LLM_MODEL" class="btn-del">Reset</button>
```

**Detection**: if a Playwright `locator.click()` on a submit button causes no navigation (even with `expect_navigation()` timing out at 30s), check for nested forms.

---

## Phase 1.8 — Setup Doc: Collect Credentials Before Creating the File

**Lesson**: When a step says "add to `.env`" but the file isn't created until the next step, users are stuck. The right pattern: the credential-collection step shows a "Your three values:" reference block and verifies connectivity inline (using env vars directly, not loading from a file). The file-creation step then says "paste the values from Step N here."

```bash
# Step 4 — verify inline, no .env needed
ERRANDER_LLM_BASE_URL=https://.../v1/ \
ERRANDER_LLM_MODEL=my-deployment \
ERRANDER_LLM_API_KEY=sk-... \
uv run python -m errander --check-llm

# Step 5 — now create .env with verified values
cat > .env << 'EOF'
ERRANDER_LLM_BASE_URL=<from step 4>
...
EOF
```

---

## Phase 1.8 — Commit Messages Must Be One Line

**Lesson**: Commit messages must be `type: short description` on a single line, under 72 characters. No body, no bullets, no blank lines. If multi-line messages are used the user will correct you — add this rule to CLAUDE.md so it persists across sessions.

---

## Phase 1.8 — Ollama Runs on CPU or GPU (Not CPU-Only)

**Lesson**: Ollama supports CPU, NVIDIA GPU, AMD GPU, and Apple Silicon — it auto-detects. The correct distinction between Ollama and vLLM is *ease of setup* (Ollama: single install, any hardware) vs *production throughput* (vLLM: NVIDIA GPU required, dedicated VM). Don't describe Ollama as "CPU-only."

---

## Phase 1.8 — Consolidate Related Steps; Don't Split Across Sections

**Lesson**: When two consecutive steps are tightly coupled (e.g., "collect Slack tokens" immediately followed by "put Slack tokens in .env"), merge them into one step with a subsection. Users who complete step N expect to act on its output immediately — making them hold values until a later step causes confusion and mistakes.

---

## Phase 4 — Playwright `locator.click()` Does NOT Auto-Wait for Navigation

**Lesson**: Playwright's `locator.click()` returns as soon as the click event is dispatched — it does NOT implicitly wait for a navigation that the click triggers. If you immediately call `page.goto()` after clicking a submit button, the browser may abort the in-flight POST request before the server writes to the DB.

```python
# WRONG — goto() may abort the POST before the server processes it
page.locator("button.btn-save").click()
page.goto("/ui/settings")  # POST aborted? DB not updated

# CORRECT — wait for the redirect to land before navigating elsewhere
page.locator("button.btn-save").click()
expect(page.get_by_text("Settings saved")).to_be_visible()  # waits for redirect
page.goto("/ui/settings")
```

OR use `with page.expect_navigation():` around the click for an explicit navigation wait.

---

## Phase 1.8 — Long echo One-Liners Break on Terminal Copy-Paste

**Lesson**: A shell `echo "..."` with a 200+ character string prints the full string, but the terminal wraps it visually. Users who copy the visible text get a truncated string — if it cuts mid-quote, the shell shows `>` waiting for the closing quote. The command silently fails.

Fix: never put long commands in terminal output. Instead, add a dedicated CLI flag and print the short flag:

```bash
# WRONG — wraps at terminal width, breaks on copy
echo "    uv run python -c \"from errander.config.schema import validate_inventory; from pathlib import Path; inv = validate_inventory(Path('inventory.yaml')); print('Targets:', sum(len(e.targets) for e in inv.environments.values()))\""

# CORRECT — short, safe to copy regardless of terminal width
echo "    uv run python -m errander --check-inventory"
```

**Rule**: any command shown in a script's final summary must be short enough to fit on one terminal line (~80 chars). If it doesn't fit, add a CLI flag for it.

---

## Phase 1.8 — `set -euo pipefail` + bare `grep` = silent script death

**Lesson**: `set -e` makes any non-zero exit code kill the script immediately, with no error message. `grep` exits 1 when it finds no match — which is a normal, expected outcome. A bare `grep` inside a `$()` subshell or pipeline under `set -e` silently kills the script the moment it finds nothing.

```bash
# WRONG — kills the script silently if grep finds no match
ENV_NAME=$(grep -m1 "^environments:" inventory.yaml | tail -1 | tr -d ' :')

# CORRECT — || true makes no-match a non-fatal outcome
ENV_NAME=$(grep -m1 "^environments:" inventory.yaml | tail -1 | tr -d ' :' || true)
```

**Rule**: every `grep` call in a `set -e` script must end with `|| true` unless you explicitly *want* a no-match to be fatal. This includes calls inside `$()`, pipelines, and `if` conditions (the `if` form is already safe — `if grep ...` doesn't trigger `set -e`).

---

## Phase 1.8 — Inline env var overrides don't inherit the full environment

**Lesson**: When calling a subprocess with inline env var overrides (`VAR=val cmd`), only the vars you explicitly list are added. Other env vars the subprocess needs — including ones set earlier in the same script via `export` — are still inherited from the parent shell. But if the script uses inline overrides to supply *some* vars and forgets one that's needed for decryption, the subprocess gets no key and crashes.

```bash
# WRONG — forgets ERRANDER_SECRETS_KEY; load_settings() sees enc:v1: with no key
ERRANDER_LLM_BASE_URL="$URL" uv run python -m errander --check-llm

# CORRECT — include the key so load_settings() can decrypt enc:v1: values
ERRANDER_LLM_BASE_URL="$URL" ERRANDER_SECRETS_KEY="${SECRETS_KEY:-}" uv run python -m errander --check-llm
```

**Rule**: when a command is invoked with inline env var overrides in a configure script, explicitly list every secret the command might need to decrypt — don't rely on environment inheritance being complete.

---

## Phase 1.8 — Early-exit CLI modes must run before load_settings()

**Lesson**: Modes like `--check-inventory`, `--generate-secrets-key`, and `--encrypt` don't need settings. Putting them after `load_settings()` means they fail if settings can't be loaded (e.g., encrypted values with no key). Always move no-settings-needed modes before `load_settings()`.

```python
# WRONG — load_settings() crashes before --check-inventory ever runs
settings = load_settings(...)
if args.check_inventory:
    return run_inventory_check(args.inventory)

# CORRECT — exit before settings are needed
if args.check_inventory:
    return run_inventory_check(args.inventory)
settings = load_settings(...)
```

---

## Phase 1.8 — configure.sh must reuse the existing secrets key, not regenerate it

**Lesson**: Generating a new `ERRANDER_SECRETS_KEY` on every re-run of `configure.sh` invalidates all `enc:v1:` values previously written to `.env`. The `encrypt_val` helper correctly passes through already-encrypted values unchanged, but those blobs were encrypted with the *old* key — the new key can't decrypt them.

```bash
# WRONG — new key every re-run; old enc:v1: values in .env become unreadable
_key_line=$(uv run python -m errander --generate-secrets-key ...)
SECRETS_KEY="${_key_line#ERRANDER_SECRETS_KEY=}"

# CORRECT — reuse existing key if one is already on disk; generate only when absent
if [ -f "$KEY_FILE" ]; then
    _existing=$(grep "^ERRANDER_SECRETS_KEY=" "$KEY_FILE" | cut -d= -f2-)
    [ -n "$_existing" ] && SECRETS_KEY="$_existing" && _encrypt=true
fi
[ -z "$SECRETS_KEY" ] && SECRETS_KEY=$(generate_new_key)
```

**Rule**: a secrets key is stable infrastructure — treat it like a signing certificate. Rotate it deliberately (with re-encryption of all stored blobs), never accidentally on re-run.

---

## Phase 1.8 — bootstrap.sh must use `uv sync --extra dev`, not bare `uv sync`

**Lesson**: `pytest`, `ruff`, and `mypy` live under `[project.optional-dependencies] dev`. A bare `uv sync` installs only the core runtime deps and leaves those tools absent. The verify step (`uv run pytest`) then fails with "No such file or directory" — no indication that deps are the cause.

**Rule**: any bootstrap or setup script that expects `pytest`/`ruff`/`mypy` to be available must run `uv sync --extra dev`. Surface this command explicitly in every place that lists verify steps (bootstrap.sh, configure.sh Step 6 output, SETUP.md).

---

## Phase 1.8 — Hardcoded future dates in tests go stale

**Lesson**: `WINDOW_START = datetime(2026, 4, 26, ...)` was "the future" when written but is now in the past. `get_pending()` filters `AND expiry_at > now`, so records with `expiry_at = 2026-05-03` are silently excluded on any run after that date. Tests pass on the dev machine one week, fail on a fresh VM the next.

**Rule**: never hardcode an absolute future date in tests. Use `datetime.now(tz=timezone.utc) + timedelta(days=N)` so expiry is always N days ahead. Even a "far future" hardcoded date (2030, 2050) will eventually become past.

---

## Phase 1.8 — Exported `.env` vars pollute pytest on VMs

**Lesson**: `export $(grep -v '^#' .env | xargs)` before running tests causes real values (`ERRANDER_LLM_MODEL`, `ERRANDER_UI_USER`, `ERRANDER_UI_PASSWORD`) to leak into tests that expect a clean environment. Tests that check default-empty behaviour or YAML/DB precedence see the real value instead and fail.

**Rule**: add an autouse fixture to `tests/conftest.py` that clears all `ERRANDER_*` env vars before every test. Tests that need specific values set them explicitly via `monkeypatch.setenv`. This makes the suite runnable regardless of what the shell environment contains.

---

## Phase 1.8 — Playwright tests need the browser binary, not just the Python package

**Lesson**: `uv sync --extra dev` installs `pytest-playwright` (the Python package) but NOT the Chromium binary. Running `uv run pytest` then ERRORs with "Executable doesn't exist" — the error message is cryptic and looks like a broken install. The binary requires a separate `uv run playwright install chromium` step (~150 MB download).

**Rule**: (1) add a `tests/ui/conftest.py` that skips playwright tests with a clear message when the binary is absent; (2) keep playwright install out of bootstrap.sh — end users don't need it.

---

## Phase 1.8 — Setup docs must distinguish end users from developers

**Lesson**: Setup steps that mix end-user deployment steps (check-inventory, dry-run) with developer steps (pytest, playwright, ruff) confuse both audiences. An end user who runs `uv run playwright install chromium` downloads 150 MB they'll never use. A developer who skips the dev section misses the test suite.

**Rule**: keep a single linear path for end users (Steps 1–N) covering only what's needed to run the agent in production. Add a separate "For developers" section at the bottom for the test/lint/type-check workflow. Scripts (bootstrap.sh, configure.sh) follow the same split — no dev tools in the end-user path.

## Phase A — sudo privilege model

**Lesson 1**: `/usr/bin/env` in a sudoers entry is a root bypass. `sudo env rm -rf /` works if `env` is allowed. Always remove `env` from privileged commands and use explicit flags (`-o Dpkg::Options::=--force-confold`) instead of environment variables.

**Lesson 2**: `replace_all=true` in Edit only matches exact occurrences. When two code blocks look similar but have different extra fields (e.g., one has `metadata=` and one does not), they won't be deduplicated by a single replace_all. Check the surrounding context of every occurrence separately.

**Lesson 3**: `f"docker_prune_{mode}"` produces wrong key when mode is `"direct_sudo"` (would give `"docker_prune_direct_sudo"` not `"docker_prune_direct"`). Never use f-string interpolation for dictionary key lookups when the mode name doesn't exactly match the key suffix. Use explicit conditionals.

**Lesson 4**: `load_inventory` from `config/inventory.py` returns `list[VMTarget]` (flattened). `validate_inventory` from `config/schema.py` returns `InventoryConfig` with `.environments`. When you need per-environment fields (like `docker_command_mode`), use `validate_inventory`.

**Rule**: Before patching env-level config into any function, check whether `load_inventory` or `validate_inventory` is the right call.

---

## Phase A.5 — Static gates cleanup (ruff + mypy)

**Lesson 1 — Non-ASCII in `# type: ignore` causes mypy `[syntax]` errors.** Em-dashes (`—`) and other Unicode in `# type: ignore` comments are not ASCII and mypy rejects them with `[syntax]`. Always use ASCII-only in type: ignore comments; put explanations in a separate `# explanation` line or use plain hyphens.

**Lesson 2 — `dict[str, object]` cascades to every call site.** Changing `list[dict]` → `list[dict[str, object]]` forces narrow types downstream: every `.get()` returns `object`, string interpolation requires `str()` casts, and every consumer needs updating. For heterogeneous mock/web data where types can't be predicted, `dict[str, Any]` is correct. Reserve `dict[str, object]` for narrowly typed mappings you control.

**Lesson 3 — `per-file-ignores` beats 100+ `# noqa` comments.** When an entire directory (e.g. `errander/web/`) has a structural reason to violate a rule (inline HTML/CSS templates), one `[tool.ruff.lint.per-file-ignores]` entry in `pyproject.toml` is the right solution — not 100+ per-line suppressions that inflate the noqa budget and obscure real suppressions.

**Lesson 4 — `# type: ignore` inside a closing paren is a Python syntax error.** `web.Application(middlewares=[...]  # type: ignore[list-item])` puts the comment inside the `)` — Python reports `'(' was never closed`. Always place `# type: ignore` comments after the closing delimiter on the same line.

**Lesson 5 — Always specify the exact error code in `# type: ignore[code]`.** Adding `[arg-type]` when mypy reports `[call-overload]` silently creates an unused-ignore that mypy later flags as an error itself. Run mypy to see the precise code, then add it verbatim. Wrong codes do not suppress the real error and add noise.

**Rule**: Before adding a `# type: ignore`, copy the error code from mypy output exactly. Never guess the code from memory.

---

## Phase B -- Proactive Signals

**Lesson 1 -- A standalone probe must mirror the vm_graph node ordering.** The initial Phase B probe skipped `discover_node` and called signal nodes directly. This caused two problems: (1) signal nodes used inventory fallback values (`os_family` from YAML) instead of what was actually on the VM at runtime; (2) SSH failures weren't caught before the first signal call -- the error appeared mid-probe rather than at a clean entry point.

**Rule**: Any component that reuses vm_graph signal nodes must call `discover_node` first, in the same order as `build_vm_graph()`. If discover fails, return unreachable immediately and skip all signal nodes. Check the vm_graph node chain whenever adding a new caller.

**Lesson 2 -- "Works independently" doesn't mean "skip the shared pre-check".** The probe is intentionally independent of maintenance batches (no locking, no approval, no waves). But independence from the *scheduler* is different from independence from the *node contract*. Signal nodes depend on `vm_info` being populated; discover provides it. Architectural independence doesn't excuse skipping required setup.

---

## Phase D -- Operator Assistant

**Lesson 1 -- Layer A violations are import-level, not runtime.** The Layer A invariant ("never executes") is enforced by what you import, not by runtime guards. If `operator_assistant.py` imports `SandboxExecutor`, a future developer could use it. The right check is `grep SandboxExecutor errander/agent/operator_assistant.py` returning 0 matches -- make it part of the definition of done for any Layer A module.

**Lesson 2 -- Deferred imports inside async functions need concrete class names for isinstance.** `from __future__ import annotations` makes all annotations lazy strings at runtime. If you put a class in `TYPE_CHECKING` and then try `isinstance(x, TheClass)`, you get `NameError` because `TheClass` is never imported at runtime. Fix: inside the function body, import the concrete class with a short alias (`from errander.safety.disk_history import VMDiskHistoryStore as _DiskStore`) and use that alias for isinstance checks.

**Rule**: TYPE_CHECKING imports are for type annotations only. isinstance checks always need a runtime import.

---

## Phase C -- Prometheus adapter

**Lesson 1 -- Camelcase class aliases must also be CamelCase (N814).** Ruff rule N814 fires when a CamelCase class is imported with a constant-style alias: `from errander.integrations.prometheus import PrometheusClient as _PC`. The underscore-prefixed all-caps alias `_PC` reads as a constant. Fix: keep the alias CamelCase (`as _PrometheusClient`). Same rule applies to any `from X import ClassName as _ALIAS` pattern.

**Lesson 2 -- `resp.json()` returns `object`, not `Any`, in typed aiohttp stubs.** mypy sees `await resp.json()` as returning `object`. Accessing `.get()` on `object` is an `[attr-defined]` error. Fix: `isinstance(raw, dict)` first, then `isinstance(data_block, dict)`, then `isinstance(rows, list)` at each level of nesting. This narrowing chain is required for every JSON response body that has nested structure -- don't assign to `dict[str, object]` directly, narrow step by step.

**Rule**: Every `await resp.json()` in a typed file needs explicit isinstance narrowing before any attribute access. The pattern from `_query_instant()` is the reference implementation.

---

## P0-1 -- Immutable Plan Artifact

**Lesson 1 -- Load tests with hardcoded SSH call counts break when new phases add calls.** `test_wave_abort_stops_fleet_at_boundary` had `call_count <= 75` covering "12 validate + 60 plan_vm + 3 wave-0 health". Adding `enrich_plan_node` (24 new SSH calls for disk_cleanup preview) shifted wave-0's health check past the threshold, causing it to fail instead of wave-1.

**Rule**: whenever a new node adds SSH calls to the planning flow, grep for hardcoded call-count thresholds in tests (`grep -n "call_count" tests/`). Update the comment AND the threshold together, and document the breakdown: `# 12 validate + 60 plan_vm + 24 enrich_plan + 3 wave-0 health = 99`.

**Lesson 2 -- Backwards-compatibility in approval message: empty preview should show params, not silence.** When `enrich_plan_node` is skipped (e.g., on first deploy before the node was added to existing deferred batches) or when SSH fails at plan time, the preview is `{}`. The new message format for patching with empty preview should fall back to showing `params`, not just "patching" with no detail. Existing test `test_params_appear_in_approval_slack_summary` caught this.

**Rule**: when reformatting a message that previously showed field X, always check if there's an existing test that asserts X appears. If the new format conditionally omits X, add a fallback so the assertion still passes.

## 2026-05-16 — EventType attributes must exist before they're referenced in except blocks

`EventType.PREFLIGHT_FAILED` (non-existent) raised `AttributeError` inside an `except Exception` block, which was silently swallowed — so blocked VMs still landed in `healthy_targets`. Pattern: always define EventType values before using them, and add a test asserting blocked VMs appear in `failed_targets`, not `healthy_targets`.

## 2026-05-16 — Local import inside function changes the patch path

`check_target` imported inside `validate_targets_node` must be patched at `errander.execution.target_validation.check_target`, not `errander.agent.graph.check_target`. Module-level imports create a reference in the importing module; local imports do not.

## 2026-05-16 — MagicMock is not awaitable; AsyncMock is required for async SSH calls

After adding `await ssh_manager.execute(...)` calls in `probe_vm`, all existing tests using `ssh_manager=MagicMock()` broke. Fix: `mgr.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))`. Never use bare `MagicMock` for objects whose methods are awaited.

## 2026-05-16 — _make_ssh_manager() helper prevents the above mistake at scale

Any test file that uses `SSHConnectionManager` should define a `_make_ssh_manager()` helper at the top that returns a properly wired `AsyncMock`. When new SSH calls are added to the SUT, only the helper needs updating, not every test.

## 2026-05-17 — `server.py` and `metrics.py` are two separate servers; routes in one don't exist in the other

`server.py` has `create_app()` with routes at `/`, `/glossary`, `/inventory`, etc. — this is a standalone demo. The actual production server is `metrics.py`'s `start_metrics_server()` with routes under `/ui/`. Navigating to `/glossary` on the metrics server returns 404 because the route is only registered in `server.py`.

**Rule**: when adding a UI page, add the route and handler to **both** `server.py` (standalone demo) and `metrics.py` (production). Check which server is actually running before debugging a 404.
