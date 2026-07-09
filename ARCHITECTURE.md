# Errander-AI — System Architecture

> Supervised agentic AI · two-layer safety model · AI investigates and recommends, humans approve, deterministic code acts.
> As-built as of **2026-07-10** (post-R3 process split · detect-and-propose Phases 1–5 shipped).
> Renders inline on GitHub. The editable draw.io version is [`docs/diagrams/errander-system-architecture.drawio`](docs/diagrams/errander-system-architecture.drawio) (same as-built state, swim-lane layout).

```mermaid
flowchart TB
  classDef layerA fill:#DAE8FC,stroke:#6C8EBF,color:#15314B;
  classDef layerB fill:#D5E8D4,stroke:#82B366,color:#1E3A1E;
  classDef appr fill:#FFF2CC,stroke:#D6B656,color:#5B4A12;
  classDef ext fill:#FFE6CC,stroke:#D79B00,color:#7A4F00;
  classDef store fill:#F5F5F5,stroke:#666666,color:#222222;
  classDef planned fill:#E1D5E7,stroke:#9673A6,color:#4B2E63,stroke-dasharray:5 4;
  classDef infra fill:#E6F0FB,stroke:#3D6CB0,color:#15314B;

  subgraph PUB["PUBLIC INTERNET — outbound HTTPS only, no inbound to agent"]
    OP["Operator<br/>laptop / mobile"]:::ext
    SLACK["Slack API<br/>#errander-approvals<br/>notify + link only"]:::ext
  end

  subgraph VPN["VPN — PRIVATE NETWORK (no public IPs)"]
    LLM["LLM ENDPOINT — Layer A only<br/>cloud API · or self-hosted vLLM (GPU VM)"]:::infra

    subgraph CTRL["Controller VM — two OS-separated processes (R3) · shared PostgreSQL is the ONLY link between them"]
      subgraph AGENTP["AGENT PROCESS — OS user errander-agent · holds SSH keys · :9090 = /metrics + /health only"]
        PROBE["Daily probe + proposal detector<br/>deterministic, no LLM<br/>disk / drift / login signals → evidenced proposals"]:::layerB
        INV["INVESTIGATION AGENT — shipped, opt-in (default OFF)<br/>bounded ReAct loop (tool-call + wall-clock budget)<br/>read-only tools: audit · disk trend · vm facts · inventory · Prometheus · ELK"]:::layerA
        LA["LAYER A — Brain (LLM)<br/>advisory planning note · post-batch report · --ask assistant<br/>recommends + proposes, never executes"]:::layerA
        LB["LAYER B — Hands (deterministic, NO LLM)<br/>deterministic planner · patching · disk_cleanup · log_rotation<br/>docker_hygiene · backup_verify · service_restart<br/>validate → approve → execute → verify → rollback → audit"]:::layerB
      end

      subgraph WEBP["WEB PROCESS — OS user errander-web · no SSH keys · :9091 (nginx Mode 2 optional)"]
        WEB["Web UI — dashboards · users/groups RBAC<br/>TOTP for admin in public mode"]:::infra
        AG{"HUMAN APPROVAL GATE<br/>authenticated Web UI only · named user + group · durable DB row<br/>batch approvals + /ui/proposals agent-proposal queue"}:::appr
      end

      DB[("Audit DB PostgreSQL<br/>audit_events · ai_decisions · plan_snapshots<br/>approval_requests · agent_proposals")]:::store
    end

    subgraph TGT["Target VMs — Linux, private (SSH key-based)"]
      VM1["web-01<br/>node_exporter :9100"]
      VM2["db-01<br/>node_exporter :9100"]
      VM3["app-01<br/>node_exporter :9100"]
    end

    subgraph OBS["Observability — bring-your-own · read-only · Layer A reads, Layer B never depends"]
      MPROM["Monitoring Prometheus<br/>BYO external VM · scrapes Errander /metrics<br/>watches the watcher"]:::infra
      GRAF["Grafana<br/>dashboards"]:::infra
      FPROM["Fleet Prometheus<br/>target VM metrics<br/>BYO · optional"]:::infra
      ELK["ELK / Loki<br/>target + agent logs<br/>BYO · optional"]:::infra
      LS["LangSmith tracing + eval harness<br/>Layer A traces · golden scenarios<br/>shipped · opt-in via env vars · egress only when enabled"]:::infra
    end
  end

  %% --- batch path (deterministic plan, human gate, deterministic execution) ---
  LA -.->|"advisory ai_note — inside the hashed plan, informational only"| LB
  LB -->|"deterministic plan → approval_requests row"| DB
  DB -->|"pending approvals + proposals"| AG
  AG -->|"atomic decision by named operator"| DB
  DB -->|"agent polls decision"| LB
  LB ==>|"SSH key-based: ONLY path that changes a VM"| TGT

  %% --- detect-and-propose (agentic origination, HITL execution) ---
  PROBE -->|"files agent_proposals (dedup + suppression)"| DB
  PROBE -.->|"triggers on findings (opt-in)"| INV
  INV -->|"evidenced proposals (validated action set)"| DB
  DB -.->|"memory loop: repeated rejections suppress re-filing"| PROBE

  %% --- Layer A reads ---
  LA -->|"LLM completions"| LLM
  INV -->|"LLM tool-calling"| LLM
  PROBE -.->|"reads metrics + logs, opt-in"| OBS
  INV -.->|"read-only tools"| OBS
  LA -->|"ai_decisions + per-hop investigation steps"| DB

  %% --- operators + notifications ---
  OP -->|"approve / monitor"| WEB
  OP -->|"notified, follows link"| SLACK
  AGENTP -->|"notify + link · outbound HTTPS only"| SLACK

  %% --- observability ---
  MPROM -.->|"scrapes"| AGENTP
  GRAF -->|"queries"| FPROM
  FPROM -.->|"scrapes"| TGT
```

