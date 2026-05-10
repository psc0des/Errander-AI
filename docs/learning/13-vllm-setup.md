# 13 — vLLM Deployment + LLM Health Check

## What Was Built and Why

Errander-AI's LLM integration was already coded (`LLMClient`), but there was no way to actually get vLLM running without manually piecing together the correct Docker invocation, GPU flags, and serve arguments. Two things were added:

1. **`deploy/vllm/docker-compose.yml`** — a production-ready Docker Compose file that encodes the exact vLLM configuration for this project (model, reasoning flags, GPU utilization)
2. **`LLMClient.check_endpoint()`** + **`--check-llm` CLI flag** — verifies the vLLM endpoint is reachable, lists available models, and measures round-trip completion latency

---

## Key Concepts

### 1. Docker Compose GPU passthrough

Running a GPU container requires Docker to pass the physical GPU through to the container. The Compose `deploy.resources` block handles this:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

`count: all` passes all available GPUs. On a dedicated T4 VM this is always one GPU. `capabilities: [gpu]` tells Docker this is a GPU-capable device requirement, which triggers the NVIDIA Container Runtime.

**Prerequisite**: NVIDIA Container Toolkit must be installed on the host and Docker must be configured to use the `nvidia` runtime.

### 2. Model weights as a host volume

The model weights (~5GB for Qwen3-8B-AWQ) are stored on the host, not inside the container:

```yaml
volumes:
  - ${MODEL_CACHE_DIR:-/opt/vllm/model-cache}:/root/.cache/huggingface
```

**Why**: If the volume were inside the container, every `docker compose pull` (upgrade) would re-download 5GB. With a host volume, the weights survive image upgrades. The container reads them from the mounted path.

The `${MODEL_CACHE_DIR:-/opt/vllm/model-cache}` syntax is Docker Compose variable substitution with a default — reads from `.env` if set, falls back to the default path otherwise.

### 3. Healthcheck with long start period

The vLLM container takes ~2-3 minutes to load the model weights into VRAM on first start. Docker's healthcheck must account for this:

```yaml
healthcheck:
  test: ["CMD", "curl", "-sf", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 10
  start_period: 180s
```

`start_period: 180s` tells Docker not to count failures during the first 3 minutes as health failures. Without this, Docker would restart the container repeatedly during model load, creating an infinite restart loop.

### 4. The exact vLLM serve command

The serve command is not just `vllm serve <model>` — it requires several flags for Errander-AI's specific needs:

```yaml
command: >
  --model Qwen/Qwen3-8B-AWQ
  --enable-reasoning               # enables <think>...</think> blocks
  --reasoning-parser deepseek_r1   # parses the thinking output
  --enable-auto-tool-choice        # allows tool call format in responses
  --tool-call-parser hermes        # parses Hermes-format tool calls
  --max-model-len 8192             # context window — fits in 16GB VRAM
  --gpu-memory-utilization 0.85    # leaves ~2.4GB headroom
  --host 0.0.0.0                   # bind to all interfaces (within container)
  --port 8000
  --served-model-name qwen3-8b     # the ID the agent uses in API calls
```

`--enable-reasoning` is what enables thinking mode — the model wraps its reasoning in `<think>...</think>` tags which vLLM strips from the final response but can expose via the reasoning parser.

### 5. `check_endpoint()` — two-stage health check

A simple `/health` ping only tells you the HTTP server is up — not that the model is loaded and can generate tokens. `check_endpoint()` runs two checks:

```python
# Stage 1: list models (confirms model is loaded)
models = await self._client.models.list()
result["model_ids"] = [m.id for m in models.data]

# Stage 2: minimal completion with timing
t0 = time.monotonic()
resp = await self._client.chat.completions.create(
    model=_MODEL_ID,
    messages=[{"role": "user", "content": "/no_think Respond with exactly: OK"}],
    max_tokens=8,
    temperature=0.0,
)
result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
```

`/no_think` disables the reasoning chain — we want a fast "is it generating tokens?" check, not a full thinking-mode response. `max_tokens=8` keeps it minimal. `temperature=0.0` makes the output deterministic.

### 6. `--check-llm` exits before any agent setup

`--check-llm` runs **before** `load_settings()` and reads LLM env vars directly — this means it works even when an unrelated encrypted secret (e.g. `ERRANDER_UI_PASSWORD`) has a key mismatch that would crash `load_settings()`:

```python
# Early exits that need no settings at all
if args.check_llm:
    return await run_llm_check()  # reads env vars directly, exits here

settings = load_settings(...)  # only reached for full agent modes

if args.audit:
    return await run_audit_query(args, settings)  # exits here

# ... full agent setup only if neither flag was passed
```

This makes `--check-llm` a lightweight diagnostic tool — no side effects, no infrastructure started.

---

## Why Docker Compose for Production

The choice of Docker Compose over bare metal for the LLM VM:

| | Docker Compose | Bare metal (systemd) |
|---|---|---|
| Reproducibility | Exact same environment every time | Depends on OS state |
| Upgrades | `docker compose pull && up -d` | `pip install --upgrade vllm` (may break) |
| GPU setup | One toolkit install + `capabilities: [gpu]` | Manual CUDA driver management |
| Restart on crash | `restart: unless-stopped` | systemd `Restart=on-failure` |
| Overhead | Negligible for GPU workloads | None |

For a single dedicated T4 VM, Docker Compose is production-grade. The only reason to go bare metal would be to squeeze out the ~1% container overhead, which is irrelevant compared to model inference time.

---

## Quiz

1. What does `count: all` mean in the Docker Compose GPU device config?
2. Why are model weights stored in a host volume rather than inside the container?
3. What would happen without `start_period: 180s` in the healthcheck?
4. What does `--enable-reasoning` enable, and how does the agent use it?
5. Why does `check_endpoint()` run a test completion instead of just hitting `/health`?
6. Why is `--check-llm` handled before inventory loading in `async_main`?
