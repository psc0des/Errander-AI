# Errander-AI — System Architecture

> Supervised agentic AI · two-layer safety model · the LLM recommends, humans approve, deterministic code acts.
> Renders inline on GitHub. The editable draw.io version is `errander-system-architecture.drawio` (same diagram).

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

    subgraph CTRL["Controller VM (Agent) — private"]
      WEB["Web UI + /metrics :9090"]:::infra
      CHAT["Dashboard Chat /ui/chat<br/>· planned ·"]:::planned
      LA["LAYER A — Brain (LLM)<br/>prioritize · analyze · report · --ask<br/>recommends, never executes"]:::layerA
      INV["Investigation Agent<br/>· planned ·<br/>agentic read-only · Prometheus · ELK · audit"]:::planned
      CHATIF["Operator Chat Interface<br/>· planned ·<br/>answers /ui/chat"]:::planned
      AG{"HUMAN APPROVAL GATE<br/>authenticated Web UI (RBAC) · durable (DB row)<br/>Slack notifies + links · every live change"}:::appr
      LB["LAYER B — Hands (deterministic, NO LLM)<br/>patching · disk_cleanup · log_rotation<br/>docker_hygiene · backup_verify · service_restart<br/>validate → execute → verify → rollback → audit"]:::layerB
      DB[("Audit DB PostgreSQL<br/>audit_events · ai_decisions<br/>plan_snapshots · approval_requests")]:::store
      PROM["Controller Prometheus :9091<br/>scrapes agent /metrics<br/>monitors Errander itself"]:::infra
    end

    subgraph TGT["Target VMs — Linux, private (SSH key-based)"]
      VM1["web-01<br/>node_exporter :9100"]
      VM2["db-01<br/>node_exporter :9100"]
      VM3["app-01<br/>node_exporter :9100"]
    end

    subgraph OBS["Observability — bring-your-own · read-only · Layer A reads, Layer B never depends"]
      GRAF["Grafana<br/>dashboards"]:::infra
      FPROM["Fleet Prometheus<br/>target VM metrics<br/>BYO · optional"]:::infra
      ELK["ELK / Loki<br/>target + agent logs<br/>BYO · optional"]:::infra
      LS["LangSmith / equivalent<br/>Layer A traces<br/>BYO · optional · egress"]:::planned
    end
  end

  %% --- core two-layer safety flow ---
  LA -->|proposes plan| AG
  AG -->|approved| LB
  LB ==>|SSH key-based: ONLY path that changes a VM| TGT

  %% --- Layer A reads ---
  LA -->|LLM completions| LLM
  LA -.->|reads metrics + logs, opt-in| OBS

  %% --- audit ---
  LA -->|ai_decisions| DB
  LB -->|audit_events| DB
  AG -->|plan_snapshots · approval_requests| DB

  %% --- operators ---
  OP -->|approve / monitor| WEB
  OP -->|notified, follows link| SLACK
  WEB <-->|outbound HTTPS| SLACK
  WEB -.->|decision| AG

  %% --- observability ---
  PROM -->|scrape| WEB
  GRAF -->|queries| FPROM
  FPROM -.->|scrapes| TGT

  %% --- planned wirings ---
  CHAT -.->|adds /ui/chat| WEB
  INV -.->|upgrades| LA
  CHATIF -.->|serves| CHAT
```

## Reading the diagram

- **Layer A (blue)** thinks and recommends — never touches a VM.
- **Human approval gate (amber)** sits between thinking and acting — mandatory for every live change. Since §8d Step 2 each request is a durable row in `approval_requests`: decisions are atomic (exactly one winner), and a pending approval survives an agent restart (a reconciler job recovers it). Since §8d Step 3 (R2) the **only** decision surface is the authenticated Web UI with users/groups RBAC — every decision records a named user + group; Slack notifies and links but cannot decide.
- **Layer B (green)** is deterministic Python; the thick **SSH edge is the only path that changes a target VM**.
- **Two Prometheus instances:** the Controller Prometheus `:9091` *scrapes the agent* (monitors Errander itself); Fleet Prometheus separately *scrapes target node_exporters* `:9100` (opt-in, for Layer A to read when investigating fleet health).
- **Dashed purple** = planned (Investigation Agent, Dashboard Chat, Operator Chat Interface, LangSmith tracing).
- Everything inside **VPN** is private; the only outbound path is HTTPS to Slack (and optionally LangSmith).
- **Observability lane** is bring-your-own and read-only — Layer A may read these sources; Layer B never depends on them.