## Reading the diagram

- **Layer A (blue)** thinks, recommends, and — since detect-and-propose (2026-07-07) — *proposes work*: the advisory planning note, the post-batch report, the `--ask` assistant, and the **Investigation Agent** (a bounded ReAct loop over read-only tools, opt-in via `investigation_agent_enabled`, per-hop redaction + `investigation_agent_step` audit rows). It never touches a VM, and it is import-isolated from Layer B (test-enforced — the agent never imports the approval or proposal stores; its caller files proposals).
- **Detect-and-propose (shipped Phases 1–4):** the daily probe's deterministic detector turns disk/drift/login signals into evidenced `agent_proposals`; probe findings can (opt-in, `investigation_trigger_enabled`) trigger the Investigation Agent for deeper evidence. Proposals land in the `/ui/proposals` queue with an AGENT-ORIGINATED badge. **Approving a proposal originates work — it is not execution authorization**: the approved action runs through the normal Layer B path with every gate intact (maintenance window, VM lock, drift checks, exact-object approval). A memory loop suppresses re-proposing work that operators repeatedly reject (default: 2 rejections within 14 days).
- **Human approval gate (amber)** sits between thinking and acting — mandatory for every live change. Each request is a durable row in `approval_requests`: decisions are atomic (exactly one winner) and survive an agent restart (a reconciler job recovers pending ones). The **only** decision surface is the authenticated Web UI with users/groups RBAC — every decision records a named user + group; Slack notifies and links but cannot decide.
- **Layer B (green)** is deterministic Python. Since R1 the batch plan's membership and ordering are 100% deterministic — the LLM can only attach the clearly-labeled advisory note, never change what executes. The thick **SSH edge is the only path that changes a target VM**.
- **R3 process split:** the agent process (`errander-agent`, holds SSH keys, `:9090` metrics-only) and the web process (`errander-web`, no SSH keys, `:9091`, RBAC + TOTP) are separate OS users; the shared PostgreSQL database is the **only** link between them, with table-level role grants (the web role cannot write audit tables). The Layer A / Layer B boundary is an OS-enforced privilege boundary, not just a code convention.
- **Prometheus, twice:** a BYO Monitoring Prometheus on an external VM scrapes Errander's own `/metrics` (who watches the watcher); a separate Fleet Prometheus scrapes target node_exporters `:9100` for Layer A to read when investigating fleet health. Neither runs on the controller.
- **Nothing on this diagram is aspirational** — every component is shipped. LangSmith tracing + the golden-scenario eval harness landed with detect-and-propose Phase 5 (opt-in via the standard `LANGSMITH_*`/`LANGCHAIN_*` env vars; `--eval-golden-scenarios` runs offline). The Dashboard Chat / Operator Chat Interface shown in older revisions was removed from core scope on 2026-06-23 and lives on as a separate future project.
- Everything inside **VPN** is private; the only outbound path is HTTPS to Slack (and optionally LangSmith).
- **Observability lane** is bring-your-own and read-only — the daily probe and Layer A may read these sources; Layer B never depends on them.
