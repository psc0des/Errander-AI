# Errander-AI Command Log

Developer reference for every command used in building this project.

## Project Setup

### 2026-03-21 — Initial Scaffolding

```bash
mkdir -p errander/agent/subgraphs errander/safety errander/execution errander/integrations errander/observability errander/config errander/models errander/scheduling tests/agent/subgraphs tests/safety tests/execution tests/integrations tests/observability tests/config tests/models tests/scheduling tasks
```
**What**: Created the full directory tree for Option C architecture (parent orchestrator + fan-out + sub-graphs).
**Why**: Scaffolding all modules upfront so every file has a home from day one.

```bash
ls "C:/PS/AI/Junior DevOps Engineer - Agent/"
```
**What**: Listed project root contents before scaffolding.
**Why**: Verified starting state — only CLAUDE.md and docs/ existed.

```bash
find errander tests tasks -type f -name "*.py" -o -name "*.md" -o -name "*.toml" | sort
```
**What**: Listed all scaffolded files after creation.
**Why**: Final verification that all 86 files were created in the correct locations.

## Dependencies

### 2026-03-21 — Initial Setup

```bash
where uv 2>/dev/null || where.exe uv 2>/dev/null || echo "uv not found"
```
**What**: Checked if `uv` package manager was installed.
**Why**: `uv` is the project's package manager (specified in CLAUDE.md). It wasn't found.

```bash
pip install uv
```
**What**: Installed `uv` (v0.10.12) via pip.
**Why**: Needed as the project's package manager. Installed globally since it's a CLI tool.
**Result**: `Successfully installed uv-0.10.12`

```bash
python -m uv sync
```
**What**: Installed all project dependencies from pyproject.toml into .venv.
**Why**: First run — creates the virtualenv and installs all runtime dependencies.
**Note**: Multiple parallel runs were triggered (bj0shnnzk, bbmwd6rmi, byyuvn4cf) due to timeout issues with background tasks. All completed successfully.
**Result**: Resolved 68 packages, created `.venv/` with Python 3.12.10.

```bash
ls .venv/Scripts/python.exe
```
**What**: Checked if the virtualenv was successfully created.
**Why**: After timeout issues with `uv sync`, needed to verify the venv existed before proceeding.
**Result**: File found — venv was created successfully.

```bash
python -m uv sync --group dev
```
**What**: Attempted to install dev dependencies using `--group`.
**Why**: Needed pytest, ruff, mypy for development.
**Result**: **FAILED** — `error: Group 'dev' is not defined in the project's 'dependency-groups' table`
**Fix**: Used `--extra dev` instead (pyproject.toml uses `[project.optional-dependencies]`, not `[dependency-groups]`).

```bash
python -m uv sync --extra dev
```
**What**: Installed dev dependencies (pytest, ruff, mypy, etc.).
**Why**: Needed for running tests and linting.
**Result**: Successfully installed pytest 9.0.2, pytest-asyncio 1.3.0, ruff 0.15.7, mypy 1.19.1, and related packages.

## Git

*(No git commands run yet — scaffolding not committed.)*

## Testing

### 2026-03-21 — Import Verification + First Test Run

```bash
.venv/Scripts/python.exe -c "import errander; print('errander OK')"
.venv/Scripts/python.exe -c "from errander.models.vm import VMTarget, OSFamily; print('models OK')"
.venv/Scripts/python.exe -c "from errander.agent.state import BatchState, VMMaintenanceState; print('state OK')"
.venv/Scripts/python.exe -c "from errander.execution.commands import get_package_manager; print('commands OK')"
.venv/Scripts/python.exe -c "from errander.config.policies import get_policy; print('policies OK')"
```
**What**: Verified all key modules import without errors.
**Why**: Ensuring the scaffold is importable before running tests.
**Result**: All 5 imports passed.

```bash
.venv/Scripts/python.exe -m pytest
```
**What**: Attempted to run pytest.
**Why**: First test run to verify scaffolding.
**Result**: **FAILED** — `No module named pytest` — dev dependencies weren't installed yet.
**Fix**: Ran `uv sync --extra dev` (see Dependencies section).

