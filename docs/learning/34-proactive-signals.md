# 34 — Proactive Signals (Phase B)

## What was built and why

The existing SRE signal probes (disk growth, drift detection, failed logins) only fired **inside maintenance batches**. An operator had no way to know a VM was filling up or drifting until the next scheduled maintenance window ran.

Phase B added a **standalone daily probe** that runs independently of maintenance, collects the same three signals, and posts a Slack digest. The key design goal: operators see fleet health every day, not just on patch nights.

---

## Architecture decision: no new LangGraph

The three existing probe node functions are plain Python:

```python
async def disk_snapshot_node(state: VMGraphState, *, executor, ...) -> dict[str, Any]: ...
async def drift_baseline_node(state: VMGraphState, *, executor, ...) -> dict[str, Any]: ...
async def failed_logins_node(state: VMGraphState, *, executor, ...) -> dict[str, Any]: ...
```

They take a dict-like state and return a dict of updates. A `StateGraph` is not required to call them — they work anywhere.

Phase B calls them **directly**, chaining the dict updates manually:

```python
probe_state = {"vm_id": vm_id, "hostname": hostname, ...}
disk_result = await disk_snapshot_node(probe_state, executor=executor, settings=sre_settings.disk_growth_trend)
probe_state.update(disk_result)
drift_result = await drift_baseline_node(probe_state, executor=executor, settings=sre_settings.drift)
probe_state.update(drift_result)
...
```

This is the right call: the probe doesn't need state machines, approval gates, locking, or rollback — a simple async loop is correct for read-only probes.

**Rule**: only use LangGraph when you genuinely need state-machine routing. For linear read-only pipelines, a plain async function is simpler and easier to test.

---

## Key design: settings sub-types

The node functions do **isinstance checks** to validate their settings:

```python
async def disk_snapshot_node(..., settings: object) -> dict:
    if not isinstance(settings, DiskGrowthSettings):
        return {"disk_growth_alerts": []}  # graceful no-op
```

This means you must pass the right sub-type, not the full `Settings` object:

```python
# CORRECT
disk_snapshot_node(..., settings=sre_settings.disk_growth_trend)   # DiskGrowthSettings
drift_baseline_node(..., settings=sre_settings.drift)               # DriftSettings
failed_logins_node(..., settings=sre_settings.failed_ssh_logins)    # FailedSSHLoginsSettings

# WRONG — the isinstance check returns {} silently
disk_snapshot_node(..., settings=settings)   # Settings is not DiskGrowthSettings
```

---

## Code walkthrough

### `errander/agent/probe.py`

```
probe_vm()
  - builds a minimal probe_state dict
  - calls disk_snapshot_node → drift_baseline_node → failed_logins_node
  - aggregates results into ProbeVMResult
  - if any node raises → catches exception, returns ProbeVMResult(reachable=False)

run_env_probe()
  - emits DAILY_PROBE_STARTED audit event
  - fans out probe_vm() concurrently via asyncio.gather()
  - builds DigestReport from all ProbeVMResult objects
  - emits DAILY_PROBE_COMPLETE audit event
  - returns DigestReport
```

The `batch_id` field in `probe_state` is set to `""` — probes don't belong to a maintenance batch. This is a sentinel that tells audit queries "this event came from a standalone probe, not a batch run."

`dry_run` is always `False` — probes read real data from VMs. There is no "simulated probe."

### `errander/models/reports.py`

Two new dataclasses:

```python
@dataclass
class ProbeVMResult:
    vm_id: str
    hostname: str
    reachable: bool = True
    disk_growth_alerts: list[dict[str, object]] = field(default_factory=list)
    drift_changes: list[dict[str, object]] = field(default_factory=list)
    failed_login_summary: dict[str, object] | None = None
    error: str | None = None


@dataclass
class DigestReport:
    probe_id: str
    env_name: str
    generated_at: datetime
    vm_results: list[ProbeVMResult] = field(default_factory=list)

    @property
    def all_disk_alerts(self) -> list[dict[str, object]]:
        return [a for r in self.vm_results for a in r.disk_growth_alerts]
    # ... etc
```

The `all_*` properties flatten per-VM signal lists for rendering — avoids the renderer having to know the nested structure.

### `errander/observability/reporting.py`

`render_digest_report(report: DigestReport) -> str` is entirely deterministic — no LLM. It follows the same Slack-markdown style as `render_batch_report()`:

- Empty sections are omitted (healthy fleet stays concise)
- Drift changes grouped by kind (same as batch report)
- Failed logins: zero-count VMs not listed as line items

### Scheduling

`ScheduleSchema` gains a new `signals` field:

```yaml
schedules:
  production:
    maintenance: "0 2 * * tue,thu"
    signals: "0 6 * * *"   # daily probe at 06:00 UTC
```

In `main.py`, if `schedule.signals` is set for an environment, a probe job is registered alongside the maintenance job:

```python
if signals_cron:
    scheduler.add_maintenance_job(_run_probe, signals_cron, job_id=f"probe-{env_name}")
```

The `--probe-now <env>` CLI flag triggers an immediate probe without the scheduler — useful for testing and on-demand checks.

---

## "LLM only summarizes, not computes" design principle

Phase B is entirely deterministic. The LLM is intentionally absent from:
- What to probe (hardcoded: disk, drift, logins)
- What counts as an alert (threshold from `DiskGrowthSettings`, presence of any drift/login)
- What to include in the report (all signals, nothing filtered)

When Phase D (Operator Assistant Layer) is built, an optional LLM summarization step could wrap the digest: "Here's the digest. In 2 sentences: two VMs are accumulating disk pressure on `/data`, and one VM shows unexpected sudoers drift. Recommend reviewing before the Tuesday window." That's Layer A — it investigates and recommends. Layer B (this module) never makes that call.

---

## Gotchas

1. **`batch_id=""` sentinel** — probe audit events use `batch_id=""`. If your audit queries filter by non-empty batch_id, probe events will be silently excluded. This is correct behavior: probe events aren't part of any batch.

2. **`dry_run=False` in probe_state** — the existing nodes skip some operations in dry-run mode (e.g., `drift_baseline_node` skips `compare_and_save`). Probes should always read real data, so `dry_run=False` is correct even though no maintenance actions are taken.

3. **isinstance narrowing removes type: ignore** — after `raw_disk if isinstance(raw_disk, list) else []`, mypy correctly narrows the type. The `# type: ignore` comments that seemed needed were actually unused. This surprised us during implementation.

4. **Deferred imports in run_env_probe_main** — the function uses deferred `from X import Y` inside the function body. Test patches must target the **source module** (`errander.agent.probe.run_env_probe`), not the consumer module (`errander.main.run_env_probe` — this attribute doesn't exist at module level).

---

## Quiz

1. Why does `probe_vm()` set `dry_run: False` in the probe_state?
2. What happens if you pass the full `Settings` object to `disk_snapshot_node` as `settings=`?
3. Why is `asyncio.gather()` used in `run_env_probe()` instead of a sequential loop?
4. Where must `signals` be set to enable the daily probe cron job?
5. What is the `batch_id=""` sentinel and why is it used?
6. Why is `render_digest_report()` deterministic rather than LLM-powered?
