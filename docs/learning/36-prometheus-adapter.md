# 36 -- Prometheus HTTP Adapter (Phase C)

## What was built and why

Phases B and D built probes and an Operator Assistant that read from stored signal data (audit trail, disk history, baselines). Neither had access to live time-series metrics. An operator asking `--ask "Is CPU pressure contributing to the disk I/O spike?"` got no CPU or memory data.

Phase C adds a thin Prometheus adapter that enriches both pipelines with live node_exporter metrics. It's optional and additive -- set `ERRANDER_PROMETHEUS_BASE_URL` to enable it, leave it empty to disable (the default). Neither probe nor `--ask` breaks without it.

---

## Architecture

```
Settings.prometheus_base_url  ("")  →  disabled
                              ("http://prometheus:9090")  →  enabled

PrometheusClient
  fetch_vm_metrics(host: str) → list[str]    # e.g. ["CPU (5m): 72.3%", "Memory: 84.1%", "Load(5m): 2.40"]
  _query_instant(promql: str) → float | None
  _get_session()               → aiohttp.ClientSession  (lazy, reused, mirrors slack.py)
  close()                      → None

Integration points:
  probe_vm()             errander/agent/probe.py           → ProbeVMResult.prometheus_metrics
  run_env_probe()        errander/agent/probe.py           → passes client through
  render_digest_report() errander/observability/reporting.py → :bar_chart: section
  _build_context()       errander/agent/operator_assistant.py → VMSignalSummary.prometheus_metrics
  _format_prompt()       errander/agent/operator_assistant.py → "Prometheus: ..." per VM
  run_env_probe_main()   errander/main.py                  → builds + closes client
  run_ask_query()        errander/main.py                  → builds + closes client
  scheduler _run_probe   errander/main.py                  → builds + closes client per run
```

---

## What metrics are queried

Three standard node_exporter metrics, matched by `instance=~"HOST:.*"`:

| Metric | PromQL (abbreviated) | Format |
|--------|---------------------|--------|
| CPU usage | `100 - avg(rate(node_cpu_seconds_total{mode="idle",instance=~...}[5m])) * 100` | `CPU (5m): 72.3%` |
| Memory usage | `(1 - MemAvailable / MemTotal) * 100` | `Memory: 84.1%` |
| Load average 5m | `node_load5{instance=~...}` | `Load(5m): 2.40` |

The `instance=~"HOST:.*"` regex matches any port (9100, 9101, etc.), so no per-VM config is needed. The VM's `host` field from inventory is used as-is.

---

## The "never blocking" invariant

Every failure path in `_query_instant()` returns `None`, never raises:
- HTTP status != 200 → `None`
- Empty result set → `None`
- JSON parse error → `None`
- Connection refused / timeout → `None` (5s total timeout)

`fetch_vm_metrics()` skips any metric whose query returned `None`. The caller always gets a `list[str]`, possibly empty. The probe and `--ask` continue normally whether Prometheus is available or not.

---

## Code walkthrough

### `PrometheusClient.fetch_vm_metrics(host)`

```python
# For each of 3 (promql, format_string) pairs:
value = await self._query_instant(promql_with_host_label)
if value is not None:
    results.append(fmt.format(value))
return results
```

### `PrometheusClient._query_instant(promql)`

```python
async with session.get("/api/v1/query", params={"query": promql}, timeout=5s) as resp:
    if resp.status != 200:
        return None
    raw = await resp.json()
    # safe isinstance narrowing: raw → data_block → rows → first → value_pair
    return float(str(value_pair[1]))
```

Key: each layer of the JSON structure is narrowed with `isinstance` before access. Mypy requires this because `resp.json()` returns `object`.

### Injection into `probe_vm()`

After all signal nodes and before `return ProbeVMResult(...)`:
```python
prom_metrics: list[str] = []
if prometheus_client is not None:
    prom_metrics = await prometheus_client.fetch_vm_metrics(hostname)
return ProbeVMResult(..., prometheus_metrics=prom_metrics)
```

`hostname` (not `vm_id`) is used — it's the IP/hostname that Prometheus knows about.

### `render_digest_report()` section

Appended after failed-logins, before the healthy-fleet sentinel:
```
*:bar_chart: Prometheus Metrics (2 VM(s))*
  `vm-prod-01` (10.0.0.1): CPU (5m): 72.3%, Memory: 84.1%, Load(5m): 2.40
  `vm-prod-02` (10.0.0.2): CPU (5m): 45.1%, Memory: 62.3%, Load(5m): 1.10
```

Only shown when at least one VM has non-empty `prometheus_metrics`.

### `_format_prompt()` line

```
  Prometheus: CPU (5m): 72.3%, Memory: 84.1%, Load(5m): 2.40
```

Only shown for VMs that have Prometheus data. Empty list → line omitted.

### main.py wiring pattern

All three call sites (`run_env_probe_main`, `run_ask_query`, `_run_probe` closure) follow the same pattern:
```python
from errander.integrations.prometheus import PrometheusClient as _PrometheusClient
prom = _PrometheusClient(settings.prometheus_base_url) if settings.prometheus_base_url else None
try:
    result = await run_something(..., prometheus_client=prom)
finally:
    if prom is not None:
        await prom.close()
```

The `finally` guarantees the aiohttp session is closed even if the probe raises.

---

## Gotchas

1. **`instance` label format varies by deployment.** Default node_exporter uses `hostname:9100`. Some setups use IP:port, FQDN:port, or custom labels. The `instance=~"HOST:.*"` regex handles port variation but requires the host portion to match inventory. If Prometheus returns empty for all VMs, check `prometheus.internal:9090/api/v1/targets` to see actual instance labels.

2. **`resp.json()` returns `object`, not `dict`.** aiohttp's type stub types the return as `Any` but mypy sees it as `object`. The safe narrowing chain (`isinstance(raw, dict)` → `isinstance(data_block, dict)` → `isinstance(rows, list)`) is required to satisfy mypy without `type: ignore`.

3. **`float(str(value_pair[1]))` not `float(value_pair[1])`.** Prometheus returns numeric values as strings in the JSON (`"value": [timestamp, "72.3"]`). `float("72.3")` works; `float(some_object)` could raise. The `str()` call makes the conversion explicit and mypy-safe.

4. **5-second timeout is intentional.** A Prometheus query that takes more than 5 seconds indicates a serious problem. Better to skip metrics and let the probe complete than to stall the whole probe run waiting for a slow Prometheus.

5. **`close()` in a `finally` block.** The aiohttp session must be closed or the event loop will emit a ResourceWarning. The `try/finally` pattern in main.py ensures this even when the probe raises.

---

## Quiz

1. Why does `fetch_vm_metrics` return `[]` rather than raising when Prometheus is unreachable?
2. What does `instance=~"10.0.0.1:.*"` match in Prometheus label terms?
3. Why is `float(str(value_pair[1]))` used instead of `float(value_pair[1])`?
4. In which three places in `main.py` is `PrometheusClient` constructed? Why does each need `try/finally`?
5. What happens to the digest report section when all VMs have empty `prometheus_metrics`?
6. If an operator's Prometheus uses FQDN labels (`myhost.example.com:9100`) but inventory uses IP addresses, what happens and how would you fix it?