```bash
.venv/Scripts/python.exe -m pytest -v
```
**What**: Ran full test suite with verbose output.
**Why**: Verify all 40 tests pass after installing dev deps.
**Result**: **40 passed in 1.74s** — all placeholder tests + real assertion tests pass.
**Platform**: Python 3.12.10, pytest 9.0.2, plugins: anyio, langsmith, asyncio, cov.

### 2026-03-21 — Phase 1.2: Settings Loader

```bash
mkdir -p "C:/PS/AI/Junior DevOps Engineer - Agent/config"
```
**What**: Created `config/` directory for YAML configuration files.
**Why**: Needed a home for inventory.yaml, policies.yaml, settings.yaml.

```bash
uv run pytest tests/config/ -v
```
**What**: Ran all config tests (schema, settings, inventory, policies).
**Why**: Verify settings loader implementation — 58 tests, all passing.

```bash
uv run pytest -v
```
**What**: Full test suite run.
**Why**: Ensure no regressions — 91 tests passing.

```bash
uv sync --extra dev
```
**What**: Re-synced dependencies after adding `aiosqlite>=0.20` to pyproject.toml.
**Why**: Audit logging requires async SQLite access.
**Result**: Installed `aiosqlite==0.22.1`.

```bash
uv run pytest tests/safety/test_audit.py -v
```
**What**: Ran audit store tests.
**Why**: Verify SQLite audit logging — 20 tests, all passing.

```bash
uv run pytest tests/execution/ -v
```
**What**: Ran SSH + OS detection + sandbox tests.
**Why**: Verify SSH connection manager, OS detection parsing, dry-run wrapper — 44 tests passing.

```bash
uv run pytest tests/safety/test_locking.py -v
```
**What**: Ran file locking tests.
**Why**: Verify FileLocker TTL, stale detection, ownership — 22 tests passing.

```bash
uv run pytest -v
```
**What**: Full test suite.
**Why**: Final regression check — 179 tests, all passing.

### 2026-03-23 — Phase 1.3: Disk Cleanup Sub-Graph

```bash
uv run pytest tests/agent/subgraphs/test_disk_cleanup.py -v
```
**What**: Ran disk cleanup sub-graph tests.
**Why**: Verify LangGraph sub-graph implementation — 31 tests, all passing.
**Issues**: 2 failures on first run:
  1. Lambda wrapping async functions caused `InvalidUpdateError: Expected dict, got <coroutine>` — fixed by using `async def` wrappers.
  2. Mock at wrong level — SandboxExecutor dry-run mode adds `[DRY-RUN]` prefix, need to mock at executor level not SSH level.

```bash
uv run pytest -v
```
**What**: Full test suite.
**Why**: Regression check — 209 tests, all passing.

### 2026-04-03 — Phase 1.4 + 1.5: Per-VM Graph + Batch Orchestrator

```bash
uv run pytest tests/agent/test_decisions.py -v
```
**What**: Decisions module tests (23 tests).
**Why**: Verify hardcoded action prioritization + template report generation.
**Result**: 23 passed.

```bash
uv run pytest tests/agent/test_vm_graph.py -v
```
**What**: Per-VM graph tests (28 tests).
**Why**: Verify lock/discover/plan/dispatch/audit/unlock lifecycle.
**Issues**: None on first run — patterns from disk_cleanup subgraph were followed correctly.
**Result**: 28 passed.

```bash
uv run pytest tests/agent/test_graph.py -v
```
**What**: Batch orchestrator tests (21 tests).
**Why**: Verify init/window/validate/fan-out/collect/report flow.
**Issues**: `fan_out` node returning `list[Send]` caused `InvalidUpdateError: Expected dict, got [Send(...)]`. Fixed by moving Send() emission to a conditional edge routing function (`make_fan_out_router`), not a node. LangGraph nodes must return dicts; Send() objects must come from conditional edge functions.
**Result**: 21 passed.

