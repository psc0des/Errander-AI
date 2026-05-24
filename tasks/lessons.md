# Errander-AI — Lessons Learned

## 2026-05-24 — Use `@computed_field` for model properties that must always be derived

When adding a field that is always derived from other fields (e.g., `confidence` from `sample_size`), use Pydantic v2's `@computed_field` + `@property` instead of a regular field with a default. This avoids callers ever passing an inconsistent value, keeps the single source of truth in the model, and doesn't break existing instantiations.

**Why:** A regular required field breaks all existing instantiation sites. A field with a default (e.g., `confidence: str = "low"`) silently passes the wrong value at those sites. `@computed_field` is the correct Pydantic v2 primitive for derived read-only model properties.

**How to apply:** `from pydantic import computed_field` → decorate with `@computed_field  # type: ignore[prop-decorator]` then `@property`. The suppression is needed because mypy strict mode flags the double-decorator as `prop-decorator`.

## 2026-05-24 — Migration tests need updating when a new migration is added

When adding a new database migration, the existing migration tests check exact version counts and table lists. They will fail with `AssertionError: assert N+1 == N`. Always update `tests/safety/test_migrations.py` when adding a migration: (1) update `versions == [0, ..., N]` to include the new version, (2) update `count == N` to `N+1`, (3) add new table names to the expected tables set.

**Why:** The tests are designed to be brittle — they lock in the exact migration count and schema so regressions are caught immediately. The cost is that adding a migration requires a matching test update.

**How to apply:** After adding any migration entry to `_MIGRATIONS`, immediately grep `test_migrations.py` for the old count and update it.

## 2026-05-24 — ruff TC001: local imports inside methods should be moved to TYPE_CHECKING when only used as annotations

When an import is used only as a type annotation (e.g., `capped_vms: list[VMSignalSummary] = []`), ruff's `TC001` rule flags it as "should be in TYPE_CHECKING block" to avoid the runtime import cost. With `from __future__ import annotations`, variable annotations are strings and the class doesn't need to be importable at runtime. The fix: add to `if TYPE_CHECKING:` at the module level and remove the local import.

**Why:** `from __future__ import annotations` makes ALL annotations (including variable annotations) lazy strings. No runtime type object needed. A local runtime import for a pure annotation is dead weight that ruff correctly flags.

**How to apply:** If you see TC001 on a local import, check whether the imported name is used only in annotations. If yes, move to TYPE_CHECKING. Only keep the local import if the name is used at runtime (isinstance checks, class instantiation, dataclasses.replace with the type, etc.).



## 2026-05-23 — `_INJECTION_RE` catches shell metacharacters; unknown commands are caught by the action-type filter

When writing adversarial tests for the prompt injection layer, the regex `_INJECTION_RE` only catches strings containing shell metacharacters (`;&|`, backtick, `$()`, `{}`, `\n`, `../`). Strings like `kubectl delete pod --all` or `docker exec -it bash` don't contain these characters — they are rejected instead by `_parse_action_types()` as unknown action types. Separate the two test cases accordingly: one for `_INJECTION_RE.search()`, one for `_parse_action_types()`.

**Why:** The two-layer defence is complementary, not redundant. The regex handles smuggled shell payloads; the allowlist handles out-of-scope commands. Both must be tested — but testing the wrong one gives a false pass.

**How to apply:** For any new action-type adversarial test, ask "does this payload contain a shell metacharacter?" If yes → `_INJECTION_RE`. If no (arbitrary free-text command) → `_parse_action_types()`.

## 2026-05-23 — `aiosqlite.execute_fetchall()` returns `Iterable[Row]`, not `list[Row]`

Indexing the return value of `execute_fetchall()` with `rows[0]` raises a mypy error: `Value of type "Iterable[Row]" is not indexable`. The fix is to wrap with `list()` before indexing: `rows = list(await db.execute_fetchall(...))`.

**Why:** `aiosqlite` types `execute_fetchall` as returning `Iterable` (the underlying `sqlite3.fetchall()` returns a list at runtime, but the stub is conservative). Mypy strict mode catches this.

**How to apply:** Any new query method that needs to index a result row: wrap with `list()`. Iteration (list comprehension) works fine without wrapping.

## 2026-05-23 — `APIConnectionError` constructor takes `message` keyword arg, not `body`

`openai.APIConnectionError(message="...", request=MagicMock())` is the correct constructor. Passing `body=None` raises `TypeError: __init__() got an unexpected keyword argument 'body'`.

**Why:** The openai SDK's `APIConnectionError.__init__` signature does not accept `body`; that kwarg belongs to `APIStatusError`.

**How to apply:** When mocking openai exceptions: `APITimeoutError(request=mock)`, `APIConnectionError(message="...", request=mock)`, `APIStatusError(message="...", response=mock, body=None)`.



## 2026-05-23 — Local imports inside async functions cause ruff I001 (import sort) errors

A `from datetime import UTC, datetime` import placed inside a function body will be flagged by ruff as `I001 [*] Import block is un-sorted or un-formatted`. ruff treats intra-function imports independently and applies isort rules to them.

**Why:** ruff/isort applies to every import block it finds, including those inside function bodies. The sort ordering inside a function body can differ from what ruff expects (stdlib first, then third-party).

