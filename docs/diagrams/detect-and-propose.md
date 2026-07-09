# Detect-and-Propose — Agentic Origination, HITL Execution (Implementation Diagram)

> Design-time diagram for the plan in `tasks/fable-plan.md`, reconciled against the current
> as-built code (post-R3 process split, post-2026-06-23 chat removal). Not yet implemented —
> see `tasks/todo.md` for status. Renders inline on GitHub. Companion to
> [`ARCHITECTURE.md`](../../ARCHITECTURE.md) at the repo root (whole-system view) and
> `investigation-agent-dashboard-chat.md` (the Plan A engine internals that Phase 2 adopts).

```mermaid
flowchart TB
  classDef layerA fill:#DAE8FC,stroke:#6C8EBF,color:#15314B;
  classDef layerB fill:#D5E8D4,stroke:#82B366,color:#1E3A1E;
  classDef store fill:#F5F5F5,stroke:#666666,color:#222222;
  classDef tool fill:#FFF2CC,stroke:#D6B656,color:#5B4A12;
  classDef human fill:#FFE6CC,stroke:#D79B00,color:#7A4F00;
  classDef guard fill:#E1D5E7,stroke:#9673A6,color:#4B2E63;

  subgraph SIG["Signals — existing daily probe (Layer B, read-only on targets)"]
    PROBE["agent/probe.py<br/>disk_growth_alerts · drift_changes · failed_login_summary"]:::layerB
  end

  subgraph ORIG["Origination — where the new agency lives (and nowhere else)"]
    DET["Phase 1 — deterministic detector<br/>post-probe hook: signal → template proposal<br/>no LLM · owns dedup: one open proposal per (vm, action) ·<br/>re-probe refreshes evidence, never duplicates"]:::layerB
    TRIG{"Phase 3 — trigger gate<br/>ERRANDER_INVESTIGATION_TRIGGER_ENABLED?<br/>max_investigations_per_probe · 24h dedup window"}:::layerA
    INV["Phase 2 — InvestigationAgent (Layer A)<br/>bounded ReAct loop per<br/>tasks/investigation-agent-implementation-plan.md §3–§6<br/>output: AssistantResponse + proposed_work[]"]:::layerA
    VAL["proposed_work validation (guardrail)<br/>action_type ∈ fixed action set · vm_id ∈ inventory ·<br/>_INJECTION_RE on all strings · cite-only-tools-called"]:::guard
  end

  subgraph TOOLS["Read-only tools (Plan A §4 — unchanged)"]
    T1["query_prometheus"]:::tool
    T2["search_logs (ELK)"]:::tool
    T3["get_audit_events"]:::tool
    T4["get_disk_trend"]:::tool
    T5["get_vm_facts"]:::tool
    T6["list_inventory"]:::tool
  end

  subgraph GUARD["Guardrails — every hop (Plan A §5, adopted)"]
    RED["ContextRedactor"]:::guard
    BUD["ContextBudgeter / size caps"]:::guard
    AUD[("AIDecisionStore<br/>investigation_agent_step — N hops ⇒ N rows")]:::store
  end

  PSTORE[("proposal_store (Phase 1, new migration)<br/>AgentProposal: exact target · action_type ·<br/>evidence chain · confidence · origin ·<br/>status: pending/approved/rejected/snoozed/expired ·<br/>7-day expiry · atomic decide()")]:::store

  subgraph HITL["Human decision — Phase 1 (named, authenticated, RBAC)"]
    UI["Web UI proposal queue (web/ui.py)<br/>AGENT-ORIGINATED badge · origin + model provenance ·<br/>evidence chain rendered · approve / reject / snooze"]:::human
    SLACK["Slack notify-and-link<br/>(no decision authority — existing pattern)"]:::human
  end

  subgraph LB["EXISTING Layer B — unchanged, reached ONLY via the human gate"]
    RUN["Targeted run (mirrors --restart-service pattern)<br/>assess fresh state → plan → exact-object approval<br/>where risk tier demands (D1: proposal approval is<br/>work origination, not execution authorization) →<br/>execute → per-object audit"]:::layerB
  end

  FACTS[("Phase 4 memory — VMFactsStore (existing)<br/>proposal decisions + run outcomes as facts ·<br/>rejected ≥2× per (vm, action) ⇒ suppress 14d +<br/>'needs human review' digest line · snooze honored")]:::store

  EVAL["Phase 5 — eval harness (offline, CI)<br/>golden scenarios with known root causes ·<br/>proposal precision/recall · citation validity ·<br/>opt-in LangSmith tracing"]:::guard

  PROBE --> DET --> PSTORE
  PROBE --> TRIG
  TRIG -->|enabled + under caps| INV
  TRIG -->|"disabled / capped / LLM down —<br/>template proposal already filed, stands as-is"| DET
  INV <-->|tool_calls| TOOLS
  INV -->|every tool result| RED --> BUD
  INV -.->|per hop| AUD
  INV --> VAL -->|"accepted items only —<br/>enrich existing / create via store API"| PSTORE
  PSTORE --> UI
  PSTORE -.->|notify only| SLACK
  UI -->|approved| RUN
  UI -->|rejected / snoozed| FACTS
  RUN -->|outcome| FACTS
  FACTS -->|suppression + context| DET
  FACTS -->|"facts into context<br/>(StoredSignalContext / FleetContext pattern)"| INV
  EVAL -.->|replays synthetic probe fixtures through| DET
  EVAL -.->|scores| INV

  subgraph NEVER["Structurally unreachable from the investigator"]
    NLB["execution / SSH / ApprovalRequestStore /<br/>rollback / locking"]:::layerB
  end
  INV -.->|"no code path exists — test-enforced<br/>(import ban incl. ApprovalRequestStore, §5.1)"| NEVER
```