```bash
uv run pytest --tb=short
```
**What**: Full regression test suite.
**Why**: Ensure 278 tests (all phases) pass with no regressions.
**Result**: 278 passed in 7.56s.

## vLLM / LLM

### 2026-04-03 — Phase 1.6: LLM Client

```bash
uv run pytest tests/integrations/test_llm.py -v
```
**What**: LLM client tests (23 tests).
**Why**: Verify complete(), health_check(), retry logic, fallback behavior, decisions.py integration.
**Issues**: First run took 57s — each test was creating a new `AsyncOpenAI` client with httpx transport initialization (~1.4s each). Fixed by using module-scoped pytest fixtures for shared clients. Reduced to 6.5s.
**Result**: 23 passed.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 300 tests pass with LLM client added.
**Result**: 300 passed in 13.36s.

## Slack Integration

### 2026-04-03 — Phase 1.6: Slack Client + Approval Gate

```bash
uv run pytest tests/integrations/test_slack.py -v
```
**What**: Slack client tests (10 tests).
**Why**: Verify post_message, get_reactions, post_alert, rate limiting retry.
**Issues**:
  1. `session.post` is an async context manager (`async with session.post() as resp:`), not awaitable (`await session.post()`). Test mocks needed `_ctx()` wrapper returning an async CM. Fixed by switching implementation to `async with ctx as resp:` pattern.
  2. `get_reactions` called `_call(..., method="GET")` — but `_call`'s first param is the Slack API method name (e.g. `"reactions.get"`), not the HTTP method. Fixed by using `http_method="GET"`.
  3. Rate-limit retry used `break` instead of `continue`, which exited the loop instead of retrying. Fixed.
**Result**: 10 passed in 0.12s.

```bash
uv run pytest tests/safety/test_approval.py -v
```
**What**: Approval gate tests (21 tests).
**Why**: Verify request_approval message formatting and poll_approval reaction logic (approve, reject, priority, timeout, error recovery, delayed approval).
**Result**: 21 passed in 0.09s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 321 tests pass with Slack + approval added.
**Result**: 321 passed in 13.8s.

### 2026-04-03 — Phase 1.6: Prometheus Metrics

```bash
uv run pytest tests/observability/test_metrics.py -v
```
**What**: Metrics tests (20 tests).
**Why**: Verify metric registration, counter/histogram tracking, /metrics and /health handlers, server startup.
**Issues**: `web.Response(body=..., content_type=..., headers={"Content-Type": ...})` raises ValueError — aiohttp forbids passing both `content_type` param and `Content-Type` header. Fixed by removing the `content_type` kwarg and relying on the header alone.
**Result**: 20 passed in 1.02s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 338 tests pass with metrics added.
**Result**: 338 passed in 15.48s.

### 2026-04-03 — Pre-Phase 1.8: Window Wiring + main.py

```bash
uv run pytest tests/agent/test_graph.py::TestValidateWindowNode -v
```
**What**: Window node tests (6 tests including new window enforcement tests).
**Why**: Verify validate_window_node correctly blocks outside-window batches, passes with force=True, and is properly wired in build_batch_graph.
**Issues**: `SandboxExecutor(dry_run=True)` missing required `ssh_manager` arg — used `_make_executor()` helper instead.
**Result**: 6 passed.

```bash
uv run pytest tests/test_main.py -v
```
**What**: main.py tests (17 tests).
**Why**: Verify CLI arg parsing, _build_maintenance_window, and async_main error paths.
**Issues**:
  1. `FileLocker` doesn't accept `ttl_seconds` kwarg — fixed to `FileLocker(lock_dir=...)` only.
  2. `_make_env(days=[])` — test helper used `days or [...]` which treated empty list as falsy. Fixed to `days if days is not None else [...]`.
**Result**: 17 passed.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 394 tests pass after wiring + main.py.
**Result**: 394 passed in 6.57s.

### 2026-04-03 — Phase 1.7: Scheduling + Windows