**How to apply:** Move datetime (and any other stdlib) imports to module level. Only use local imports inside functions when there is an explicit circular-import reason — and even then, order them correctly within the local block.

## 2026-05-23 — `_window_opener` must forward every manager arg that `run_env_batch` accepts

When a new manager is added to `run_env_batch` (e.g. `hygiene_manager`, `disk_history_store`), every call site must be updated — including `_window_opener`. It has its own signature that does not inherit from `async_main`, so omitting a parameter silently passes `None` (the default), causing the deferred code path to behave differently from the scheduled and `--run-now` paths.

**Why:** `_window_opener` is a standalone async function, not a closure over `async_main`. New parameters added to `run_env_batch` are invisible to it unless explicitly threaded through.

**How to apply:** After adding any parameter to `run_env_batch`, grep for all `run_env_batch(` call sites and confirm each one passes the new parameter. The three sites are: `async_main --run-now`, `_run` closure in scheduler, and both branches in `_window_opener`.

## 2026-05-23 — Docker wrapper emits short 12-char IDs by default; remove wrapper uses full 64-char SHA256

`docker images --format '{{.ID}}'` emits 12-character short IDs. `docker images -q --no-trunc` and `docker image inspect` accept both formats. But the `errander-docker-remove-v2` wrapper's revalidation loop uses `grep -Fx "$obj_id"` against the assess output, requiring an exact string match — short vs full always fails.

**Why:** `grep -Fx` is "fixed string, exact line match." A full 64-char SHA256 will never match a 12-char short ID, so every dangling image would always return `drift_skipped reason=image_re_tagged`.

**How to apply:** Always add `--no-trunc` to `docker images` calls in assess wrappers. Tests for ID format should assert the full `sha256:[a-f0-9]{64}` pattern, not just that the string starts with `sha256:`.



## 2026-05-23 — snapshot_node returning empty strings on validation error is not a FAILED status

When `snapshot_node` catches a `CommandBuildError` (invalid unit name), it logs the error and returns `{"pre_status": "", "pre_journal": ""}` — no `status` key. The `execute_node` DOES return `{"status": "failed"}`. Tests for `snapshot_node` safety must assert that `.execute` was NOT called (no SSH), not that `status == FAILED` in the returned dict.

**Why:** The snapshot node is not a terminal node — it's a pre-execution read step. LangGraph merges its return dict into state without requiring a `status` field. The execute_node is the one that terminates the graph on failure.

**How to apply:** When writing adversarial tests for a node that guards via early return (not via explicit FAILED status), assert the side effect was prevented (no SSH call, no mutation) rather than asserting on the status field.

## 2026-05-23 — Base64 last-character tamper tests are unreliable; use full signature replacement

Flipping the last character of a base64url-encoded HMAC signature is not guaranteed to change the decoded bytes in a detectably different way (the last char may encode only padding bits). Instead, replace the entire signature with a known-wrong value (e.g., all-zeros HMAC).

**Why:** base64url encoding of 32 bytes (HMAC-SHA256) produces 43 chars. The last char encodes 4 real bits + 2 zero padding bits. Flipping it might or might not produce a different decoded value depending on the specific character.

**How to apply:** For tamper-resistance tests, construct the fake signature explicitly: `base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode()`.

## 2026-05-23 — Add migration test updates when adding a new DB migration

The migration test `test_records_applied_versions` asserts an exact version list, and `test_idempotent_on_second_run` asserts the exact count. Adding migration 8 requires updating both:
- `versions == [0, 1, ..., 7]` → `[0, 1, ..., 8]`
- `count == 8` → `count == 9`
- `expected` table set must include the new table

**How to apply:** Search `tests/safety/test_migrations.py` after adding any migration and update all three assertions.

## 2026-05-22 — When replacing an action type in source, grep tests too

Replacing `ActionType.DOCKER_PRUNE` with `DOCKER_HYGIENE` in `decisions.py` required matching fixes in 6 test files. `uv run pytest -x` surfaced them one by one; the final count was 6 stale references across `test_decisions.py`, `test_enabled_actions_planning.py`, `test_vm_graph.py`, `test_golden_plans.py`, and `test_metrics.py`.

**Why:** Tests that reference action type enum values by name must be updated alongside the production code they exercise. `DOCKER_PRUNE` in `_is_action_applicable` controlled the "docker unavailable → skip" logic — any test exercising that code path had a stale assertion.

**How to apply:** After any enum-value rename or replacement in source, run `grep -rn "OLD_NAME" tests/` before the full test run to find all impacted test files at once rather than fixing them one failure at a time.

## 2026-05-22 — Approval-surface scope must be per-finding, not per-class

When an executable class has findings at different classifications (e.g. `image_unused` with age > 30 is `cleanup_candidate` but age ≤ 30 is `report_only`), gating the approval surface at the class level is insufficient. The formatter showed `✓` for ALL `image_unused` items; the parser allowed approving any by index; `approve all` would have selected report_only findings.

Fix: push the scope check to the individual finding level in three places:
1. **Formatter** — per-finding classification marker (`✓` only when classification==CLEANUP_CANDIDATE AND class in _EXECUTABLE_CLASSES).
2. **`_select_all`** — default to CLEANUP_CANDIDATE; raise on INVESTIGATE/REPORT_ONLY filter.
3. **`_parse_explicit_indices`** — reject each finding where `classification != CLEANUP_CANDIDATE` before adding to the selected set.

