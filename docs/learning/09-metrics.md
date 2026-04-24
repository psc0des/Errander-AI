# 09 — Prometheus Metrics + /health Endpoint

## What Was Built and Why

`errander/observability/metrics.py` defines a Prometheus `CollectorRegistry` with 7 metrics covering every observable dimension of the agent: actions, durations, SSH errors, LLM call outcomes, approval wait times, and VM lock hold times. It also runs a lightweight `aiohttp` HTTP server exposing `/metrics` (Prometheus scrape target) and `/health` (liveness probe).

`errander/observability/tracking.py` is the write layer — it translates domain objects (`ActionResult`, error strings, LLM outcomes) into metric increments and histogram observations.

The goal: Grafana can scrape `/metrics` and build dashboards showing fleet health, LLM availability, and approval latency without any code changes in the agent itself.

---

## Key Concepts

### 1. CollectorRegistry — One Registry, All Metrics

`prometheus_client` has a default global registry (`REGISTRY` in the library). We use a **custom registry** instead:

```python
from prometheus_client import CollectorRegistry
REGISTRY = CollectorRegistry()
```

Why a custom registry?
- **Test isolation**: Tests can import `REGISTRY` and read metric values without contaminating the library's default global state.
- **Explicit ownership**: Everything in `REGISTRY` belongs to Errander-AI — no accidental inclusion of Python process metrics or other libraries.
- **Testability**: `generate_latest(REGISTRY)` only outputs our metrics, making response body assertions simple.

All metrics are registered at module import time with `registry=REGISTRY`:

```python
ACTIONS_TOTAL = Counter(
    "errander_actions_total",
    "Total maintenance actions executed",
    ["action_type", "status", "vm_id"],
    registry=REGISTRY,
)
```

### 2. Counter vs Histogram — When to Use Each

| Type | Use for | Example |
|---|---|---|
| Counter | Things that only go up (events, errors) | Actions executed, SSH errors |
| Histogram | Measured durations and sizes | Action duration, approval wait |
| Gauge | Things that go up AND down | (not used here — lock held time is a duration, better as histogram) |

Counters are labeled for slicing in Grafana:
```python
ACTIONS_TOTAL.labels(action_type="disk_cleanup", status="success", vm_id="vm-001").inc()
```

Histograms define custom buckets tuned to expected ranges. For action duration (seconds):
```python
buckets=(5, 15, 30, 60, 120, 300, 600)
```
This gives useful resolution for both fast (5s disk cleanup) and slow (600s patching) operations.

### 3. aiohttp Web Application for the HTTP Server

Instead of Flask or FastAPI, we use `aiohttp.web` — it's already a dependency (the Slack client uses `aiohttp`) and it's async-native with no extra overhead:

```python
app = web.Application()
app.router.add_get("/metrics", _metrics_handler)
app.router.add_get("/health", _health_handler)

runner = web.AppRunner(app, access_log=None)
await runner.setup()
site = web.TCPSite(runner, host="0.0.0.0", port=port)
await site.start()
```

`access_log=None` suppresses the per-request log lines (Prometheus scrapes every 15s — that's a lot of noise in structured logs).

`start_metrics_server()` returns the `AppRunner` so the caller can call `runner.cleanup()` on graceful shutdown.

### 4. /metrics Handler: Prometheus Text Format

```python
async def _metrics_handler(request: web.Request) -> web.Response:
    output = generate_latest(REGISTRY)
    return web.Response(
        body=output,
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
```

`generate_latest(REGISTRY)` returns bytes in the [Prometheus text exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/). `CONTENT_TYPE_LATEST` is the correct MIME type string from `prometheus_client`.

**Gotcha**: Do NOT pass both `content_type=` kwarg and `Content-Type` in the `headers` dict to `web.Response`. aiohttp raises `ValueError: passing both Content-Type header and content_type or charset params is forbidden`. Set it via `headers` only.

### 5. /health Handler: Simple Liveness

```python
async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})
```

No dependency checks. If the process is alive enough to serve HTTP, it's alive. Kubernetes/Docker health checks only need a 200 OK — they don't need to know if the LLM is reachable or if Slack is up (those are measured via separate metrics, not health gate failures).

### 6. Tracking Layer: Domain → Metrics

`tracking.py` translates `ActionResult` fields into metric labels:

```python
def record_action_result(result: ActionResult) -> None:
    ACTIONS_TOTAL.labels(
        action_type=result.action_type,
        status=result.status,
        vm_id=result.vm_id,
    ).inc()

    if result.completed_at is not None and result.started_at is not None:
        duration = (result.completed_at - result.started_at).total_seconds()
        ACTION_DURATION.labels(action_type=result.action_type).observe(duration)
```

Duration is only recorded when `completed_at` is set — pending or mid-execution results don't pollute the histogram with zeroes.

---

## Testing Prometheus Metrics

### Reading Counter Values in Tests

Prometheus counters accumulate across the test session (they're module-level singletons). Tests must read the value *before* and *after* and assert the delta:

```python
def _sample_value(metric, labels):
    for m in REGISTRY.collect():
        if m.name == metric._name:
            for sample in m.samples:
                if all(sample.labels.get(k) == v for k, v in labels.items()):
                    if not sample.name.endswith(("_sum", "_bucket", "_count")):
                        return sample.value
    return 0.0

before = _sample_value(ACTIONS_TOTAL, {"action_type": "disk_cleanup", ...})
record_action_result(result)
after = _sample_value(ACTIONS_TOTAL, {"action_type": "disk_cleanup", ...})
assert after == before + 1
```

This delta approach means tests can run in any order without asserting absolute values.

### Testing HTTP Handlers Directly

Handlers are plain async functions — no need to spin up a real server:

```python
async def test_returns_200():
    request = MagicMock()
    response = await _metrics_handler(request)
    assert response.status == 200
```

The `request` object is only passed to satisfy the handler signature; `_metrics_handler` doesn't read anything from it.

---

## Gotchas

### 1. aiohttp web.Response: content_type param vs Content-Type header

Do NOT pass both. Set Content-Type via `headers` only:
```python
web.Response(body=output, headers={"Content-Type": CONTENT_TYPE_LATEST})
```

### 2. Custom registry vs default registry

If you use `Counter("name", "help", ["labels"])` without `registry=REGISTRY`, it registers in the library's default global `prometheus_client.REGISTRY`. This causes:
- `generate_latest(REGISTRY)` misses those metrics
- Tests that check metric presence by name will fail
- Two processes in the same Python environment may collide

Always pass `registry=REGISTRY` explicitly.

### 3. Counters never reset

Prometheus counters are monotonically increasing. If you restart the agent, counters reset to 0 — this is expected and Prometheus handles it with `rate()` and `increase()` functions in PromQL. Don't try to persist counter values across restarts.

---

## Quiz Yourself

1. Why use a custom `CollectorRegistry` instead of the default one?
2. What is the difference between a Counter and a Histogram? When would you use a Gauge?
3. Why does `_metrics_handler` not read anything from the `request` object?
4. Why do tests read counter values before AND after the function call, instead of just asserting the final value?
5. What happens if you pass both `content_type=` and `headers={"Content-Type": ...}` to `web.Response`?
6. Why is `access_log=None` set on the AppRunner?