```bash
uv run pytest tests/scheduling/ -v
```
**What**: Scheduling tests (windows + scheduler, 36 tests).
**Why**: Verify maintenance window enforcement (normal, overnight, timezone) and APScheduler wrapper lifecycle.
**Issues**:
  1. `test_timezone_conversion_outside` — Europe/Paris in April is CEST (UTC+2), not CET (UTC+1). Test comment was wrong; fixed the UTC time to 01:00 (→ 03:00 CEST, outside [04:00, 06:00)).
  2. APScheduler 3.x `job.next_run_time` is a `__slots__` attribute — not initialized on pending jobs (scheduler not started). Fixed with `getattr(job, "next_run_time", None)`.
  3. `replace_existing=True` only deduplicates against jobstores, not the pending list. Removed that test (APScheduler internal, not our logic).
**Result**: 36 passed in 0.16s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 373 tests pass with scheduling added.
**Result**: 373 passed in 5.98s.

### 2026-04-03 — vLLM Deployment + LLM Health Check

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Verify 415 tests still pass after adding check_endpoint() to LLMClient and --check-llm to main.py.
**Result**: 415 passed in 6.74s.

### 2026-04-03 — Web UI

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Verify 415 tests still pass after adding UI routes to metrics server.
**Result**: 415 passed in 7.85s, no warnings (AppKey fix applied).

### 2026-04-03 — SQLite Audit Integration

```bash
uv run pytest tests/safety/test_audit.py tests/safety/test_audit_integration.py -v
```
**What**: Ran audit store tests (existing 20) + new integration tests (21).
**Why**: Verify action_type filter, get_recent_batches, VM graph audit trail, and audit CLI mode.
**Result**: 41 passed in 2.03s.

```bash
uv run pytest --tb=short
```
**What**: Full regression suite.
**Why**: Ensure 415 tests pass with audit integration added.
**Result**: 415 passed in 5.80s.

### 2026-04-10 — Dual-Channel Approval + Approval UI

```bash
uv run pytest tests/safety/test_approval.py tests/ui/test_approval_ui.py -x -q
```
**Why**: Run new approval tests after implementing ApprovalManager, await_dual_approval, and UI routes.
**Result**: First run — 1 failing (test_ui_approval_wins_race). Global asyncio.sleep patch caused lambda recursion.

```bash
uv run pytest tests/safety/test_approval.py tests/ui/test_approval_ui.py -x -q
```
**Why**: Rerun after fixing tests to use asyncio.Event().wait() blocking instead of patched sleep.
**Result**: 50 passed in 1.02s.

```bash
uv run pytest --ignore=tests/ui/test_web_ui.py -q
```
**Why**: Full suite after changes to metrics.py, approval.py, and main.py.
**Result**: 454 passed in 7.23s.

```bash
uv run pytest tests/ui/test_web_ui.py -q
```
**Why**: Verify Playwright tests still pass after nav changes in _page().
**Result**: 25 passed in 53.57s.

## Phase 3 — Hardening (Rolling Updates, Canary, Drift Detection)

### 2026-04-18 — Rolling updates, canary logic, drift detection

```bash
uv run pytest tests/config/test_settings.py tests/safety/test_drift.py -x -q
```
**What**: Run settings + drift module tests after Step 1 (schema/settings) and Step 2 (drift.py).
**Why**: Verify foundation before building on it.
**Result**: 35 passed in 0.32s.

```bash
uv run pytest tests/agent/test_vm_graph.py -x -q
```
**What**: Run VM graph tests after Step 3 (drift_check_node integration).
**Why**: Catch routing regression — route_after_discover now returns "drift_check" not "plan_actions".
**Result**: 1 failure (existing test expected "plan_actions"). Fixed test. 39 passed.

```bash
uv run pytest tests/agent/test_graph.py -x -q
```
**What**: Run batch graph tests after Step 4 (rolling updates / new topology).
**Why**: Verify new wave-based graph topology didn't break existing tests.
**Result**: 25 passed in 1.41s.