**Why:** Without this, an operator could accidentally (or intentionally) approve a report_only finding by index. The formatter showing `✓` would mislead them into thinking it was safe to remove.

**How to apply:** Every time a new resource class or sub-classification is added to `_EXECUTABLE_CLASSES`, ask: "Can ALL findings in this class be removed, or only a subset?" If only a subset, add a per-finding guard at all three sites above.

## 2026-05-22 — LEGACY_ACTION_TYPES pattern: keep an enum value for read-back, skip it in active-action assertions

When removing an action type from active use, do NOT delete its `ActionType` enum value if it appears in historical audit rows — that breaks deserialization of old records. Instead:
1. Keep the enum value.
2. Add it to a `LEGACY_ACTION_TYPES: frozenset[ActionType]` set.
3. Update tests that iterate `ActionType` to skip `LEGACY_ACTION_TYPES` (e.g., `test_all_active_action_types_have_risk_tiers`).
4. Do NOT add the legacy type to `ACTION_RISK_TIERS` — it must not be planned.

**Why:** Audit databases are append-only. Old rows reference the value by string. If the enum value is gone, `ActionType("docker_prune")` raises `ValueError` when reading history.

**How to apply:** Every time an action is retired, add it to `LEGACY_ACTION_TYPES` rather than deleting the enum value.

## 2026-05-22 — Hard delete beats soft deprecation for safety-critical invariant violations

When a code path violates a core invariant (e.g., bulk-approval for a destructive action), deleting it is safer than deprecating it. Soft deprecation leaves the violation path accessible and operators may inadvertently use it. A loud `ConfigError` on load + migration helper is the right pattern: fail fast, tell the operator exactly how to fix it.

**Why:** Soft deprecation requires every future contributor to know "don't use X" — it's documentation. Hard delete + loud-fail is enforcement.

**How to apply:** When removing a legacy config key, add a `model_validator` that raises `ConfigError` with the migration command in the message. Never silently ignore old keys.

## 2026-05-22 — Local imports inside a function body change the patch target

When `poll_hygiene_replies_once` is imported *inside* `_run_docker_hygiene` (not at module level), patching `errander.agent.vm_graph.poll_hygiene_replies_once` fails — the name never exists in that module's namespace. The correct patch target is the source module: `errander.safety.hygiene_approval.poll_hygiene_replies_once`. When the local import runs at call time, Python resolves it from `errander.safety.hygiene_approval`, so patching the attribute there before the call works.

**Why:** Module-level imports create a reference in the importing module's namespace at load time, so patching the importer works. Local imports happen at call time and always resolve from the *source* module. They leave no attribute in the importing module's namespace.

**How to apply:** Before writing `patch("errander.X.function_name")`, verify the import is at module level in `errander/X.py`. If the import is inside a function body, patch the source module instead: `patch("errander.source_module.function_name")`.

## 2026-05-22 — Classify-time gate is the right pattern for default-off resource classes

When a resource class (e.g., volumes) should be default-off with an operator opt-in flag, the gate belongs in the classifier (`_classify_volume(enabled=False)` → always `REPORT_ONLY`), not in `_EXECUTABLE_CLASSES`. This is Option A (classify-time gate):

- `_EXECUTABLE_CLASSES` stays static — no dynamic filtering needed.
- The approval surface never sees a cleanup_candidate unless the flag is on.
- The formatter, `_select_all`, and `_parse_explicit_indices` all work correctly without modification.
- Adding the class to `_EXECUTABLE_CLASSES` is safe as long as the classifier returns `REPORT_ONLY` by default.

**Why:** Option B (dynamic `_EXECUTABLE_CLASSES`) would require threading the config flag into three approval-surface sites and making the set depend on runtime state. Option A keeps the approval surface stateless and the gate a single decision point.

**How to apply:** For any new resource class with a default-off config flag, implement the gate in the classifier function with `enabled: bool = False`. Then add the class to `_EXECUTABLE_CLASSES` so the approval surface can handle it when enabled.

## 2026-05-22 — build_cache identity must be a stable string, not the angle-bracket fallback

`DockerHygieneFinding.identity` falls back to `f"<{resource_class.value}>"` (with angle brackets) when neither `name` nor `object_id` is set. The `errander-docker-remove-v2` wrapper outputs `id=build_cache` (no angle brackets). This caused the parser to fail to match the key → it treated the build_cache result as unapproved → dropped it (per Contract B: drop results for unapproved objects). Fix: set `name="build_cache"` explicitly in `_build_finding()` for the BUILD_CACHE branch, giving `identity == "build_cache"`.

**Why:** The `identity` property is the join key between the assessment snapshot and the wrapper's removal output. Any mismatch silently drops the result or causes a lookup failure.

**How to apply:** For every resource class, verify that `DockerHygieneFinding.identity` returns exactly the string the wrapper will output as `id=...`. Trace from `_build_finding()` (where the finding is constructed) → `identity` property → wrapper output. Add a test that calls `parse_remove_v2_output` with a matching wrapper line and asserts the status is not `FAILED`.

