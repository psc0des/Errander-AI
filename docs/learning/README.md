# Errander-AI — Learning Documentation

Index of all learning docs created during development. Each doc explains what was built, how it works, key concepts, gotchas, and self-test questions.

## Index

| # | Topic | Phase | File |
|---|-------|-------|------|
| 01 | [Project Scaffold](01-project-scaffold.md) | 1.1 | `01-project-scaffold.md` |
| 02 | [Settings Loader](02-settings-loader.md) | 1.2 | `02-settings-loader.md` |
| 03 | [Core Infrastructure](03-core-infrastructure.md) | 1.2 | `03-core-infrastructure.md` |
| 04 | [Disk Cleanup Sub-Graph](04-disk-cleanup-subgraph.md) | 1.3 | `04-disk-cleanup-subgraph.md` |
| 05 | [Per-VM Graph](05-vm-graph.md) | 1.4 | `05-vm-graph.md` |
| 06 | [Batch Orchestrator](06-batch-orchestrator.md) | 1.5 | `06-batch-orchestrator.md` |
| 07 | [LLM Client](07-llm-client.md) | 1.6 | `07-llm-client.md` |
| 08 | [Slack Approval](08-slack-approval.md) | 1.6 | `08-slack-approval.md` |
| 09 | [Prometheus Metrics](09-metrics.md) | 1.6 | `09-metrics.md` |
| 10 | [Scheduling & Windows](10-scheduling.md) | 1.7 | `10-scheduling.md` |
| 11 | [SQLite Audit Integration](11-sqlite-audit.md) | 1.7 | `11-sqlite-audit.md` |
| 12 | [Web UI](12-web-ui.md) | 1.7 | `12-web-ui.md` |
| 13 | [vLLM Setup](13-vllm-setup.md) | 1.7 | `13-vllm-setup.md` |
| 14 | [Dual-Channel Approval](14-dual-channel-approval.md) | 1.7 | `14-dual-channel-approval.md` |
| 15 | [Log Rotation Sub-Graph](15-log-rotation-subgraph.md) | 2 | `15-log-rotation-subgraph.md` |
| 16 | [Docker Prune Sub-Graph](16-docker-prune-subgraph.md) | 2 | `16-docker-prune-subgraph.md` |
| 17 | [Patching Sub-Graph](17-patching-subgraph.md) | 2 | `17-patching-subgraph.md` |
| 18 | [Backup Verify Sub-Graph](18-backup-verify-subgraph.md) | 2 | `18-backup-verify-subgraph.md` |
| 19 | [Phase 3 Hardening: Rolling Updates, Canary, Drift](19-phase3-hardening.md) | 3 | `19-phase3-hardening.md` |
| 20 | [Phase 3 Edge-Case Hardening](20-phase3-edge-case-hardening.md) | 3 | `20-phase3-edge-case-hardening.md` |
| 21 | [Load Testing & Playwright Approvals](21-load-testing.md) | 3 | `21-load-testing.md` |
| 22 | [UI Settings & Inventory Management](22-ui-settings-and-inventory.md) | 4 | `22-ui-settings-and-inventory.md` |
| 23 | [Playwright UI Tests](23-playwright-ui-tests.md) | 4 | `23-playwright-ui-tests.md` |
| 24 | [Deferred Execution: Window-Gated Approval](24-deferred-execution.md) | 4 | `24-deferred-execution.md` |
| 25 | [SRE Groundwork: Migrations, State, Baselines, Reports](25-sre-groundwork.md) | SRE-G | `25-sre-groundwork.md` |
| 26 | [Package Lock Detection (Preflight)](26-pkg-lock-detection.md) | SRE-1.1 | `26-pkg-lock-detection.md` |
| 27 | [Reboot-Required Detection](27-reboot-detection.md) | SRE-1.2 | `27-reboot-detection.md` |
| 28 | [Service Health Regression Detection](28-service-health-checks.md) | SRE-1.3 | `28-service-health-checks.md` |
| 29 | [Disk Growth Trend Detection](29-disk-growth-trend.md) | SRE-1.4 | `29-disk-growth-trend.md` |
| 30 | [Configuration Drift Detection + Failed SSH Logins](30-drift-detection.md) | SRE-2 | `30-drift-detection.md` |
| 31 | [SRE Signal Aggregation + BatchReport Rendering](31-sre-signal-aggregation.md) | SRE-2 | `31-sre-signal-aggregation.md` |
| 32 | [SRE Production Wiring: Making Signal Stores Active](32-sre-production-wiring.md) | SRE-audit | `32-sre-production-wiring.md` |
| 33 | [sudo Privilege Model + Docker Wrapper Mode + check-targets CLI](33-sudo-privilege-model.md) | Phase A | `33-sudo-privilege-model.md` |
| 34 | [Proactive Signals: Daily Probe, DigestReport, render_digest_report](34-proactive-signals.md) | Phase B | `34-proactive-signals.md` |
| 35 | [Operator Assistant: Layer A Investigation + --ask CLI](35-operator-assistant.md) | Phase D | `35-operator-assistant.md` |
| 36 | [Prometheus HTTP Adapter: node_exporter metrics in probe + --ask](36-prometheus-adapter.md) | Phase C | `36-prometheus-adapter.md` |
| 37 | [Immutable Signed Plan Artifact: assessment at plan time, exact packages in hash](37-immutable-plan-artifact.md) | P0-1 | `37-immutable-plan-artifact.md` |
| 38 | [ELK journalctl Enrichment](38-elk-journalctl-enrichment.md) | Phase C | `38-elk-journalctl-enrichment.md` |
| 39 | [LangGraph Signal Integration](39-langgraph-signal-integration.md) | Phase C | `39-langgraph-signal-integration.md` |
| 40 | [Service Restart Module](40-service-restart-module.md) | v1 actions | `40-service-restart-module.md` |
| 41 | [Durability Measurement](41-durability-measurement.md) | Phase A1 | `41-durability-measurement.md` |
| 42 | [D1 Prompt Capture](42-d1-prompt-capture.md) | AI Trust | `42-d1-prompt-capture.md` |
| 43 | [vm-facts CLI](43-vm-facts-cli.md) · [AI Decision Explainability](43-ai-decision-explainability.md) | B3 / AI Trust | two docs share the number |
| 44 | [Provider Layer](44-provider-layer.md) · [Context Budget & Redaction](44-context-budget-redaction.md) | Web / AI Trust | two docs share the number |
| 45 | [docker_hygiene Session 3 Cutover](45-docker-hygiene-session3-cutover.md) · [Replay Evals](45-replay-evals.md) | v1.1 / AI Trust | two docs share the number |
| 46 | [docker_hygiene v1.2 Scope](46-docker-hygiene-v1.2-scope.md) · [Operational Memory Confidence](46-operational-memory-confidence.md) | v1.2 / AI Trust | two docs share the number |
| 47 | [docker_hygiene v1.5 Scope](47-docker-hygiene-v1.5-scope.md) · [AI Source Citation](47-ai-source-citation.md) | v1.5 / AI Trust | two docs share the number |
| 48 | [AI SRE Gap Fix](48-ai-sre-gap-fix.md) · [AI Prefix Caching](48-ai-prefix-caching.md) | SRE / AI Trust | two docs share the number |
| 49 | [SRE Residual Fixes](49-sre-residual-fixes.md) · [SRE Trust-Gap Fixes](49-sre-trust-gap-fixes.md) | SRE | two docs share the number |
| 50 | [Per-Item Approval UI + Decision Reasoning](50-per-item-approval.md) | UI | `50-per-item-approval.md` |
| 51 | [Per-Target Actions](51-per-target-actions.md) | config | `51-per-target-actions.md` |
| 52 | [Configure Wizard](52-configure-wizard.md) | setup | `52-configure-wizard.md` |
| 53 | [Wizard Input Validation](53-wizard-input-validation.md) | setup | `53-wizard-input-validation.md` |
| 54 | [PostgreSQL Dual-Backend (superseded by 55)](54-postgres-dual-backend.md) | §8d-1 | `54-postgres-dual-backend.md` |
| 55 | [PostgreSQL-Only Migration](55-postgresql-only.md) | §8d-1 | `55-postgresql-only.md` |
| 56 | [Durable Approval Requests Store](56-approval-requests-store.md) | §8d-2 | `56-approval-requests-store.md` |
| 57 | [Web-Only Approval with Users/Groups RBAC](57-web-only-approval-rbac.md) | §8d-3 | `57-web-only-approval-rbac.md` |
| 58 | [Advisory-LLM Batch Planning](58-advisory-planning-note.md) | §8d-5 | `58-advisory-planning-note.md` |
| 59 | [Docker + Compose + PostgreSQL Bootstrap](59-docker-postgres-bootstrap.md) | infra | `59-docker-postgres-bootstrap.md` |
| 60 | [Detect-and-Propose, Phase 1: the proposal bridge](60-detect-and-propose-phase1.md) | fable-plan P1 | `60-detect-and-propose-phase1.md` |
| 61 | [Detect-and-Propose, Phase 2: the agentic investigation engine](61-investigation-agent-phase2.md) | fable-plan P2 | `61-investigation-agent-phase2.md` |
| 62 | [Detect-and-Propose, Phase 3: probe-triggered investigations](62-investigation-trigger-phase3.md) | fable-plan P3 | `62-investigation-trigger-phase3.md` |
| 63 | [Detect-and-Propose, Phase 4: the memory loop and re-proposal suppression](63-suppression-memory-phase4.md) | fable-plan P4 | `63-suppression-memory-phase4.md` |