```bash
uv run pytest tests/agent/test_rolling_updates.py tests/agent/test_canary.py -x -q
```
**What**: Run new rolling update and canary tests.
**Why**: Step 4 + Step 5 verification.
**Result**: 31 passed in 1.09s.

```bash
uv run pytest -x -q
```
**What**: Full test suite after all 6 implementation steps.
**Why**: No regressions — all 652 tests must pass.
**Result**: 652 passed in 56.54s.

```bash
uv run ruff check errander/safety/drift.py errander/agent/vm_graph.py errander/agent/graph.py errander/config/schema.py errander/config/settings.py errander/models/events.py errander/observability/metrics.py errander/main.py
```
**What**: Lint the modified files.
**Why**: Confirm no new lint errors introduced.
**Result**: All errors are pre-existing (TC001 type-checking imports, UP017 datetime.UTC alias, etc.) — none introduced by Phase 3 changes.

## Phase 3 — Edge Case Hardening (2026-04-19)

```bash
uv run pytest -q
```
**What**: Full test suite after all Phase 3 hardening implementation steps.
**Why**: Verify 677 tests pass (25 new tests added) with no regressions.
**Result**: 677 passed in ~61s.

```bash
uv run ruff check errander/safety/audit.py errander/agent/vm_graph.py errander/agent/graph.py errander/execution/ssh.py errander/config/schema.py errander/safety/locking.py
```
**What**: Lint check on all files modified during Phase 3 edge case hardening.
**Why**: Confirm no new lint violations introduced (only pre-existing UP017/TC001 violations remain).
**Result**: No new errors from Phase 3 changes. Fixed UP041 (asyncio.TimeoutError alias), F401 (unused timezone), E402 (logger placement) issues found during lint.

### 2026-04-19 — Load tests + Playwright approvals tests

```bash
uv run pytest tests/agent/test_load.py tests/ui/test_approvals_playwright.py -v
```
**What**: Run the two new test files in isolation to catch failures early.
**Why**: Verify 20 load tests and 22 Playwright approvals tests all pass before running full suite.
**Result**: 2 failures — `ActionStatus.COMPLETED` (wrong enum value, fixed to `ActionStatus.SUCCESS`) and `test_report_excerpt_shown` (report inside collapsed `<details>`, fixed by clicking to expand first).

```bash
uv run ruff check --fix tests/agent/test_load.py tests/ui/test_approvals_playwright.py
```
**What**: Auto-fix lint in new test files.
**Why**: Caught I001 (unsorted imports), UP017 (timezone.utc → UTC), F541 (f-string without placeholders).
**Result**: 10 auto-fixed; remaining 8 (TC003, E501, SIM117) fixed manually.

```bash
uv run pytest -q
```
**What**: Full test suite after load test + Playwright approvals additions.
**Why**: Verify 719 tests pass with no regressions.
**Result**: 719 passed in ~89s.

## Phase 4 — LLM Flexibility + Secrets + UI Config (2026-04-19)

```bash
uv run pytest tests/integrations/test_llm.py tests/integrations/test_secrets.py tests/observability/test_redaction.py tests/config/test_secrets_loading.py -v
```
**What**: Run Phase A + A.5 new tests in isolation.
**Why**: Verify rewritten LLM tests, 24 secrets tests, 9 redaction tests, and 6 secrets-loading tests all pass before touching the full suite.
**Result**: All passed.

```bash
uv run ruff check --fix errander/integrations/llm.py errander/integrations/secrets.py errander/observability/redaction.py errander/config/schema.py errander/config/settings.py errander/agent/decisions.py errander/main.py
```
**What**: Auto-fix lint on all Phase A/A.5 modified files.
**Why**: Caught I001 (import ordering — two `from openai import ...` lines merged), F401 (unused imports).
**Result**: Fixed automatically. Pre-existing UP047/B905/SIM105 errors left untouched.

```bash
uv run pytest -q
```
**What**: Full test suite after Phase A + A.5 implementation.
**Why**: Verify no regressions from LLM client and secrets changes.
**Result**: All tests passing.