## 2026-05-22 — Mock functions that stand in for an interface must accept **kwargs when the interface gains new parameters

When `dispatch_action_node` gained new keyword parameters (`hygiene_manager`, `slack_client`, etc.), test functions that mocked `_run_docker_hygiene` with plain positional signatures broke with `TypeError: fake_runner() got an unexpected keyword argument 'hygiene_manager'`. The fix is to add `**_: object` to the mock function signature so it silently absorbs any extra keyword arguments.

**Why:** The production caller passes kwargs by name. Tests that fake out the callee get the same kwargs; a positional-only signature rejects them.

**How to apply:** Any test mock function that stands in for a real function should use `**_: object` in its signature if the real function accepts (or may in future accept) keyword arguments. This future-proofs the mock against interface evolution.

## 2026-05-22 — pytest-asyncio + pytest-playwright collide; isolate async tests with manual event-loop drivers when full suite breaks

Web route tests in `tests/web/test_hygiene_approve.py` (later moved to `tests/safety/test_hygiene_web_approve.py`) passed in isolation but failed in the full suite with `RuntimeError: Runner.run() cannot be called from a running event loop` during pytest-asyncio teardown. Bisection showed `tests/ui/` (which uses pytest-playwright) corrupts the asyncio runner state. Since `tests/web/` sorts AFTER `tests/ui/` alphabetically, the corruption propagated. Two fixes were applied:

1. **Bypass pytest-asyncio for these tests.** Switched the test methods from `async def + @pytest.mark.asyncio` to sync methods that drive a fresh `asyncio.new_event_loop().run_until_complete(coro)` per test. This isolates each test from any prior asyncio state.
2. **Move the test file to `tests/safety/`.** This sorts the new tests *before* `tests/ui/` runs, avoiding the corruption window entirely. The relocation is also semantically correct — they're testing the approval surface, which is a `safety/` concern.

**Why:** Both fixes are belt-and-suspenders. The manual event-loop driver makes the tests robust to ANY upstream pytest-asyncio state damage. The directory move addresses the specific cause that caught me here. Either alone would work; both together close the door.

**How to apply:** When adding new tests that involve aiohttp handlers or async code, and the full suite includes pytest-playwright (`tests/ui/`), either (a) place the new tests in a directory that sorts before `tests/ui/`, or (b) drive event loops manually with `asyncio.new_event_loop().run_until_complete()` instead of pytest-asyncio. The auto-mode pytest-asyncio in this project is convenient but fragile across plugin boundaries.

## 2026-05-22 — When introducing a new env var, the grep must include the canonical secrets lists, not just test counts

I shipped Session 2b-i with `ERRANDER_SIGNING_SECRET` as a new env var, did the doc-sync grep for test counts (per the prior lesson), and still missed the canonical env var lists in CLAUDE.md (line ~295 + ~399), docs/SECRETS.md, README.md project tree, and SETUP.md `.env` template. User caught it for the second time in three sessions with the exact same question: "did you update all the relevant docs?"

**Why:** The previous lesson narrowed the grep target to "test counts and action lists". I treated that as exhaustive instead of as one example of a broader category. Env vars, file additions to the project tree, and references in `docs/SECRETS.md` all leak across the same canonical-list pattern.

**How to apply:** Extend the pre-commit grep pass to cover ALL canonical lists that a new addition could leak into. Run these grep checks before any commit that touches:
- **New module file** → grep README project tree (`grep -n "<sibling_file>" README.md`) + grep CLAUDE.md architecture (`grep -n "<directory>/" CLAUDE.md`).
- **New env var** → grep every existing env var name (`grep -rn "ERRANDER_" --include='*.md'`) — every place that lists *some* env vars MUST list yours too if it's user-facing. Specifically check CLAUDE.md secrets block, docs/SECRETS.md, SETUP.md `.env` template.
- **New ActionType / manifest** → grep `BUILTIN_ACTIONS` count assertions in tests, action enum lists in CLAUDE.md / AI-ARCHITECTURE.md.
- **New test file** → grep test count in README (3 places) + STATUS + tasks/todo.

This is now mandatory before every commit, not optional after the user asks. The pattern is: **whatever I'm adding, grep the *type* of thing it is across the repo and update every canonical list.**

## 2026-05-22 — Signing-secret-missing must fail loud, not silently disable signing

The `signed_url.py` module raises `SigningSecretMissingError` when `ERRANDER_SIGNING_SECRET` is unset. A common alternate design — auto-generate an ephemeral secret in memory, or warn-and-continue — would create a critical vulnerability: an attacker who could cause the env var to be unset (CI misconfiguration, container restart with a missing secret) would be handed a system where unsigned URLs flow because the signature check has been silently bypassed.

**Why:** Defense-in-depth for HMAC-signed URLs means failing closed on every layer. The verifier checks signature, then expiry. The signer refuses to issue without a secret. Both refusals are loud. Tests cover the env-var-missing path explicitly so future refactors can't introduce a silent-degrade.

**How to apply:** Any future security primitive that depends on an env var or config (signing key, encryption key, secret token) MUST fail loud when absent. Never default to "if missing, generate one" or "if missing, skip the check." Make the test for the failure case as prominent as the test for the success case.

