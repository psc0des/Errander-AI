# 61 — Dashboard Chat (Plan B, phase 1)

## What was built and why

`/ui/chat` is a multi-turn, read-only chat console in the web dashboard —
"is web-02 healthy?", "any disk issues on the fleet?" — answered by the
**same** Plan A engine that powers `--ask`/`--ask --agentic`. The chat is
deliberately a thin surface, not a second brain: it builds no queries of
its own, gets no execution capability, and routes every question through
`OperatorAssistant.investigate()` or `InvestigationAgent.investigate_agentic()`
exactly as the CLI does. Default off (`ERRANDER_CHAT_ENABLED=false`).

This is phase 1 only. Streaming (SSE) and "propose an action → existing
approval flow" are deferred — see the source design doc
(`tasks/dashboard-chat-implementation-plan.md`) phases 2–3.

## Key concepts

### Reconciling a plan written before the engine existed

The source plan predates two things: the R3 process split (it references
`errander/observability/metrics.py` for UI routes — that code moved to
`errander/web/ui.py` and `errander/web/__main__.py` months before this
session) and Plan A itself (the engine this plan depends on). Its own §0
calls this out explicitly: *"reconcile against the as-built engine before
writing chat code."* The two reconciliation findings that actually changed
the build:

1. **The web process had zero LLM/Prometheus/ELK/disk-history/baseline
   wiring.** `errander/web/__main__.py` constructed `AuditStore`,
   `ApprovalRequestStore`, `UserStore`, etc. — but nothing the investigation
   engine needs beyond `AuditStore`. The plan's "just call the engine"
   framing assumed dependencies that didn't exist yet; building them is the
   single largest piece of `__main__.py`'s diff.
2. **Inventory type mismatch.** The engine requires `inventory:
   InventoryConfig` (`validate_inventory()`'s return type). `__main__.py`
   already loads inventory — but as `base_inventory: list[VMTarget]`
   (`load_inventory()`, for the read-only `/ui/inventory` page) — a
   different shape entirely. Chat needs its own `validate_inventory()` call,
   gated behind `settings.chat_enabled` so it doesn't run when chat is off.

### One AppKey bundle instead of six

`build_ui_app()` already threads ~9 individual optional store params. Adding
`llm_client`, `prometheus_client`, `elk_client`, `disk_history_store`,
`baseline_store`, and `inventory` as six *more* individual params (and six
more `AppKey`s) would make an already-long signature worse for dependencies
that are **only ever used together** — unlike `audit_store`/
`ai_decision_store`, which are independently useful elsewhere in the UI and
already have their own keys. `ChatEngineDeps` (a small dataclass) bundles
exactly the new ones under one `CHAT_ENGINE_DEPS_KEY`, while `chat_store`
itself gets its own key and is threaded individually — it follows the
existing "one store, one key" convention because, unlike the engine deps,
it's the kind of object a future page might reasonably want on its own.

```python
@dataclass
class ChatEngineDeps:
    disk_history_store: VMDiskHistoryStore
    baseline_store: BaselineStore
    inventory: InventoryConfig
    settings: Settings
    llm_client: LLMClient | None = None
    prometheus_client: PrometheusClient | None = None
    elk_client: ElkClient | None = None
```

`None` at the `CHAT_ENGINE_DEPS_KEY` app-state slot means "chat is enabled
but can't answer anything" (e.g. `chat_enabled=true` but no
`inventory.yaml` found) — distinct from `CHAT_STORE_KEY` being `None`
("chat is disabled entirely"). The thread-list and conversation-history
pages still work in the first case (`chat_store` exists); only the
question-answering part degrades, with its own inline notice.

### The contract, not the internals

The chat handler's call into the engine is exactly the branch
`run_ask_query()` (the CLI) already has:

```python
if engine_deps.settings.investigation_agent_enabled and engine_deps.llm_client is not None:
    response = await InvestigationAgent().investigate_agentic(question, ...)
else:
    response = await OperatorAssistant().investigate(question, ...)
```

Both return `AssistantResponse` (`errander/models/analysis.py`) — the same
model the CLI prints. The chat module never imports anything from either
engine's *internals* (tool registry, prompt templates) — only their public
`investigate`/`investigate_agentic` entry points. If a future change needs
the chat to do something the engine doesn't support yet (a new tool, native
multi-turn awareness), that change belongs in Plan A, not in the chat
handler — extending the engine, never forking a second one.

### History is folded into the question text — a deliberate v1 simplification