```bash
uv run pytest tests/safety/test_overrides.py tests/config/test_settings_precedence.py tests/agent/test_inventory_merge.py -v
```
**What**: Run Phase B new tests in isolation.
**Why**: Verify 18 overrides tests, 21 settings-precedence tests, and 9 inventory-merge tests before running full suite.
**Result**: All 49 passed after fixing patch target (`errander.agent.graph.build_batch_graph` not `errander.main.build_batch_graph` — local import inside function body).

```bash
uv run ruff check --fix tests/safety/test_overrides.py tests/config/test_settings_precedence.py tests/agent/test_inventory_merge.py
```
**What**: Auto-fix lint on Phase B test files.
**Why**: Caught F401 (unused `os`, `patch`, `pytest`), I001 (unsorted imports), B017 (blind Exception).
**Result**: 8 auto-fixed; `TC003` suppressed with `# noqa`; `B017` fixed by catching `aiosqlite.IntegrityError`.

```bash
uv run pytest --tb=short -q
```
**What**: Full test suite after Phase B implementation.
**Why**: Verify 799 tests pass with no regressions.
**Result**: 799 passed in ~103s.

```bash
uv run ruff check errander/ tests/
```
**What**: Full project lint check after Phase 4.
**Why**: Confirm no new violations — only pre-existing TC001/UP017/etc. remain.
**Result**: Only pre-existing errors; all Phase 4 files clean.

## Phase 4 — Playwright Tests T4-T6 (2026-04-20)

```bash
uv run pytest tests/ui/test_settings_playwright.py tests/ui/test_inventory_playwright.py tests/ui/test_ui_auth_playwright.py -v --tb=short
```
**What**: Run the three new Phase 4 Playwright test files in isolation.
**Why**: Debug 4 remaining failures from previous session before running full suite.
**Result**: Initially 41/45 passing; root cause found — nested `<form>` in settings page.

```bash
uv run ruff check tests/ui/ --fix
```
**What**: Auto-fix lint on all UI Playwright test files.
**Why**: Caught I001 (import ordering), F841 (unused variables), E501 (long lines in test_web_ui.py).
**Result**: 9 auto-fixed; remaining manually corrected.

```bash
uv run pytest tests/ui/ -v --tb=short
```
**What**: Run all 111 UI Playwright tests after nested-form fix.
**Why**: Verify all settings, inventory, auth, and existing UI tests pass.
**Result**: 111 passed.

```bash
uv run pytest --tb=short -q
```
**What**: Full test suite after all Phase 4 Playwright fixes.
**Why**: Confirm 844 tests pass with no regressions.
**Result**: 844 passed in ~146s.

## Entry Point Fix (2026-04-20)

```bash
uv run python -m errander --help
```
**What**: Test `python -m errander` invocation.
**Why**: User tried to run the agent and hit `No module named errander.__main__`.
**Fix**: Created `errander/__main__.py` that calls `errander.main.main()`.

## Deferred Execution (2026-04-27)

```bash
uv run pytest tests/safety/test_deferred.py tests/scheduling/test_windows.py tests/agent/test_graph.py::TestApprovalGateDeferred tests/test_main.py::TestWindowOpener -v
```
**What**: Run only the new deferred execution tests (59 tests across 4 files).
**Why**: Verify all new tests pass before running the full suite.
**Result**: 59 passed.

```bash
uv run pytest
```
**What**: Full test suite after deferred execution feature.
**Why**: Confirm 878 tests pass with no regressions.
**Result**: 878 passed in ~304s.

## SSH / Target VMs

### 2026-05-09 — Phase 1.8 E2E Validation (Azure VMs)

```bash
# On Master VM — install uv (pip-based, before bootstrap script existed)
pip3 install uv
```
**What**: Installed uv via pip3 on Ubuntu 22.04 Master VM.
**Why**: python3.12 not in default Ubuntu 22.04 apt repos; uv can manage its own Python.