## 2026-05-22 — Vibe-coding with LLMs requires defense-in-depth for invariants because no LLM session has persistent memory

Captured a substantive implementation contract in `lessons.md` after Session 2a (layered drift gates, per-object parsers never silently drop). User correctly pointed out: a *future* LLM session asked to add a second object-level destructive action won't grep `lessons.md` unless something nudges it. The lesson is at risk of being silently re-invented worse.

The fix is multi-layer persistence, because no single layer is reliable:

1. **`CLAUDE.md` — auto-loaded every conversation.** High-priority architectural invariants live here. Implementation contracts now have a dedicated subsection.
2. **Auto-memory (`~/.claude/projects/.../memory/`)** — auto-loaded every conversation. Project-specific patterns and pointers to the reference implementation live here. Add a `pattern_*.md` memory entry whenever a non-trivial invariant is established.
3. **`# INVARIANT:` markers in source code** — grep-discoverable at the load-bearing call sites. Each marker cites its contract in CLAUDE.md. Grep `INVARIANT` lists them all.
4. **Tests with descriptive names** — `TestExecuteNode::test_snapshot_hash_mismatch_refuses_execution` documents the behavior in a place CI enforces.
5. **Code abstraction (base class) when N>=2** — the strongest safeguard, but premature with N=1. The invariants then migrate from `# INVARIANT:` comments into a base class that physically prevents violations.

**How to apply:**
- When establishing a significant implementation contract, don't stop at `lessons.md`. Promote to CLAUDE.md (Implementation Contracts section), add `# INVARIANT:` markers at the load-bearing sites in source, add a `pattern_*.md` memory entry, and add a doc-sync rule that mandates the grep before similar future work.
- When N reaches 2 (second action with the same pattern), extract a base class. The invariants then move from comments to inheritance contracts. Tests migrate to the base-class test suite.
- Never argue "but the lesson is documented" as a substitute for these mechanisms. Documentation alone is unreliable when LLMs have no persistent attention.

## 2026-05-22 — Drift gates must compare assessment snapshot hashes, not just per-object state — defence in depth

In `docker_hygiene.execute_node`, both layers of drift detection are required:
1. **Snapshot-level gate (Python):** compare `compute_assessment_hash(current_assessment)` against `approval.snapshot_hash`. If they differ, refuse execution outright — the operator approved against a stale view of the world.
2. **Per-object gate (wrapper):** the `errander-docker-remove-v2` wrapper re-queries each object's current state at execution time and skips drifted ones (e.g., dangling image that's now re-tagged, container that's now running).

The Python snapshot gate catches the "whole assessment is stale" case (e.g., the assessment ran an hour ago, lots changed); the wrapper catches the "this specific object changed since the snapshot" case. Neither alone is sufficient. The snapshot hash deliberately omits volatile fields (`size_bytes`) so it doesn't trigger false drifts when only the size changed.

**Why:** A single drift gate creates a race window between approval and execution. The defence-in-depth design closes the window: the snapshot hash rejects bulk drift; the wrapper rejects per-object drift even within a stable snapshot.

**How to apply:** For any future destructive action that supports object-level approval, design two drift gates: a hash-level snapshot pin in the orchestrator + per-object re-validation in the wrapper. Document which fields are *in* the snapshot hash vs which are deliberately excluded as volatile.

## 2026-05-22 — Wrapper output that returns per-object results must NEVER silently drop approved objects

In `parse_remove_v2_output`, when the wrapper returns results for fewer objects than were approved (e.g., crashed mid-loop, network glitch), the missing objects are recorded as `RemovalStatus.FAILED` with `error="no_result_from_wrapper"` — never silently dropped from the returned tuple. Conversely, when the wrapper returns a result for an *un-approved* object (which should never happen), the parser logs an error and drops the result rather than trusting it.

**Why:** Silent drops violate the Exact-Object Approval invariant — the operator approved N objects, the audit log must record N outcomes. If the wrapper failed for some, the audit shows that explicitly. Trusting wrapper output for objects we didn't approve would create a path for the wrapper to remove things the operator didn't see.

**How to apply:** Any per-object wrapper output parser must (a) ensure every input object has a corresponding result in the output (synthesise `FAILED` if missing), (b) drop wrapper-emitted results for objects not in the input set. Both are non-negotiable for the audit invariant to hold.

## 2026-05-21 — The doc-sync rule covers more than the "always update" list — test counts and action lists leak into README, CLAUDE.md, SETUP.md, AI-ARCHITECTURE.md

After shipping Session 1 of docker_hygiene I updated STATUS.md, command-log.md, tasks/todo.md, and tasks/lessons.md (the "always update" list) but missed: README.md test-count references (3 places: tech stack table, project tree, key commands), CLAUDE.md "v1 Scope" action count + Docker prune wording, SETUP.md docker-cleanup section (needed a forward-looking transition note), and docs/AI-ARCHITECTURE.md Layer B sub-graph list. The user had to ask "did you update all the relevant docs and md file?" to surface it.

**Why:** The doc-sync rule in CLAUDE.md has two lists — "always update" and "update when relevant". The second list isn't a checklist I run through automatically; I tend to forget it when the change feels small. But test counts and action lists are exactly the kind of facts that get scattered across the repo and rot when not updated.

