# Errander-AI — Lessons Learned

Self-improvement log. Updated after corrections, mistakes, and surprises.

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