```bash
# On Master VM — install Python 3.12 via uv
uv python install 3.12
```
**What**: Downloaded and installed Python 3.12.13 into uv's managed Python store.
**Why**: Agent requires Python 3.12+; uv installer avoids deadsnakes PPA.

```bash
# Add uv to PATH for current session and permanently
export PATH="/root/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```
**What**: Fixed missing PATH entry after `uv python install` warning.
**Why**: uv installs to ~/.local/bin which is not in PATH by default on Ubuntu 22.04.

## Deployment

*(No deployment commands run yet.)*

## Setup Scripts

### 2026-05-10 — doc audit and fixes

```bash
git rev-parse --short HEAD   # checked current HEAD hash for validation checklist update
```
**What**: Verified current HEAD commit hash.
**Why**: tasks/phase-1.8-validation-checklist.md had a stale commit hash (8a7c65e) — updated to current HEAD.

### 2026-05-10 — scripts/configure.sh (interactive setup)

```bash
# End users run this after bootstrap.sh + LLM setup
bash scripts/configure.sh
```
**What**: Interactive script that prompts for LLM provider/credentials, target VMs, SSH key path, optional Slack, then writes `.env` + `inventory.yaml` and verifies the LLM connection.
**Why**: Steps 4–6 of SETUP.md required users to manually construct .env and inventory.yaml — the script eliminates that and makes the flow sequential and prompting.

```bash
# Verify LLM inline (no .env needed) — used inside configure.sh
ERRANDER_LLM_BASE_URL=https://<resource>.openai.azure.com/openai/v1/ \
ERRANDER_LLM_MODEL=<deployment> \
ERRANDER_LLM_API_KEY=<key> \
uv run python -m errander --check-llm
```
**What**: Tests LLM connection using inline env vars rather than loading from .env.
**Why**: .env doesn't exist yet during Step 4 — inline vars verify credentials before Step 5 creates the file.

### 2026-05-10 — scripts/bootstrap.ps1 (Windows bootstrap)

```powershell
# Clone first, then run bootstrap
git clone https://github.com/psc0des/Errander-AI.git errander
powershell -ExecutionPolicy Bypass -File errander\scripts\bootstrap.ps1
```
**What**: Windows equivalent of bootstrap.sh — installs git (winget), uv (official PS installer), Python 3.12, runs uv sync, verifies import. No admin required.
**Why**: Windows Step 1 was manual; Linux had a one-liner. Now both platforms have identical one-liner experience.

## Bootstrap Script

### 2026-05-10 — private repo fix

```bash
# Correct invocation for private repo (curl one-liner returns 404)
git clone https://github.com/psc0des/Errander-AI.git errander
bash errander/scripts/bootstrap.sh
```
**What**: Replaced `curl | bash` one-liner with clone-first approach.
**Why**: `raw.githubusercontent.com` returns 404 for private repos without a token.

### 2026-05-09 — scripts/bootstrap.sh

```bash
# End users run this one-liner to bootstrap the Master VM
curl -LsSf https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/bootstrap.sh | bash
```
**What**: Single command that detects distro, installs git/curl/uv/Python 3.12, clones repo, runs uv sync, verifies import.
**Why**: Manual step-by-step approach in SETUP.md had implicit steps that tripped up real users (PATH export, Python 3.12 not in apt on Ubuntu 22.04).

## Debugging

### 2026-03-21 — uv sync timeout issues

**Problem**: `uv sync` commands kept running in the background and timing out in the CLI tool output reader.
**What happened**: Three separate `uv sync` invocations were launched (direct `uv`, `uv sync`, `python -m uv sync`) because the first two appeared to hang.
**Root cause**: `uv` was downloading and installing 68 packages which took longer than the default tool timeout.
**Resolution**: All three completed successfully. The `.venv/` was created and all packages installed. Used `ls .venv/Scripts/python.exe` to verify the venv existed before proceeding.
**Lesson**: Use longer timeouts for package installation commands, or check for the venv's existence rather than waiting for the install command output.