**How to apply:** After the "always update" list, run two grep checks before committing:
1. `grep -rn "<old test count>" --include='*.md'` — catches any test count that needs bumping.
2. `grep -rn "docker_prune\|6 actions\|<other affected term>" --include='*.md'` — catches scope/action-list references.
If a new manifest is added: also grep for the manifest list in docs/AI-ARCHITECTURE.md and CLAUDE.md's v1 scope. Treat the second list in the doc-sync rule as a *mandatory grep pass*, not a "maybe."

## 2026-05-21 — Adding a new ActionManifest requires updating multiple test files, not just `test_registry.py`

When the `docker_hygiene` manifest was registered in `BUILTIN_ACTIONS`, two separate test files failed on hardcoded count assertions: `tests/agent/subgraphs/test_registry.py` (`len(BUILTIN_ACTIONS) == 6`) and `tests/agent/subgraphs/test_service_restart_manifest.py` (`len(BUILTIN_ACTIONS) == 6` — duplicated from registry tests). Both needed to bump to 7.

**Why:** Manifest count is asserted in multiple places defensively. There's no single source of truth for "how many actions exist" in tests.

**How to apply:** When adding a new ActionManifest, grep tests for `BUILTIN_ACTIONS) ==` and `len(BUILTIN_ACTIONS)` before running pytest. Update every count assertion in one pass. Future maintainers should consider consolidating these into a single registry test file (out of scope for this session).

## 2026-05-21 — Docker actions need special-case handling in `target_validation.py` — they don't fit the generic manifest loop

`target_validation.py` has a generic loop that iterates `BUILTIN_ACTIONS` and probes each action's `required_wrappers`. Docker is explicitly skipped because it has a `command_mode` (`wrapper`/`direct_sudo`/`disabled`) that the generic loop can't represent. When `docker_hygiene` was added (its own manifest, its own wrappers, also `command_mode`-gated), the generic loop tried to probe its wrappers too — breaking `test_disabled_mode_skips_docker_checks` which asserted no docker-related commands when mode is disabled.

**Why:** Each docker action has its own privilege escalation model. `docker_prune` supports three modes; `docker_hygiene` supports two (wrapper-only — per-object validation requires the wrapper). The generic loop assumes one wrapper-probing path per action; docker actions have command_mode-driven branching that needs custom code.

**How to apply:** When adding a new action that involves privileged commands with multiple modes, exempt it from the generic wrapper loop in `target_validation.py` (alongside `docker_prune` and `docker_hygiene`) and add a dedicated probe block. The backward-compat path (`enabled_actions=None`) should be conservative and only probe actions that are on by default or explicitly enabled — not every registered action.

## 2026-05-21 — "HITL dissolves safety concerns" is the wrong framing — exact-object approval is what matters

In a Docker scope discussion I framed Human-In-The-Loop approval as sufficient safety justification for surfacing more findings: "HITL dissolves most safety concerns." The user's SRE consultant correctly pushed back — a human can rubber-stamp a bad plan if the approval artifact is vague. The protection comes from the **evidence quality of what is being approved**, not the approval gesture itself.

The correct framing (now codified in CLAUDE.md → AI Safety Invariant → Exact-Object Approval):

> Agent presents exact objects → operator approves exact objects → execution removes only those exact approved objects → wrapper re-validates each object at execution time → audit logs every individual object removed.

**Why:** "Approved Docker cleanup" is not a meaningful approval. "Approved removal of these 4 specific image IDs and 2 stopped container names" is. Action-level approval lets the operator be wrong about *what* they approved; object-level approval doesn't.

**How to apply:** When proposing any destructive action (remove/delete/destroy), check that:
1. The approval artifact references exact objects (IDs, names, paths), not action categories.
2. The wrapper re-validates each object's state at execution time, not just at approval time.
3. The audit log has one row per object removed, not per batch.
4. If the design only supports "approve the action" without enumerating objects, the design is wrong — flag it and propose object-level redesign before writing code.

Never argue that HITL alone makes destructive automation safe.

## 2026-05-21 — Page functions that render live data must guard every dict access when the provider returns empty sentinels

When rendering a live-mode page with an empty data store, any `dict["key"]` access on a sentinel dict (e.g., `probe = {}`, `nodes = []`) raises `KeyError` or `ValueError`. Three patterns to always apply:

1. **Empty list with `[0]` access**: `(sch.get("next_runs") or ["—"])[0]` — `or` replaces the empty list before indexing.
2. **`max()` over an empty generator**: `max((n["x"] for n in nodes), default=0)` — the `default=` kwarg avoids `ValueError`.
3. **Dict access when the dict may be `{}`**: guard the entire rendering block with `if not probe:` and render a "no data" placeholder card instead of scattered `.get()` calls.

**Why:** Live mode with no stores returns empty collections. Fixture mode always had data so crashes were invisible during development. The fix is to guard at the top of each rendering block, not in every individual access.

**How to apply:** After any change that introduces a new live-mode code path, run `test_page_*_live_renders` tests. They catch these crashes immediately since they use an unrefreshed LiveProvider.

## 2026-05-21 — Helper functions that build sub-sections can also contain hardcoded fixture data