The engine's contract is `question: str → AssistantResponse`. Rather than
extend that signature to natively accept conversation history (which would
touch Plan A's already-shipped, already-tested surface), the chat handler
folds prior turns into the question text itself before redaction:

```python
history_text = _render_history_for_prompt(existing, engine_deps.settings.chat_max_history_turns)
question = f"{history_text}\nOperator: {new_message}" if history_text else new_message
question, _ = ContextRedactor().redact(question)
```

This keeps Plan A's definition-of-done intact through this change (its
contract didn't move) at the cost of the engine seeing history as plain
text context rather than structured turns. `chat_max_history_turns`
(default 20) caps how much history gets folded in — independent of how many
messages exist in the thread, so a long-running conversation doesn't grow
the prompt unboundedly.

### Per-user ownership breaks the "bootstrap mode stays open" pattern — on purpose

Every other read-only `/ui/*` page renders for anonymous visitors when zero
user accounts exist (`_session_auth_middleware`'s bootstrap passthrough).
The chat handlers explicitly redirect to `/ui/login` instead, even in that
mode:

```python
user = _request_user(request)
if user is None:
    raise web.HTTPFound("/ui/login")
```

Threads are owned by `user.username` (`ChatStore.list_threads(user_id, ...)`,
ownership-checked delete/post) — there's no sensible behavior for "whose
thread is this" without a real identity. The other pages show global,
ownerless data, so anonymous bootstrap access is safe for them and isn't
for chat. See `tasks/lessons.md` for the longer version of this rationale.

### CSRF and ownership are both enforced without new per-handler code (mostly)

The existing global `_csrf_middleware` (`request.method == "POST" and
request.path.startswith("/ui")`) already covers `/ui/chat/*` POSTs with zero
new code — `tests/web/test_chat.py::TestChatCSRF` proves this directly
(POST without a token → 403). Ownership, however, **is** new code per
handler — `if thread is None or thread.user_id != user.username: ...` — since
CSRF and ownership are different concerns (CSRF proves the request came from
this browser session; ownership proves this session's user is allowed to
touch this specific thread).

### Rate limiting a double-submit without new infrastructure

`chat_max_history_turns` only caps what's *sent* to the engine per call, not
how often a call can be made. A double-submit (or impatient double-click)
would otherwise duplicate the turn and re-run the (potentially slow,
self-hosted-vLLM) engine call. The guard is a plain timestamp check against
the thread's own last message — no new cache/Redis dependency:

```python
if existing:
    elapsed = (datetime.now(tz=UTC) - existing[-1].created_at.astimezone(UTC)).total_seconds()
    if elapsed < _CHAT_MIN_SECONDS_BETWEEN_TURNS:
        raise web.HTTPFound(f"/ui/chat/{_uq(thread_id)}")  # drop silently
```

## Gotchas

- **`kill $!` after backgrounding `uv run python -m errander.web ...` does
  not stop the server on Windows.** Same root cause as the `timeout`/zombie
  issue from Plan A's build: `uv run` → `uv.exe` → `python.exe` is a 2-hop
  chain, and Windows doesn't propagate the signal/PID relationship through
  it the way POSIX job control does. Verify a backgrounded server actually
  stopped (re-`curl` its port) rather than trusting `kill $!`'s success.
  Find the real process by exact command-line or listening-port match
  (`Get-NetTCPConnection -LocalPort <port> | ... OwningProcess`) before
  `taskkill /F /PID`.
- **`AuditStore | None` from an `AppKey` needs an explicit guard before
  passing it to the engine.** `investigate()`/`investigate_agentic()` both
  require a non-`None` `AuditStore` — mypy strict won't narrow
  `request.app.get(AUDIT_STORE_KEY)` to `AuditStore` without an explicit
  `if audit_store is None: <return/raise>` first.
- **`MagicMock(name=...)` doesn't set `.name`** — same gotcha as Plan A's
  tests; not hit here directly but worth re-flagging since `ChatEngineDeps`
  test fixtures use `MagicMock()` stand-ins for `inventory`/`settings` too.

## Code map

| Piece | Where |
|---|---|
| Conversation store | `errander/safety/chat_store.py` (`ChatStore`, `ChatThread`, `ChatMessage`) |
| Migration | `errander/safety/migrations.py` migration 16 (`chat_threads`, `chat_messages`) |
| Routes + handlers + nav | `errander/web/ui.py` (`_ui_chat*`, `ChatEngineDeps`, `CHAT_STORE_KEY`, `CHAT_ENGINE_DEPS_KEY`) |
| Process wiring | `errander/web/__main__.py` (gated on `settings.chat_enabled`) |
| Settings | `errander/config/settings.py` (`chat_enabled`, `chat_max_history_turns`, `chat_max_threads_per_user`) |
| Store tests | `tests/safety/test_chat_store.py` |
| Route/CSRF/engine-call tests | `tests/web/test_chat.py` |
| Isolation proof | `tests/web/test_import_isolation.py::test_chat_engine_imports_do_not_drag_in_execution_code` |

## Quiz yourself

1. Why does `ChatEngineDeps` bundle six dependencies under one `AppKey`
   instead of giving each its own key, the way `audit_store`/
   `ai_decision_store` each have their own?
2. The chat handlers redirect anonymous bootstrap-mode visitors to
   `/ui/login`, unlike every other read-only `/ui/*` page. What property of
   chat's data makes this the *correct* exception rather than an
   inconsistency to "fix"?
3. `_ui_chat_message_post` checks `audit_store is None` and bails before
   calling the engine, even though the AppKey-stored value is type
   `AuditStore | None`. What would mypy strict flag if that check were
   removed?
4. History is folded into plain question text rather than passed to the
   engine as structured turns. What did this *avoid* changing in Plan A,
   and what did it cost in return?
5. `tests/web/test_chat.py::test_new_thread_post_without_csrf_is_403` adds
   no new CSRF code to prove this — why does the *existing* middleware
   already cover the new `/ui/chat/*` routes with zero changes?
6. The "no LLM/inventory configured" notice and the "chat disabled" notice
   are two different messages, gated by two different `None` checks
   (`chat_store` vs `chat_engine_deps`). What real deployment scenario does
   each one correspond to?
