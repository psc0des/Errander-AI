# LLM Provider Configuration

Errander-AI works with any OpenAI-compatible API. Set `ERRANDER_LLM_BASE_URL` and
`ERRANDER_LLM_MODEL` (or `llm.model` in `settings.yaml`) to point at your provider.

After configuring, verify with:

```bash
uv run python -m errander --check-llm
```

---

## Option A: Self-hosted vLLM (Qwen3-8B-AWQ)

**When to pick:** Privacy-first, no data leaves your VPN, lowest per-token cost at scale.
Requires a GPU VM (Tesla T4 or better, 16 GB VRAM).

```env
ERRANDER_LLM_BASE_URL=http://10.0.1.5:8000/v1
ERRANDER_LLM_MODEL=Qwen/Qwen3-8B-AWQ
# ERRANDER_LLM_API_KEY is not required for unauthenticated vLLM
```

```yaml
# settings.yaml
llm:
  model: "Qwen/Qwen3-8B-AWQ"
  temperature: 0.1
  timeout_seconds: 60   # T4 is slower than cloud APIs
  max_retries: 2
```

vLLM serve command (see `deploy/vllm/docker-compose.yml`):

```bash
vllm serve Qwen/Qwen3-8B-AWQ \
  --reasoning-parser deepseek_r1 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --max-model-len 8192 --gpu-memory-utilization 0.85
```

---

## Option B: Ollama (local, CPU/GPU)

**When to pick:** Privacy-first, no GPU required (runs on CPU), easiest local setup.
Slower than vLLM but zero infrastructure overhead.

```env
ERRANDER_LLM_BASE_URL=http://localhost:11434/v1
ERRANDER_LLM_MODEL=llama3.2
ERRANDER_LLM_API_KEY=ollama
```

```yaml
llm:
  model: "llama3.2"
  temperature: 0.1
  timeout_seconds: 120  # CPU inference is slow
  max_retries: 1
```

Start Ollama:

```bash
ollama serve
ollama pull llama3.2
```

---

## Option C: OpenAI

**When to pick:** Best ecosystem support, reliable latency, easiest to get started.
Data leaves your network — check your compliance requirements.

```env
ERRANDER_LLM_BASE_URL=https://api.openai.com/v1
ERRANDER_LLM_MODEL=gpt-4o-mini
ERRANDER_LLM_API_KEY=sk-...
```

```yaml
llm:
  model: "gpt-4o-mini"
  temperature: 0.1
  timeout_seconds: 30
  max_retries: 2
```

`gpt-4o-mini` is the recommended default — low cost, fast, sufficient for structured JSON.
Use `gpt-4o` for higher-quality failure analysis on complex incidents.

---

## Option D: Anthropic (Claude)

**When to pick:** Highest quality reasoning, strong structured output. Uses Anthropic's
OpenAI-compatible endpoint. Data leaves your network.

```env
ERRANDER_LLM_BASE_URL=https://api.anthropic.com/v1
ERRANDER_LLM_MODEL=claude-haiku-4-5-20251001
ERRANDER_LLM_API_KEY=sk-ant-...
```

```yaml
llm:
  model: "claude-haiku-4-5-20251001"
  temperature: 0.1
  timeout_seconds: 30
  max_retries: 2
```

Use `claude-sonnet-4-6` for higher quality at higher cost.

---

## Option E: Groq

**When to pick:** Fastest inference of any cloud provider (LPU hardware), very low cost.
Good choice when latency matters and you're comfortable with cloud APIs.

```env
ERRANDER_LLM_BASE_URL=https://api.groq.com/openai/v1
ERRANDER_LLM_MODEL=llama-3.3-70b-versatile
ERRANDER_LLM_API_KEY=gsk_...
```

```yaml
llm:
  model: "llama-3.3-70b-versatile"
  temperature: 0.1
  timeout_seconds: 15   # Groq is very fast
  max_retries: 2
```

---

## Verifying your configuration

```bash
# Check connectivity, list available models, measure latency
uv run python -m errander --check-llm

# Expected output:
# Checking LLM endpoint: http://10.0.1.5:8000/v1 (model: Qwen/Qwen3-8B-AWQ)
# --------------------------------------------------
#   Status   : OK
#   Models   : Qwen/Qwen3-8B-AWQ
#   Latency  : 2341.5 ms (test completion)
#   Response : 'OK'
# --------------------------------------------------
# LLM endpoint is healthy.
```

---

## Choosing a temperature

All providers use `temperature: 0.1` by default, which gives low-variance structured JSON.

| Value | Behavior | Use case |
|-------|----------|----------|
| 0.0 | Deterministic | Debugging, reproducibility |
| 0.1 | Low variance | **Default — structured JSON output** |
| 0.5 | Moderate variance | Creative reports |
| 1.0+ | High variance | Not recommended for this agent |