`page_settings()` had two sources of fixture leaks: the `_SETTINGS_SECTIONS` dict (gated in the previous pass) and a separate `restart_rows` loop that iterated `_ENV_RESTARTABLE_UNITS` directly. Even when the main section is gated, sub-section helpers and inline loops need their own gates.

**How to apply:** After gating a page function, run the regression tests against every sub-section helper called by that page (`_inventory_env_breakdown`, `_live_settings_sections`, `restart_rows`, etc.). Grep the page function body for any dict or constant that isn't from the provider.

## 2026-05-21 — Live-mode regression tests must assert specific fixture strings, not just "no crash"

After gating evidence overlays, there can still be fixture strings in page renders from:
1. Hardcoded inline strings in f-strings (dates, counts, model names)
2. Default values in `.get()` calls (e.g., `ev.get("window", "Tue/Thu 02:00–04:00 UTC")`)
3. Documentation/example text in UI reference tables

**Fix:** Write parametrized regression tests that render every page in live mode and assert a known list of fixture-only strings never appear (`"2026-04"`, `"prod-0423"`, specific hostnames, model names, demo IPs, hardcoded counts). Run these after each evidence-gating session. Any failure reveals a new leak.

**How to apply:** Build `_FIXTURE_ONLY_STRINGS` as a list of unambiguous demo values. Add to it whenever a new SRE QA round finds a new leak. The test prevents regressions.

## 2026-05-21 — Fixture evidence overlays must be gated at the helper level, not at every call site

When introducing a provider layer that separates `fixture` mode from `live` mode, any fixture-only data (e.g., `VM_EVIDENCE`, `BATCH_EVIDENCE`, `APPROVAL_EVIDENCE`, `audit_evidence_for()`) will silently leak into live mode if page functions call them directly. The correct fix is to introduce thin gate helpers (`_ev_vm()`, `_ev_batch()`, `_ev_approval()`, `_ev_audit()`) that return `{}` / a null sentinel in live mode and the real lookup in fixture mode. Page functions then call only the gate helpers.

**Why:** Provider layer tells page functions WHERE to get live data, but fixture enrichment overlays are a separate concern — decorators layered on top. Without the gate, live mode shows fake operational facts (e.g., "prod-db-01 holds a lock", "April 2026 chart", "Qwen3-8B-AWQ") even though no live stores are connected.

**How to apply:**
1. For every fixture-only data lookup in server.py, create a `_ev_*()` helper that checks `get_provider().data_mode() == "FIXTURE"` before calling it.
2. For every static computed value (KPIs, settings sections, admin health checks), provide a `_live_*()` variant that reads from the provider or `os.environ`.
3. For computed aggregates (chart data, total counts), use `if _is_fixture:` blocks — a list comprehension that generates the chart only in fixture mode; a computed value from the provider in live mode.

## 2026-05-21 — anyio 4.13 + pytest on Windows makes asyncio event loop creation take ~250 s per test

With `asyncio_mode=AUTO` and `anyio-4.13.0` installed, any event loop creation inside a pytest run — even `asyncio.new_event_loop()` in a sync test — takes ~250 seconds. The root cause is anyio intercepting the loop factory.

**Fix:** Replace async test bodies that call `asyncio.new_event_loop().run_until_complete()` with sync alternatives:
- Contract tests: `inspect.iscoroutinefunction(obj.method)` proves the method is awaitable without running it.
- Behaviour tests: inject directly into the provider's private cache attributes (`provider._approvals = [...]`) and verify the getter returns them.
- Don't test the async refresh integration path in the same test file — it belongs in a separate integration test that can tolerate long runtimes.

**Why:** anyio's event loop plugin runs even for non-asyncio tests in AUTO mode. The workaround is to never create event loops in the fast unit test suite.

## 2026-05-20 — Always read the full function signature before wiring into a startup hook

`load_inventory(config_path: Path)` requires a positional argument. `_on_startup` was calling it with no args, crashing metrics collection silently at server start. The crash was invisible unless you read server logs — no 500 to the browser.

**Why:** Startup hooks run once at process start with no request context. Errors there are swallowed unless explicitly logged; the server continues without the feature.

**How to apply:** Before wiring any function into `_on_startup` / `_on_cleanup`, grep its signature. Wrap in try/except and log the exception — never let a startup feature failure be silent.

## 2026-05-20 — SQLite WAL mode is mandatory for file-backed DBs with concurrent readers

`sqlite3.OperationalError: disk I/O error` on aiosqlite is almost always a journal locking issue (default DELETE journal mode creates a `.sqlite-journal` file that blocks readers). Fix: `PRAGMA journal_mode=WAL` (write-ahead log, multiple readers + one writer allowed) + `PRAGMA busy_timeout=10000` (10 s wait before raising instead of immediate failure) + `aiosqlite.connect(timeout=30)`.

**Why:** The web server and agent process both open the same SQLite file. DELETE journal mode serializes all access; a slow write blocks the server's reads.

**How to apply:** Apply these three PRAGMAs in every `_on_startup` that opens a file-backed SQLite DB. In-memory DBs (tests) don't need them.

## 2026-05-21 — JS braces inside Python f-strings must be doubled as {{ / }}

