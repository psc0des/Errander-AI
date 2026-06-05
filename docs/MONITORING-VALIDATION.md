# Monitoring Validation — Built-in vs Prometheus + Grafana

## Why this document exists

Errander ships a built-in `/ui/monitoring` dashboard (audit DB + in-process Prometheus registry). The external Prometheus + Grafana stack remains installed in parallel so both can be compared on live data before deciding whether Prometheus + Grafana are still needed.

This doc records what to compare, how to compare it, known differences, and the criteria for declaring the built-in solution sufficient.

---

## What to compare

### 1. Action counts and success rate

| Built-in (`/ui/monitoring`) | Prometheus / Grafana |
|---|---|
| Stat cards — Total Actions, Success Rate (30d, from audit DB) | `errander_actions_total` counter — sum over time window |

**How to verify:** the built-in 30-day total should be in the same ballpark as Grafana's counter (exact match unlikely — Grafana shows since last Prometheus scrape start; built-in shows exactly 30 days from audit DB).

**Direct DB check:**
```bash
sqlite3 errander.sqlite \
  "SELECT event_type, COUNT(*) FROM audit_events
   WHERE event_type IN ('action_completed','action_failed')
   AND timestamp >= datetime('now','-30 days')
   GROUP BY event_type"
```

---

### 2. Approval funnel

**Built-in only** — Grafana has no approval panel. The built-in page is the only visualization of this data. Cross-check directly against the DB:

```bash
sqlite3 errander.sqlite \
  "SELECT event_type, COUNT(*) FROM audit_events
   WHERE event_type LIKE 'approval%'
   AND timestamp >= datetime('now','-30 days')
   GROUP BY event_type"
```

---

### 3. Duration averages

| Built-in | Prometheus / Grafana |
|---|---|
| `sum / count` from in-process histograms — **resets on agent restart** | Time-series from Prometheus scrapes — **survives restarts** |

**Known difference:** if the agent restarted during the comparison window, the built-in will show a shorter average (only since last restart). Grafana will show the full history. This is the biggest known gap.

**How to check for restarts:**
```bash
sqlite3 errander.sqlite \
  "SELECT COUNT(*) FROM audit_events WHERE event_type='agent_starts'"
# or look at the agent_starts counter on /ui/monitoring itself
```

If agent_starts > 1 during your window, the duration averages will diverge.

---

### 4. Safety signals

**Built-in only** — Grafana has no safety signal panels. No cross-check possible from Prometheus. Verify via DB:

```bash
sqlite3 errander.sqlite \
  "SELECT event_type, COUNT(*) FROM audit_events
   WHERE event_type IN (
     'drift_detected','drift_kind_changed',
     'sudo_preflight_failed','target_preflight_failed','disk_gate_blocked',
     'reboot_required_detected','service_health_regression','failed_ssh_logins_observed'
   )
   AND timestamp >= datetime('now','-30 days')
   GROUP BY event_type"
```

---

### 5. LLM health

| Built-in | Prometheus / Grafana |
|---|---|
| LLM requests by outcome — in-process counter (resets on restart) | `errander_llm_requests_total` — Prometheus time-series (survives restarts) |

Same restart caveat as durations.

---

## Decision criteria

**The built-in page is sufficient (Prometheus + Grafana can be made optional) if:**

- [ ] Action counts match to within reasonable margin (< 5% difference on a stable deployment with no restarts)
- [ ] Approval funnel shows correct counts (verified against direct DB query)
- [ ] Safety signals show correct counts (verified against direct DB query)
- [ ] Duration averages are plausible (order-of-magnitude correct vs Grafana, acknowledging restart window difference)
- [ ] LLM health outcomes are consistent with `/metrics` raw endpoint

**Prometheus + Grafana remain valuable (keep scripts) if:**

- Time-series history matters operationally (e.g. "show me the last 2 weeks of batch durations trending up")
- Alerting rules are needed (Prometheus alertmanager) — the built-in page has no alerting
- Multi-instance federation is needed (multiple controllers)

---

## Known limitations of the built-in page vs Prometheus + Grafana

| Limitation | Impact | Workaround |
|---|---|---|
| Duration histograms reset on agent restart | Averages reflect only current uptime window | Check `agent_starts` counter; restart is visible |
| Fixed time windows (7d / 30d) — no time-axis charts | Can't see "trend over last 6 hours" | Use `/ui/batches` for recent history |
| No alerting | Can't page on high error rate | Prometheus alertmanager if alerting needed |
| No multi-instance support | Single controller only | Out of scope for v1 anyway |

---

## How to run the comparison

1. Let the agent run at least one full batch with both stacks active
2. Open `/ui/monitoring` and screenshot all sections
3. Open Grafana (`http://localhost:3000`, SSH tunnel) and screenshot the Fleet Operations dashboard
4. Run the direct DB queries above to get ground-truth counts
5. Compare the numbers using the table in §2 above
6. Record results in the session notes

---

## Status

- [ ] Comparison not yet run — both stacks running in parallel as of 2026-06-05
- [ ] Results TBD
