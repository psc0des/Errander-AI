# Investigation Agent (Plan A) + Dashboard Chat (Plan B) — Implementation Diagram

> Design-time diagram for the plan in `tasks/investigation-agent-implementation-plan.md` and
> `tasks/dashboard-chat-implementation-plan.md`, reconciled against the current as-built code
> (post-R3 process split). Not yet implemented — see `tasks/todo.md` for status.
> Renders inline on GitHub. Companion to `errander-system-architecture.md` (the whole-system view).

```mermaid
flowchart TB
  classDef layerA fill:#DAE8FC,stroke:#6C8EBF,color:#15314B;
  classDef layerB fill:#D5E8D4,stroke:#82B366,color:#1E3A1E;
  classDef store fill:#F5F5F5,stroke:#666666,color:#222222;
  classDef tool fill:#FFF2CC,stroke:#D6B656,color:#5B4A12;
  classDef ext fill:#FFE6CC,stroke:#D79B00,color:#7A4F00;
  classDef guard fill:#E1D5E7,stroke:#9673A6,color:#4B2E63;

  subgraph ENTRY["Entry points"]
    CLI["CLI: --ask --agentic<br/>(errander/main.py)"]:::ext
    WEBUI["/ui/chat<br/>(errander/web/ui.py)"]:::ext
  end

  subgraph CHATB["Plan B — Dashboard Chat"]
    CHATSTORE[("ChatStore<br/>chat_threads / chat_messages")]:::store
    HIST["fold prior turns into question text<br/>(v1 simplification — no native history param)"]:::layerA
  end

  subgraph ENGINE["Plan A — Investigation Engine (Layer A)"]
    DECIDE{"investigation_agent_enabled<br/>AND tool-calling supported?"}:::layerA
    AGENTIC["InvestigationAgent.investigate_agentic()<br/>bounded ReAct loop"]:::layerA
    DETERM["OperatorAssistant.investigate()<br/>fixed-context single LLM call (existing, untouched)"]:::layerA
    LOOP["loop: complete_with_tools() → tool_calls? →<br/>dispatch → redact+cap result → append → repeat<br/>stop: final answer | max_tool_calls | timeout_seconds"]:::layerA
  end

  subgraph TOOLS["Read-only tools (new + existing integrations)"]
    T1["query_prometheus"]:::tool
    T2["search_logs (ELK)"]:::tool
    T3["get_audit_events"]:::tool
    T4["get_disk_trend"]:::tool
    T5["get_vm_facts"]:::tool
    T6["list_inventory"]:::tool
  end

  subgraph GUARD["Guardrails — applied every hop"]
    RED["ContextRedactor"]:::guard
    BUD["ContextBudgeter / size caps"]:::guard
    AUD[("AIDecisionStore<br/>investigation_agent_step ·<br/>operator_assistant · dashboard_chat_turn")]:::store
  end

  OUT["AssistantResponse<br/>summary + Findings(evidence) + recommendations"]:::layerA

  CLI --> DECIDE
  WEBUI --> CHATSTORE --> HIST --> DECIDE

  DECIDE -->|enabled & supported| AGENTIC
  DECIDE -->|disabled / unsupported / LLM down| DETERM
  AGENTIC --> LOOP
  LOOP <-->|tool_calls| TOOLS
  LOOP -->|every tool result| RED --> BUD
  LOOP -->|budget exhausted, no answer| DETERM
  LOOP -.->|per hop| AUD
  DETERM -.->|one call| AUD

  AGENTIC --> OUT
  DETERM --> OUT
  OUT -->|printed| CLI
  OUT -->|appended + rendered| CHATSTORE

  subgraph NEVER["Layer B — structurally unreachable from here"]
    LB["execution / SSH / approval / rollback"]:::layerB
  end
  TOOLS -.->|read-only only — no code path exists| NEVER
```

## Reading the diagram

- **Blue (Layer A)** — the engine and its two paths: the new agentic loop, or the existing
  deterministic fallback (`OperatorAssistant.investigate()`, untouched). Both produce the same
  `AssistantResponse` contract, so the chat and the CLI can call either interchangeably.
- **Amber (tools)** — the six read-only tools the loop can call; each result is redacted + capped
  (purple) before it re-enters the model. Tool results are untrusted input.
- **Gray (stores)** — `ChatStore` (new, Plan B) and `AIDecisionStore` (existing, gains a new
  `decision_type="investigation_agent_step"` value alongside `operator_assistant` and the new
  `dashboard_chat_turn`).
- **Green (Layer B)** — drawn only to show it is *not connected* to anything above it. No tool, no
  loop iteration, no chat handler has a code path into execution/SSH/approval/rollback — that is
  the entire safety argument for this feature, made visual. Confirmed by `tests/web/test_import_isolation.py`
  (blocks `errander.execution`, `errander.agent.subgraphs`, `errander.agent.graph`,
  `errander.agent.vm_graph`) and the new `tests/agent/test_investigation_agent_isolation.py`.
- Both entry points (CLI `--ask --agentic` and the new `/ui/chat`) converge on the same decision
  gate — the chat is a thin surface over the Plan A engine, not a second brain, exactly as both
  source design docs (`tasks/investigation-agent-implementation-plan.md`,
  `tasks/dashboard-chat-implementation-plan.md`) require.
- **Scope note:** this diagram covers Plan A (phases 1–3) and Plan B phase 1 only (read-only
  chat). Streaming (SSE) and action handoff to the approval flow are Plan B phases 2–3, deferred —
  see the two source plans (`tasks/investigation-agent-implementation-plan.md`,
  `tasks/dashboard-chat-implementation-plan.md`) and `tasks/todo.md` for full scope and status.