Any `{` or `}` character inside a Python f-string that is NOT an interpolation expression must be escaped as `{{` or `}}`. This includes JavaScript function bodies, arrow functions with block bodies, and object literals. The Python parser raises a `SyntaxError` (not at the `{` itself but at whatever follows it, making the error message confusing).

**Why:** The `_batchFilter` JS block was added inside a `return f"""..."""` without escaping its braces. The module compiled silently in tests (pytest imports from `.pyc` cache) but failed at runtime on a fresh process, making the server unimportable.

**How to apply:**
1. After adding any JS block to an f-string, immediately run `uv run python -m py_compile errander/web/server.py` to catch it.
2. Use the pattern from `_invFilter` and `_apprFilter` as the reference — they correctly use `{{` / `}}`.
3. The smoke test in `tests/ui/test_web_server_smoke.py` will catch this class of error automatically going forward.

## 2026-05-20 — Dead `href="#"` links are a trust signal — disable them, don't leave them

Placeholder `<a href="#">` controls that do nothing (no onclick, no server route) destroy operator trust on a safety-critical UI. The correct patterns:
- If the action requires a CLI command: `<button disabled title="Use CLI: errander --run-now">` with `opacity:0.45;cursor:not-allowed`
- If it's a v2 feature: `<button disabled title="v2 roadmap">LABEL <span>v2</span></button>`
- If there's a JS function to call: `<a href="#" onclick="event.preventDefault();_fn()">` — `event.preventDefault()` is mandatory

**Why:** An operator clicking a button that silently does nothing learns to distrust the whole UI. A disabled button with a tooltip tells them exactly what to do instead.

**How to apply:** Before any UI review, grep for `href="#"` without `onclick` — those are dead links. Every one needs either a JS handler, a disabled state, or removal.

## 2026-05-20 — isinstance guard is required when passing factory-created objects through test mocks

`AuditStore.make_batch_store()` returns a real `BatchStore` in production but a non-awaitable `MagicMock` in tests that mock `AuditStore`. If `build_batch_graph` calls `await batch_store.insert(...)` unconditionally, test runs raise `TypeError: object MagicMock can't be used in 'await' expression`. Fix: guard with `isinstance(_raw, BatchStore)` before assigning; set `_batch_store = None` when the guard fails so the code path is skipped in tests.

**Why:** `MagicMock(spec=AuditStore)` returns a synchronous MagicMock from `.make_batch_store()`, not a real BatchStore. Tests for the graph don't need BatchStore behavior; they need the graph to run without crashing.

**How to apply:** When a graph node optionally calls into a store, always accept `store: StoreType | None = None` and guard every `await store.method()` call with `if store is not None`. Wire in the `isinstance` check at graph-build time when the store comes from a factory method.

## 2026-05-20 — JsonPlusSerializer.loads_typed returns the object directly, not a tuple

`JsonPlusSerializer.dumps_typed(obj)` returns `(type_str, bytes_data)`. `JsonPlusSerializer.loads_typed((type_str, bytes_data))` returns the deserialized object directly — NOT a `(type, value)` tuple. Writing `_type, result = SERDE.loads_typed(...)` raises `ValueError: too many values to unpack`. Also: the serializer uses `"msgpack"` as `type_str` (binary format), not `"json"` — always use the actual string returned by `dumps_typed`.

**Why:** The API is `(type_str, bytes) → object`, not `(type_str, bytes) → (type_str, object)`. The type string is for dispatch only; the result is the reconstructed object.

**How to apply:** In round-trip tests: `type_str, bytes_data = SERDE.dumps_typed(state)` then `result = SERDE.loads_typed((type_str, bytes_data))`.

## 2026-05-20 — generate_report_node is the single terminal status-update point — don't add update_status calls elsewhere

`validate_window_node` and `check_fleet_health_node` both route to `generate_report_node` when aborting. Adding `await batch_store.update_status(...)` in those intermediate nodes causes double-updates (first call sets ABORTED, second call in `generate_report_node` hits the `WHERE status='running'` guard and is silently ignored — but this is fragile). Keep `generate_report_node` as the single terminal point that writes batch status.

**Why:** The `WHERE status='running'` guard prevents double-terminal-writes, but it means intermediate nodes that set status early will prevent `generate_report_node` from updating it at all if the guard is ever loosened.

**How to apply:** Only call `batch_store.update_status()` from `generate_report_node`. Intermediate abort nodes just set state fields (`error`, `approved=False`) and route to `generate_report`.

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

## 2026-05-20 — Mock async context managers need __aenter__/__aexit__, not async def returning a value

When a test uses `async with session.get(url) as resp`, the mock for `session.get` must return an object that implements `__aenter__` and `__aexit__` as coroutines. Defining `async def _fake_get()` and setting `mock.get = _fake_get` returns a coroutine, not a context manager — `async with` on a plain coroutine raises `AttributeError: __aenter__`. Fix: use a plain `def` that returns an `AsyncMock` with `__aenter__` and `__aexit__` set. Also: `mock_session.close()` must be set to `AsyncMock()` before any test that awaits `await collector.close()`, otherwise `MagicMock().close()` returns a non-awaitable.

**How to apply:** Any time a mock replaces an aiohttp `ClientSession`, explicitly wire both `close = AsyncMock()` and a factory for `get()` that returns a context-manager mock (not a coroutine).

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
