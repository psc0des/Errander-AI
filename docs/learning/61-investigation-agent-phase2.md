# 61 — Detect-and-Propose, Phase 2: the agentic investigation engine

## What was built and why

Phase 1 (doc 60) gave Errander *deterministic* origination — a rules-based detector
turning probe signals into proposals. Phase 2 adds the **agentic** upgrade: an opt-in,
bounded, read-only **tool-calling loop** (`--ask --agentic`, default OFF) where the LLM
decides which read-only tools to call, observes results, iterates, and finally emits a
structured answer that may recommend LOW-risk work. Those recommendations become
`AgentProposal`s in the same Phase 1 queue — so the agentic path and the deterministic
detector converge on one human-approved execution pipeline.

This is the "genuinely agentic" piece the project name promised, built without weakening
a single safety boundary: the loop is **Layer A** (read-only tools, never executes), and
its output is a *suggestion* a human approves before Layer B acts.

## Key concepts

### The ReAct loop, hand-rolled on the OpenAI SDK

We did not adopt a framework (LangGraph `create_react_agent` / `langchain-openai`). Per
the Plan A decision, we kept the single provider-agnostic `LLMClient` and hand-rolled the
loop — because the guardrails (redaction + budget + audit *on every hop*) are the entire
safety value, and we want them in our own code, not injected into a framework's tool
wrappers. The new primitive is one method:

```python
async def chat_with_tools(self, messages, tools, timeout_seconds=None) -> AssistantTurn | None:
    # one turn: returns either final content or tool_calls; None on transport failure
```

`AssistantTurn`/`ToolCall` are plain dataclasses. The **agent owns the loop**
(`InvestigationAgent.investigate_agentic`), which makes it trivially testable with a
scripted fake LLM — no network, no framework. The loop:

1. system + user message → `chat_with_tools`
2. if `tool_calls`: append the assistant turn, dispatch each tool, redact + cap the
   result, append as a `role:"tool"` message, audit the hop, repeat
3. if `content`: parse the final JSON answer, done
4. cap by `max_tool_calls` and a wall-clock `timeout_seconds`; on any failure/exhaustion,
   fall back to the deterministic `OperatorAssistant.investigate`

### Guardrails are the reason it's safe (fable-plan §5)

- **Bounded**: `max_tool_calls` (default 8) and a wall-clock deadline; the loop *always*
  terminates, and on budget exhaustion it falls back rather than looping forever.
- **Redaction every hop**: tool results are *untrusted input* (logs/labels can carry
  attacker-influenced text). Each result passes `ContextRedactor` before it re-enters the
  model, and is size-capped (`_TOOL_RESULT_MAX_CHARS`) to bound context blow-up.
- **Per-hop audit**: every tool call → an `AIDecisionStore` row
  (`decision_type="investigation_agent_step"`); the final answer → `investigation_agent`.
  N hops ⇒ N+1 rows. This is the in-network record; LangSmith (if enabled) is the richer
  external trace.