## Reading the diagram

- **Green (Layer B / deterministic)** — the probe, the Phase 1 detector, and the execution
  path. Note the detector is **not AI**: it turns raw probe signals into template proposals
  with no LLM, works when the LLM is down, and is the permanent fallback (design decision
  D2 in `tasks/fable-plan.md` §2). It also owns dedup — the investigator can only enrich
  what the detector's rules admit, never flood the queue.
- **Blue (Layer A)** — the only LLM-driven region: the Phase 3 trigger gate and the Phase 2
  investigation loop (internals unchanged from `investigation-agent-dashboard-chat.md`;
  Phase 2 adopts Plan A's runtime, tools, and guardrails wholesale). Its **only** output
  path runs through the purple validation gate into the proposal store — evidence and
  suggestions, never actions.
- **Purple (guardrails)** — redaction + budget on every hop (Plan A §5), plus the new
  `proposed_work` validation: hallucinated action types or VM ids are dropped and logged;
  every string field passes the `_INJECTION_RE` shell-pattern reject; evidence may cite
  only tools actually called.
- **Orange (human gate)** — every proposal passes a named, authenticated operator in the
  Web UI. Slack is notify-and-link, as everywhere else in Errander. The `approved` edge is
  the **only** edge from origination-side nodes into Layer B, and it is D1-shaped: approval
  originates a targeted run of the existing pipeline, which re-assesses fresh state and
  raises its own exact-object approval where the risk tier demands it. The Exact-Object
  invariant is reused, not paralleled.
- **Gray (stores)** — the new `proposal_store` (separate table and lifecycle from
  `approval_requests` — a proposal is a suggestion record, not an authorization), the
  existing `AIDecisionStore` (per-hop audit rows), and the existing `VMFactsStore` closing
  the memory loop in Phase 4 (suppression + context feedback into both origination paths).
- **The bottom green box** exists to make the safety argument visual, mirroring the Plan A
  diagram: the investigator has no code path — direct or transitive — into execution, SSH,
  `ApprovalRequestStore`, rollback, or locking. Test-enforced by an import-ban test
  (fable-plan §5.1), extending the pattern of `tests/web/test_import_isolation.py`.
- **Scope note:** phases refer to `tasks/fable-plan.md` §3. The dashboard chat (Plan B)
  does not appear here — it stays out of core per the 2026-06-23 decision; this pipeline
  is the in-core refinement of that decision (fable-plan §1).
