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
    SLACK["Slack API<br/>#errander-approvals ✅/❌"]:::ext
    LS["LangSmith / equivalent<br/>Layer A traces<br/>planned · optional · egress"]:::planned
  end

  subgraph VPN["VPN — PRIVATE NETWORK (no public IPs)"]
    LLM["LLM ENDPOINT — Layer A only<br/>cloud API · or self-hosted vLLM (GPU VM)"]:::infra

    subgraph CTRL["Controller VM (Agent) — private"]
      WEB["Web UI + /metrics :9090"]:::infra
      LA["LAYER A — Brain (LLM)<br/>prioritize · analyze · report · --ask<br/>recommends, never executes"]:::layerA
      AG{"HUMAN APPROVAL GATE<br/>Slack / Web UI<br/>every live change"}:::appr
      LB["LAYER B — Hands (deterministic, NO LLM)<br/>patching · disk_cleanup · log_rotation<br/>docker_hygiene · backup_verify · service_restart<br/>validate → execute → verify → rollback → audit"]:::layerB
      DB[("Audit DB SQLite<br/>audit_events · ai_decisions")]:::store
      PROM["Prometheus :9091<br/>scrapes agent /metrics<br/>monitors Errander itself"]:::infra
    end

    subgraph TGT["Target VMs — Linux, private (SSH key-based)"]
      VM1["web-01<br/>node_exporter :9100"]
      VM2["db-01<br/>node_exporter :9100"]
      VM3["app-01<br/>node_exporter :9100"]
    end

    subgraph OBS["Observability — bring-your-own"]
      GRAF["Grafana"]:::infra
      ELK["ELK / Loki"]:::infra
    end
  end

  subgraph RM["ROADMAP — planned, build A then B"]
    PA["Plan A — Investigation Agent<br/>agentic read-only query tools"]:::planned
    PB["Plan B — Dashboard Chat /ui/chat<br/>action → approval (never executes)"]:::planned
  end

  %% --- core two-layer safety flow ---
  LA -->|proposes plan| AG
  AG -->|approved| LB
  LB ==>|SSH key-based: ONLY path that changes a VM| TGT

  %% --- Layer A reads ---
  LA -->|LLM completions| LLM
  LA -.->|reads metrics + logs, opt-in| TGT

  %% --- audit ---
  LA -->|ai_decisions| DB
  LB -->|audit_events| DB

  %% --- operators / slack ---
  OP -->|approve / monitor| WEB
  OP -->|react ✅/❌| SLACK
  WEB <-->|outbound HTTPS| SLACK
  WEB -.->|decision| AG

  %% --- observability ---
  PROM -->|scrape| WEB
  GRAF -->|queries| PROM
  LA -.->|traces, egress| LS

  %% --- planned wirings ---
  PA -.->|upgrades| LA
  PB -.->|adds /ui/chat| WEB
```

## Reading the diagram

- **Layer A (blue)** thinks and recommends — never touches a VM.
- **Human approval gate (amber)** sits between thinking and acting — mandatory for every live change.
- **Layer B (green)** is deterministic Python; the thick **SSH edge is the only path that changes a target VM**.
- **Two Prometheus directions:** the controller-node Prometheus `:9091` *scrapes the agent* (monitors Errander); Layer A separately *reads target metrics/logs* (opt-in) to inform recommendations.
- **Dashed purple** = planned (LangSmith tracing, Plan A investigation agent, Plan B dashboard chat).
- Everything inside **VPN** is private; the only outbound path is HTTPS to Slack (and optionally LangSmith).