- **Graceful fallback**: LLM unreachable, tool-calling unsupported (some endpoints/models
  don't support it — a 4xx returns `None`), unparseable answer, or budget hit → the
  deterministic path. The agent never raises to the operator, never blocks on the LLM.

### Layer-A isolation, enforced statically

The agent investigates and *recommends* — it must never import a Layer B execution path,
and critically it must not even import the **proposal store** (it originates suggestions;
the *caller* files them). `test_investigation_isolation.py` enforces this two ways: an AST
scan of the modules' import statements against a forbidden-prefix list, plus a subprocess
that imports the modules fresh and asserts no Layer B module landed in `sys.modules`
transitively. (The AST approach is deterministic — unlike the `sys.modules`-diff pattern,
it doesn't depend on what earlier tests imported.)

### `proposed_work` — the only origination channel, triple-validated

The agent's structured answer may include `proposed_work: [{vm_id, action_type,
rationale}]`. This is validated at three gates so a hallucination can't originate bad work:

1. **Static, in the model** (`ProposedWorkItem`): `action_type` must be in
   `PROPOSABLE_ACTIONS` (`disk_cleanup`/`log_rotation` only — never `docker_hygiene`);
   `vm_id` must match the identifier regex (no shell metacharacters).
2. **Per-item drop, in the parser**: one invalid item is dropped and logged; the rest of
   the answer survives (`_parse_final`). A bad proposal never sinks a good investigation.
3. **Inventory gate, at conversion** (`proposed_work_to_proposals` + the CLI filer): the
   vm_id must exist in live inventory *and* the action must still be enabled for that VM.

Only then does it become an `AgentProposal` (origin `investigation_agent`) in the Phase 1
store — flowing through the same human-approval → reconciler → deterministic sub-graph
path as detector proposals.

## Code walkthrough

- `integrations/llm.py` — `chat_with_tools` + `AssistantTurn`/`ToolCall`. Note it does
  **not** redact (unlike `complete`): the agent redacts every message and tool result,
  which is the correct single place for a multi-hop loop.
- `agent/investigation_tools.py` — `ReadOnlyTool` (name + JSON schema + async `run`),
  `ToolRegistry` (schemas + `dispatch` that never raises), and `build_readonly_tools`
  wiring the existing read paths: audit events, disk trend, VM facts, inventory, and —
  only when configured — Prometheus/ELK (using their *existing fixed* methods, so no new
  injection surface). Every tool validates identifier args and caps output.
- `agent/investigation_agent.py` — `InvestigationAgent` (the loop) + `_parse_final`
  (JSON + per-item validation + evidence-honesty: strips citations to tools never called)
  + `proposed_work_to_proposals` (conversion, no store write).
- `models/analysis.py` — `ProposedWorkItem` + `AssistantResponse.proposed_work`.
- `main.py` — `run_ask_query(agentic=...)` builds the registry and runs the loop with the
  deterministic assistant as fallback; `_file_agent_proposals` converts + files (the
  caller writes the store, not the agent). `--agentic` flag; three settings default-safe.

## Gotchas encountered

- **mypy + the OpenAI `create` overload**: passing `messages: list[dict]` and `tools`
  trips the heavily-overloaded stub. One `# type: ignore[call-overload]` on the call is
  cleaner than per-arg ignores (which mypy then flags as unused).
- **`**fallback_kwargs` unpacking**: a `dict[str, object]` fails mypy on `investigate(**)`
  because it checks each param type. Annotating `fallback_kwargs: dict[str, Any]` makes
  the unpack acceptable (Any suppresses per-param checks).
- **`used_pct` is a property, not a method** on `DiskDataPoint` — `first.used_pct`, not
  `first.used_pct()`. mypy caught it as "float not callable".
- **Don't let the agent write the store**: the temptation is to have the agent file its
  own proposals. Keeping it write-free (caller files) is what preserves the clean Layer-A
  contract the isolation test enforces.

## Quiz yourself

1. Why hand-roll the ReAct loop instead of using LangGraph's `create_react_agent`?
2. Tool results are redacted before re-entering the model, but the prompt is redacted in
   `investigate_agentic`, not in `chat_with_tools`. Why is that the right split?
3. The model proposes `docker_hygiene` on a real VM. At which of the three gates is it
   stopped, and what happens to the *rest* of the answer?
4. The LLM endpoint doesn't support tool calling. Trace what the operator sees.
5. Why must the investigation agent not import `proposal_store`, when filing proposals is
   the whole point of Phase 2?

## References

- Plan: `tasks/fable-plan.md` §3 Phase 2; adopted spec `tasks/investigation-agent-implementation-plan.md`.
- Diagram: `docs/diagrams/detect-and-propose.md` (blue = this loop); engine internals in
  `docs/diagrams/investigation-agent-dashboard-chat.md`.
- Phase 1 foundation: `docs/learning/60-detect-and-propose-phase1.md`.
